"""Define the state structures for the agent."""

from __future__ import annotations

import json
import datetime
from textwrap import indent

from typing_extensions import Annotated, Literal, TypedDict
from typing import Literal, Sequence, TypedDict, List, Optional, Dict, Any, Union


from langchain_core.messages import SystemMessage, AnyMessage, AIMessage, ToolMessage
from langgraph.graph import add_messages, MessagesState
from langchain_core.messages import BaseMessage

from typing import Optional, Union, List, Dict, Any, Literal
from pydantic import BaseModel, Field

from dataclasses import dataclass, asdict

from . import utils
from .base_models import AdditionalContext, ConfirmationState


# DOC: This is a basic state that will be used by all nodes in the graph. It ha one key: "messages" : list[AnyMessage]





class MABaseGraphState(TypedDict):
    """Basic state"""
    # DOC: all messages
    messages: Annotated[list[AnyMessage], add_messages]

    # DOC: user session
    project_id: str = None  
    user_id: str = None
    
    # DOC: global state
    layer_registry: Annotated[Sequence[dict], merge_layer_registry] = []
    nowtime: str = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat()
    user_drawn_shapes: Annotated[Sequence[dict], merge_user_drawn_shapes] = []
    avaliable_tools: list[str] | None = []

    # DOC: multi-agent metadata
    parsed_request: Dict[str, Any]
    additional_context: AdditionalContext
    supervisor_next_node: str
    # DOC: handling user-agent conversation flow 
    plan: Optional[List[dict]]
    plan_confirmation: ConfirmationState
    replan_request: AnyMessage
    replan_type: Optional[str]  # "modify" | "reject" | None
    clarify_iteration_count: Optional[int]  # Counter for clarify loops
    plan_aborted: bool  # True when user aborted operation via SUPERVISOR_PLANNER_CONFIRM
    current_step: Optional[int]
    tool_results: Dict[str, Any]
    awaiting_user: bool

    # DOC: specialized retriever agent state
    retriever_invocation: AIMessage
    retriever_invocation_confirmation: ConfirmationState
    retriever_reinvocation_request: AnyMessage
    retriever_current_step: Optional[int]

    # DOC: specialized models agent state
    models_invocation: AIMessage
    models_invocation_confirmation: ConfirmationState
    models_reinvocation_request: AnyMessage
    models_current_step: Optional[int]

    # DOC: on-demand layers agent state
    layers_request: AIMessage
    layers_invocation: AIMessage
    layers_response: List[Any]


# ============================================================================
# State Manager
# ============================================================================

class StateManager:
    """Manages state lifecycle and cleanup across agent cycles."""

    @staticmethod
    def initialize_new_cycle(state: MABaseGraphState) -> None:
        """
        Initialize state for a NEW user request cycle.
        Called at the beginning of REQUEST_PARSER.
        
        Clears:
        - parsed_request (previous)
        - plan, current_step, plan_confirmation, replan_request (previous)
        - All specialized agent state (retriever/models)
        - tool_results
        - layers_request (temporary request state)
        """
        # Clear planning cycle state
        state['parsed_request'] = None
        state['plan'] = None
        state['current_step'] = None
        state['plan_confirmation'] = None
        state['replan_request'] = None
        state['replan_type'] = None
        state['clarify_iteration_count'] = 0
        state['plan_aborted'] = False
        state['awaiting_user'] = False
        
        # Clear previous tool results
        state['tool_results'] = {}
        
        # Reset additional context for new cycle
        if 'additional_context' not in state:
            state['additional_context'] = {}
        if 'relevant_layers' not in state['additional_context']:
            state['additional_context']['relevant_layers'] = {}
        state['additional_context']['relevant_layers']['is_dirty'] = True  # Will refresh in first ROUTER call
        
        # Clear specialized agent state
        StateManager._clear_specialized_agent_state(state, 'retriever')
        StateManager._clear_specialized_agent_state(state, 'models')
        
        # Clear layers agent temporary state
        state['layers_request'] = None
        state['layers_invocation'] = None
        state['layers_response'] = []

    @staticmethod
    def initialize_specialized_agent_cycle(
        state: MABaseGraphState, 
        agent_type: str
    ) -> None:
        """
        Initialize state for a SPECIALIZED AGENT cycle (retriever or models).
        Called before invoking the agent.
        
        Args:
            agent_type: 'retriever' or 'models'
        """
        prefix = agent_type
        
        # Clear previous invocation state
        state[f'{prefix}_invocation'] = None
        state[f'{prefix}_current_step'] = 0
        state[f'{prefix}_invocation_confirmation'] = None
        state[f'{prefix}_reinvocation_request'] = None

    @staticmethod
    def mark_agent_step_complete(
        state: MABaseGraphState,
        agent_type: str
    ) -> None:
        """
        Mark completion of a tool execution step in specialized agent.
        Increments current_step counter.
        
        Args:
            agent_type: 'retriever' or 'models'
        """
        prefix = agent_type
        current = state.get(f'{prefix}_current_step', 0)
        state[f'{prefix}_current_step'] = current + 1

    @staticmethod
    def cleanup_on_final_response(state: MABaseGraphState) -> None:
        """
        Cleanup state at end of request cycle (FINAL_RESPONDER).
        
        Keeps:
        - layer_registry (persistent across requests)
        - user_drawn_shapes (persistent across requests)
        - user_id, project_id (session info)
        
        Clears:
        - Temporary request state (parsed_request, plan, tool_results)
        - Specialized agent state
        - Layers request state
        """
        # Clear planning cycle
        state['parsed_request'] = None
        state['plan'] = None
        state['current_step'] = None
        state['plan_confirmation'] = None
        state['replan_request'] = None
        state['replan_type'] = None
        state['clarify_iteration_count'] = 0
        state['plan_aborted'] = False
        
        # Clear tool results (snapshot taken in final responder)
        state['tool_results'] = {}
        
        # Clear specialized agent state
        StateManager._clear_specialized_agent_state(state, 'retriever')
        StateManager._clear_specialized_agent_state(state, 'models')
        
        # Clear layers agent temporary state
        state['layers_request'] = None
        state['layers_invocation'] = None
        state['layers_response'] = []
        
        # Reset additional context dirty flag
        if 'additional_context' in state and 'relevant_layers' in state['additional_context']:
            state['additional_context']['relevant_layers']['is_dirty'] = False

    @staticmethod
    def _clear_specialized_agent_state(state: MABaseGraphState, agent_type: str) -> None:
        """Clear all state for a specialized agent."""
        prefix = agent_type
        state[f'{prefix}_invocation'] = None
        state[f'{prefix}_current_step'] = 0
        state[f'{prefix}_invocation_confirmation'] = None
        state[f'{prefix}_reinvocation_request'] = None

    @staticmethod
    def is_plan_complete(state: MABaseGraphState) -> bool:
        """Check if all plan steps have been executed."""
        plan = state.get('plan')
        current_step = state.get('current_step')
        
        if not plan or current_step is None:
            return True
        
        return current_step >= len(plan)


class BaseGraphState():
    """Basic state"""
    messages: Annotated[list[AnyMessage], add_messages]
    
    nowtime: str = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat()
    node_history: Annotated[Sequence[str], utils.merge_sequences] = []
    node_params: Annotated[dict, utils.merge_dictionaries] = dict()
    layer_registry: Annotated[Sequence[dict], merge_layer_registry] = []
    user_drawn_shapes: Annotated[Sequence[dict], merge_user_drawn_shapes] = []
    avaliable_tools: list[str] | None = []
    confirm_tool_execution: bool = True
    
    user_id: str = None
    project_id: str = None    


def merge_layer_registry(left: Sequence[dict], right: Sequence[dict]) -> Sequence[dict]:
    return utils.merge_dict_sequences(left, right, unique_key='src', method='update')

def merge_user_drawn_shapes(left: Sequence[dict], right: Sequence[dict]) -> Sequence[dict]:
    return utils.merge_dict_sequences(left, right, unique_key='collection_id', method='overwrite')



def src_layer_exists(graph_state: BaseGraphState, layer_src: str) -> bool:
    """Check if the layer exists in the graph state."""
    return any(layer.get('src') == layer_src for layer in graph_state.get('layer_registry', []))

def new_layer_title(graph_state: BaseGraphState, base_title: str) -> str:
    layers = graph_state.get('layer_registry', [])
    base_title_layers = [layer['title'] for layer in layers if layer.get('title', '').startswith(base_title)]
    # Find the highest index in existing titles
    indices = [int(lt.split()[-1]) for lt in base_title_layers]
    if len(indices) == 0:
        return f"{base_title} {str(1).zfill(3)}"
    max_index = max(indices)
    return f"{base_title} {str(max_index + 1).zfill(3)}"



def build_nowtime_system_message():
    """
    Generate a system message with the current time in ISO8601 UTC0 format.
    
    Returns:
        dict: A system message with the current time and timezone.
    """
    nowtime = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat()
    lines = []
    lines.append("[CONTEXT]")
    lines.append(f"current_time: {nowtime}")
    lines.append("timezone: UTC0")
    lines.append("\nInstructions:")
    lines.append("- Resolve any relative time expressions (e.g., today, yesterday, next N hours) using `current_time`.")
    lines.append("- If a year is missing, assume the year from `current_time`.")
    lines.append("- Always output absolute timestamps in ISO8601 UTC0 format without timezone.")
    lines.append("[/CONTEXT]")
    
    return SystemMessage(content="\n".join(lines))



def build_layer_registry_system_message(layer_registry: list) -> SystemMessage:
    """
    Generate a system message dynamically from a list of layer dictionaries.
    
    Args:
        layer_registry (list[dict]): List of layers where each layer has at least:
            - title (str)
            - type (str) -> "raster" or "vector"
            - src (str)
            - description (optional)
            - metadata (optional dict)
            
    Returns:
        str: A formatted system message ready to be injected before the user prompt.
    """

    if not layer_registry:
        return {
            'role': 'system',
            'content': "No layers available in the registry."
        }

    lines = []
    lines.append("[LAYER REGISTRY]")
    # INFO: [CONTEXT ONLY — DO NOT ACT] could enforce the agent to not run any tool calls that are not explicitly requested by the user.
    # lines.append("[CONTEXT ONLY — DO NOT ACT]")
    # lines.append("This message lists available geospatial layers for reference.")
    # lines.append("It is **read-only context** and **NOT** an instruction to run any tool.")
    # lines.append("- Do **NOT** invoke tools, create new layers, or fetch data based on this message alone.")
    # lines.append("- Take actions **only** if the user's **latest message** explicitly asks for them.")
    # # lines.append("- Do **NOT** initialize DigitalTwinTool (or similar) unless the user asks to build/create/generate a digital twin.")
    # lines.append("- If uncertain, ask a brief clarification.")
    # lines.append("[/CONTEXT ONLY — DO NOT ACT]\n")
    lines.append("The following geospatial layers are currently available in the project.")
    lines.append("Each layer has a `title` that should be referenced in conversations or tool calls "
                "when you need to use it. "
                "If the user refers to an existing dataset, check this registry to see if the dataset "
                "already exists before creating new data.\n")
    lines.append("Layers:")
    for idx, layer in enumerate(layer_registry, start=1):
        lines.append(f"{idx}.")
        lines.append(f"  - title: \"{layer.get('title', utils.juststem(layer['src']))}\"")
        lines.append(f"  - type: {layer['type']}")
        if 'description' in layer and layer['description']:
            lines.append(f"  - description: {layer['description']}")
        lines.append(f"  - src: {layer['src']}")

        # Metadata, if present
        if 'metadata' in layer and layer['metadata']:
            lines.append("  - metadata:")
            # Pretty print nested metadata with indentation
            meta_json = json.dumps(layer['metadata'], indent=4)
            lines.append(indent(meta_json, prefix="      "))

    lines.append("\nInstructions:")
    lines.append("- When a user request can be satisfied by using one of these layers, prefer re-using the layer instead of creating a new one.")
    lines.append("- Always refer to the `title` when mentioning or selecting a layer in your tool arguments.")
    lines.append("- If the type is 'vector', assume it contains geographic features like polygons, lines, or points.")
    lines.append("- If the type is 'raster', assume it contains gridded geospatial data.")
    lines.append("[/LAYER REGISTRY]")
    
    return SystemMessage(content="\n".join(lines))


def build_user_drawn_shapes_system_message(user_drawn_shapes: list) -> SystemMessage:
    """
    Generate a system message dynamically from a list of user-drawn shapes.
    
    Args:
        user_drawn_shapes (list[dict]): List of user-drawn shapes where each shape has at least:
            - collection_id (str): Unique identifier for the shape
            - type (str): Type of the shape (e.g., "polygon", "line", "point")
            - geometry (dict): Geometry data of the shape
            - metadata (optional dict): Additional metadata of the shape
    Returns:
        SystemMessage: A formatted system message ready to be injected before the user prompt.
    """

    if not user_drawn_shapes:
        return SystemMessage(content="No user-drawn shapes available in the registry.")

    lines = []
    lines.append("[USER DRAWN SHAPES]")
    lines.append("The following user-drawn shapes are currently available in the project.")
    lines.append("Each shape has a `collection_id` that should be referenced in conversations or tool calls "
                "when you need to use it.\n")
    lines.append("Shapes:")
    for idx, shape in enumerate(user_drawn_shapes, start=1):
        lines.append(f"{idx}.")
        lines.append(f"  - collection_id: {shape['collection_id']}")
        lines.append(f"  - type: {shape['metadata']['feature_type']}")
        lines.append(f"  - features:")
        lines.append(f"{indent(json.dumps(shape['features'], indent=4), prefix='    ')}")
        if 'metadata' in shape and shape['metadata']:
            lines.append("  - metadata:")
            # Pretty print nested properties with indentation
            props_json = json.dumps(shape['metadata'], indent=4)
            lines.append(indent(props_json, prefix="      "))

    lines.append("\nInstructions:")
    lines.append("- When a user request can be satisfied by using one of these shapes, prefer re-using the shape instead of creating a new one.")
    lines.append("- Always refer to the `collection_id` when mentioning or selecting a shape in your tool arguments.")
    lines.append("[/USER DRAWN SHAPES]")
    
    return SystemMessage(content="\n".join(lines))