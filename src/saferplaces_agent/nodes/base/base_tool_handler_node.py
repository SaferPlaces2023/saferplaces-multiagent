
import types
from typing_extensions import Literal

from langgraph.graph import END
from langgraph.types import Command

from . import BaseToolInterrupt
from ...common import names as N


# DOC: base_tool_handler is a function that creates a tool handler function for a specific AgentTool.

class BaseToolHandlerNodeCallback():
    def __init__(self, callback = None, callback_args: dict = dict()):
        self.callback = callback
        self.callback_args = callback_args if callback_args is not None else dict()
        
    def __call__(self, **kwargs):
        """Call the callback function with the provided arguments."""
        if self.callback is not None:
            return self.callback(**(self.callback_args | kwargs))
        else:
            return
        

class BaseToolHandlerNode:
    
    # DOC: This is a template function that will be used to create the tool handler function.
    
    def __new__(
        cls,
        state,
        tool_handler_node_name: str,
        tool_interrupt_node_name: str,
        tools: dict,
        additional_ouput_state: dict = dict(),
        exit_nodes: list[str] = [],
        on_handle_end_callback = None
    ):
        instance = super().__new__(cls) 
        instance.__init__(
            state,
            tool_handler_node_name,
            tool_interrupt_node_name,
            tools,
            additional_ouput_state,
            exit_nodes,
            on_handle_end_callback
        )
        return instance.setup()
        
    
    def __init__( 
            self,
            state,
            tool_handler_node_name: str,
            tool_interrupt_node_name: str,
            tools: dict,
            additional_ouput_state: dict = dict(),
            exit_nodes: list[str] = [],
            on_handle_end_callback = None
    ):
        self.state = state
        self.state_type = type(state)
        self.tool_handler_node_name = tool_handler_node_name
        self.tool_interrupt_node_name = tool_interrupt_node_name
        self.tools = tools
        self.additional_ouput_state = additional_ouput_state
        self.exit_nodes = exit_nodes
        
        self.on_handle_end_callback = self.default_on_handle_end_callback if on_handle_end_callback is None else on_handle_end_callback
    
    
    def default_on_handle_end_callback(self, **kwargs):
        return dict()
        
    def setup(self):
        
        # DOC: This is a template function that will be used to create the tool handler function node.
        def tool_handler_template(state):
            
            tool_message = state["messages"][-1]
            tool_call = tool_message.tool_calls[-1]
            
            result = None
            try:
                tool = self.tools[tool_call['name']]
                tool.graph_state = state
                result = tool.invoke(tool_call['args'])
            except BaseToolInterrupt as tool_interrupt:                
                update_state = {}
                update_state['node_params'] = { 
                    self.tool_interrupt_node_name: {
                        'tool_message': tool_message,
                        'tool_interrupt': tool_interrupt.as_dict,
                        'tool_handler_node': self.tool_handler_node_name,    # INFO: Where to return interrupt "response" data
                    }
                }
                update_state.update(tool_interrupt.state_updates)
                return Command(goto=self.tool_interrupt_node_name, update = update_state)     
            
            tool_response_message = {
                "role": "tool",
                "name": tool_call['name'], 
                "content": result,
                "tool_call_id": tool_call['id'],
            }
            
            tool_result_updates = result.get('updates', dict()) if isinstance(result, dict) else dict()
            
            callback_result = self.on_handle_end_callback(**{'tool_output': result})  # DOC: Call the on_handle_end function if provided
            
            additional_updates = self.additional_ouput_state | tool_result_updates | callback_result.get('update', dict())   # TODO: correct with 'updates'
            additional_updates_messages = additional_updates.pop('messages', [])

            next_node = callback_result.get('next_node', None)
            
            if next_node is not None:
                
                print(f'\n\n Next node: {next_node} \n\n')
                
                return Command(
                    goto=next_node, 
                    update={
                        "messages": [tool_response_message] + additional_updates_messages,
                        **additional_updates
                    }
                )
            else:
                return {
                    "messages": [tool_response_message] + additional_updates_messages, 
                    **additional_updates
                }
                
                

        # DOC: Creating the tool handler function using the template function.
        tool_handler = types.FunctionType(
            tool_handler_template.__code__,
            globals(),
            name = self.tool_handler_node_name,
            argdefs = tool_handler_template.__defaults__,
            closure = tool_handler_template.__closure__
        )
        
        tool_handler.__annotations__ = {
            'state': type(self.state),
            'return': Command[Literal[END, self.tool_interrupt_node_name, *self.exit_nodes]]
        }
        
        return tool_handler