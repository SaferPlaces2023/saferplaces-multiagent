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

from . import utils


# DOC: This is a basic state that will be used by all nodes in the graph. It ha one key: "messages" : list[AnyMessage]

ConfirmationState = Literal["accepted", "rejected", "pending"]

class AdditionalContext(TypedDict):
    source: str
    payload: Union[str, List[Any], Dict[str, Any]]

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
    supervisor_next_node: str
    # DOC: handling user-agent conversation flow 
    plan: Optional[List[dict]]
    plan_additional_context: Optional[List[AdditionalContext]]
    plan_confirmation: ConfirmationState
    replan_request: AnyMessage
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
    models_additional_context: Optional[List[AdditionalContext]]
    models_invocation_confirmation: ConfirmationState
    models_reinvocation_request: AnyMessage
    models_current_step: Optional[int]

    # DOC: on-demand layers agent state
    layers_request: AIMessage
    layers_invocation: AIMessage
    layers_response: List[Any]
    


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