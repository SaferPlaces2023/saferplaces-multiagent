"""Define the state structures for the agent."""

from __future__ import annotations

import json
import datetime
from textwrap import indent

from typing_extensions import Annotated
from typing import Sequence, Any


from langchain_core.messages import SystemMessage

from langgraph.graph import MessagesState

from . import utils


# DOC: This is a basic state that will be used by all nodes in the graph. It ha one key: "messages" : list[AnyMessage]


class BaseGraphState(MessagesState):
    """Basic state"""
    nowtime: str = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat()
    node_history: Annotated[Sequence[str], utils.merge_sequences] = []
    node_params: Annotated[dict, utils.merge_dictionaries] = dict()
    layer_registry: Annotated[Sequence[dict], merge_layer_registry] = []
    avaliable_tools: list[str] | None = []
    confirm_tool_execution: bool = True
    
    user_id: str = None
    project_id: str = None    


def merge_layer_registry(left: Sequence[dict], right: Sequence[dict]) -> Sequence[dict]:
    return utils.merge_dict_sequences(left, right, unique_key='src')



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