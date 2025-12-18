import os
import json
import uuid
from textwrap import indent
import datetime

from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, AnyMessage
from langchain_core.load import load as lc_load

from ..graph import graph
from ..common import s3_utils, utils
from ..common import states as GraphStates
# from .chat_handler import ChatHandler

from .leafmap_interface import LeafmapInterface

# from IPython.display import display, Markdown, clear_output



class ConversationHandler:
    
    title = None
    subtitle = None
    events: list[AnyMessage | Interrupt] = []
    new_events: list[AnyMessage | Interrupt] = []
    
    def __init__(self, chat_id=None, title=None, subtitle=None):
        self.chat_id = chat_id
        self.title = title
        self.subtitle = subtitle
        
    def add_events(self, event: AnyMessage | Interrupt | list[AnyMessage | Interrupt]):
        if isinstance(event, list):
            self.events.extend(event)
            self.new_events.extend(event)
        else:
            self.events.append(event)
            self.new_events.append(event)
        

    @property
    def get_new_events(self):
        """Returns the new events and clears the list."""
        new_events = self.new_events.copy()
        self.new_events.clear()
        return new_events
    
    
    def chat2json(self, chat: list[AnyMessage | Interrupt] | None = None) -> list[dict]:
        """
        Convert a chat to a JSON string.
        """
    
        if chat is None:
            chat = self.events
    
        def human_message_to_dict(msg: HumanMessage) -> dict:
            return {
                "role": "user",
                "content": msg.content,
                "resume_interrupt": msg.resume_interrupt if hasattr(msg, 'resume_interrupt') else None,
            }
        
        def ai_message_to_dict(msg: AIMessage) -> dict:
            return {
                "role": "ai",
                "content": msg.content,
                "tool_calls": msg.tool_calls if msg.tool_calls else [],
            }
            
        def tool_message_to_dict(msg: ToolMessage) -> dict:
            return {
                "role": "tool",
                "content": msg.content,
                "name": msg.name,
                "id": msg.id,
                "tool_call_id": msg.tool_call_id
            }
            
        def interrupt_to_dict(msg: Interrupt) -> dict:
            return {
                "role": "interrupt",
                "content": msg.value['content'],
                "interrupt_type": msg.value['interrupt_type'],
                "state_updates": msg.value.get('state_updates', dict())
            }
            
        message_type_map = {
            HumanMessage: human_message_to_dict,
            AIMessage: ai_message_to_dict,
            ToolMessage: tool_message_to_dict,
            Interrupt: interrupt_to_dict
        }
        
        chat_dict = [
            message_type_map[type(msg)](msg)
            for msg in chat 
            if type(msg) in message_type_map
        ]
        
        return chat_dict


class GraphInterface:

    def __init__(
        self, 
        thread_id: str,
        user_id: str,
        project_id: str,
        map_handler: str | bool | None = None
    ):
        self.G: CompiledStateGraph = graph
        self.thread_id = thread_id
        self.user_id = user_id
        self.project_id = project_id

        self.interrupt = None

        self.config = { "configurable": { "thread_id": self.thread_id } }
        
        self.conversation_events = []
        self.conversation_handler = ConversationHandler(chat_id=self.thread_id, title=f"Chat {user_id}", subtitle=f"Thread {thread_id}")
        
        self.map_handler = None
        if map_handler is not None:
            if map_handler is True:
                self.map_handler = LeafmapInterface()
            elif isinstance(map_handler, str):
                self.map_handler = LeafmapInterface(provider=map_handler)
            else:
                raise ValueError(f"Invalid map_handler type: {type(map_handler)}. Expected str or bool.")
            
        
        s3_utils.setup_base_bucket(user_id=self.user_id, project_id=self.project_id)
        self.restore_state()
             
            
    @property
    def graph_state(self):
        """ graph_state - returns the graph state """
        return self.G.get_state(self.config).values
    
            
    def restore_state(self):
        
        def restore_layer_registry():
            lr_uri = f"{s3_utils._STATE_BUCKET_(dict(user_id=self.user_id, project_id=self.project_id))}/layer_registry.json"
            print(f"Restoring layer registry from {lr_uri} ...")
            lr_fp = s3_utils.s3_download(uri=lr_uri, fileout=os.path.join(os.getcwd(), f'{self.user_id}__{self.project_id}__layer_registry.json'))   # TODO: TMP DIR! + garbage collect
            if lr_fp is not None and os.path.exists(lr_fp):
                with open(lr_fp, 'r') as f:
                    layer_registry = json.load(f)
                os.remove(lr_fp)
                return layer_registry
            return []
        
        restored_layer_registry = restore_layer_registry()
        event_value = { 
            'messages': [ GraphStates.build_layer_registry_system_message(restored_layer_registry) ],
            'layer_registry': restored_layer_registry 
        }
        _ = list( self.G.stream(
            input = event_value,
            config = self.config, stream_mode = 'updates'
        ) )
        self.on_end_event(event_value)
        
        
    def get_state(self, key: str | list | None = None, fallback: Any = None) -> Any:
        state = self.graph_state
        if key is None:
            return state
        if isinstance(key, str):
            return state.get(key, fallback)
        if isinstance(key, list):
            return {k: state.get(k, fallback) for k in key}
        
    
    def set_state(self, state_updates: dict) -> dict:
        if state_updates is None:
            state_updates = dict()
            return self.get_state()
        
        current_state = self.get_state()
        state_updates = {k: v for k, v in state_updates.items()}
        # _ = list( self.G.stream(
        #     input = state_updates,
        #     config = self.config, stream_mode = 'updates'
        # ) )
        # self.G.update_state(
        #     self.config,
        #     values = state_updates,
        #     # as_node = 'chatbot' # !!!: this is a hack to update the state in the chatbot node
        # )
        
        system_messages = []
        if 'layer_registry' in state_updates:
            system_messages.append(GraphStates.build_layer_registry_system_message(state_updates.get('layer_registry', [])))
        if 'user_drawn_shapes' in state_updates:
            system_messages.append(GraphStates.build_user_drawn_shapes_system_message(state_updates.get('user_drawn_shapes', [])))
        if system_messages: #and self.interrupt is None:
            state_updates['messages'] = state_updates.get('messages', []) + system_messages
        
        _ = list( self.G.stream(
            input = state_updates,
            config = self.config, stream_mode = 'updates'
        ) )
        self.on_end_event(state_updates)
        return self.get_state()
        
        
    def register_layer(self, 
        src: str,
        title: str | None = None, 
        description: str | None = None,
        layer_type: Literal['vector', 'raster'] = None,
        metadata: dict | None = None,
    ):
        """Register a new layer in the Layer Registry."""
        layer_dict = {
            'title': title if title else utils.juststem(src),
            ** ({ 'description': description } if description else dict()),
            'src': src,
            'type': layer_type if layer_type else 'vector' if utils.justext(src) in ['geojson', 'gpkg', 'shp'] else 'raster',
            ** ({ 'metadata': metadata } if metadata else dict()),
        }
        event_value = { 
            'messages': [ GraphStates.build_layer_registry_system_message(self.graph_state.get('layer_registry', []) + [layer_dict]) ],
            'layer_registry': [layer_dict] 
        }
        _ = list( self.G.stream(
            input = event_value,
            config = self.config, stream_mode = 'updates'
        ) )
        self.on_end_event(event_value)


    def _event_value_is_interrupt(self, event_value):
        return type(event_value) is tuple and type(event_value[0]) is Interrupt
    
    def _event_value2interrupt(self, event_value):
        if self._event_value_is_interrupt(event_value):
            return event_value[0]
        return None
        
    def _interrupt2dict(self, interrupt):
        interrupt_data = interrupt.value
        agent_interrupt_message = { 'interrupt': interrupt_data }
        return agent_interrupt_message
    
    
    def update_events(self, new_events: AnyMessage | Interrupt | list[AnyMessage | Interrupt]):
        """Update the chat events with new events."""
        if isinstance(new_events, list):
            self.conversation_events.extend(new_events)
            self.conversation_handler.add_events(new_events)
        else:
            self.conversation_events.append(new_events)
            self.conversation_handler.add_events(new_events)
            
    def on_end_event(self, event_value):
            
        def update_layer_registry(event_value):
            if type(event_value) is dict and event_value.get('layer_registry'):
                layer_registry = self.get_state('layer_registry')
                lr_uri = f'{s3_utils._STATE_BUCKET_(dict(user_id=self.user_id, project_id=self.project_id))}/layer_registry.json'
                lr_fp = os.path.join(os.getcwd(), f'{self.user_id}__{self.project_id}__layer_registry.json')
                with open(lr_fp, 'w') as f:
                    json.dump(layer_registry, f, indent=4)
                _ = s3_utils.s3_upload(filename=lr_fp, uri=lr_uri, remove_src= True )
                
        def update_map(event_value):
            if self.map_handler and type(event_value) is dict and event_value.get('layer_registry'):
                is_map_updated = False
                for layer in event_value['layer_registry']:
                    is_layer_added = self.map_handler.add_layer(
                        src=layer['src'],
                        layer_type=layer['type'],
                        colormap_name=layer.get('metadata', {}).get('colormap_name', 'viridis'),
                        nodata=layer.get('metadata', {}).get('nodata', -9999),
                    )
                    is_map_updated = is_map_updated or is_layer_added
                
        update_layer_registry(event_value)
        update_map(event_value)
        

    def user_prompt(
        self,
        prompt: str,
        state_updates: dict = dict(),
    ):
        
        def prepare_system_messages():            
            system_messages = []
            system_messages.append(GraphStates.build_nowtime_system_message())
            if 'layer_registry' in state_updates and state_updates['layer_registry']:
                system_messages.append(GraphStates.build_layer_registry_system_message(state_updates.get('layer_registry', [])))
            if 'user_drawn_shapes' in state_updates and state_updates['user_drawn_shapes']:
                system_messages.append(GraphStates.build_user_drawn_shapes_system_message(state_updates.get('user_drawn_shapes', [])))        
            return system_messages
        
        def build_stream():
            stream_obj = dict()
            if self.interrupt is not None:
                print(f"RESUMING INTERRUPT: {self.interrupt}")
                self.update_events(HumanMessage(content=prompt, resume_interrupt={ 'interrupt_type': self.interrupt.value['interrupt_type'] }))
                self.interrupt = None
                stream_obj = Command(resume={'response': prompt})
            else:
                print(f"NEW PROMPT: {prompt}")
                self.update_events(HumanMessage(content=prompt))
                stream_obj = {
                    'messages': [
                        * prepare_system_messages(),
                        HumanMessage(content=prompt)
                    ],
                    'user_id': self.user_id,
                    'project_id': self.project_id,
                    'node_params': state_updates.get('node_params', dict()),
                    'node_history': state_updates.get('node_history', []),
                    'layer_registry': state_updates.get('layer_registry', []),
                    'user_drawn_shapes': state_updates.get('user_drawn_shapes', []),
                    'avaliable_tools': state_updates.get('avaliable_tools', self.get_state('avaliable_tools', [])),
                    'nowtime': datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat(),
                }
            return stream_obj
        
        def process_event_value(event_value):
            if 'messages' in event_value:
                event_value['message'] = event_value['messages'][-1].to_json()
                del event_value['messages']
                self.update_events(lc_load(event_value['message']))     # !!!: json-message to obj-message → LangChainBetaWarning: The function `load` is in beta. It is actively being worked on, so the API may change.
                
            elif self._event_value_is_interrupt(event_value):
                self.interrupt = self._event_value2interrupt(event_value)
                self.update_events(self.interrupt)
                
            self.on_end_event(event_value)

        def check_interrupt_update_state():
            if self.interrupt is not None and \
                isinstance(self.interrupt, Interrupt) and 'state_updates' in self.interrupt.value and \
                isinstance(self.interrupt.value['state_updates'], dict) and len(self.interrupt.value['state_updates']) > 0:
                self.set_state(self.interrupt.value['state_updates'])
                return
            
                     
        stream_prompt = build_stream()
        # yield self.conversation_handler.get_new_events  # ???: why is this here?

        for event in self.G.stream(
            input = stream_prompt,
            config = self.config,
            stream_mode = 'updates'
        ):
            for event_value in event.values():
                if event_value is not None:
                    process_event_value(event_value)    
                    yield self.conversation_handler.get_new_events
                    
        self.on_end_event(stream_prompt) # ???: maybe it should be called before G.stream()

        # check_interrupt_update_state()


    

class GraphRegistry:

    """Registry for the agent graph."""
    
    def __init__(self):
        self.graphs = dict()

    def register(self, thread_id: str, user_id: str, **gi_kwargs) -> GraphInterface:
        self.graphs[thread_id] = GraphInterface(thread_id, user_id, **gi_kwargs)
        return self.graphs[thread_id]

    def get(self, thread_id: str) -> GraphInterface:
        return self.graphs.get(thread_id, None)