from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START

from langgraph.types import Command

from ...common import utils
from ...common import names as N
from ...common.states import BaseGraphState
from ...nodes.tools import (
    DigitalTwinTool,
    SaferRainTool,
    SaferBuildingsTool, 
    GeospatialOpsTool
)
from ...nodes.base import BaseToolHandlerNode, BaseToolInterruptNode



# DOC: SAFERPLACES API subgraph

digital_twin_tool = DigitalTwinTool()
safer_rain_tool = SaferRainTool()
saferbuildings_tool = SaferBuildingsTool()
geospatial_ops_tool = GeospatialOpsTool()
saferplaces_api_tools_dict = {
    digital_twin_tool.name: digital_twin_tool,
    safer_rain_tool.name: safer_rain_tool,
    saferbuildings_tool.name: saferbuildings_tool,
    geospatial_ops_tool.name: geospatial_ops_tool
}
saferplaces_api_tool_names = list(saferplaces_api_tools_dict.keys())
saferplaces_api_tools = list(saferplaces_api_tools_dict.values())

llm_with_saferplaces_api_tools = utils._base_llm.bind_tools(saferplaces_api_tools)


# DOC: Base tool handler: runs the tool, if tool interrupt go to interrupt node handler
saferplaces_api_tool_handler = BaseToolHandlerNode(
    state = BaseGraphState,
    tool_handler_node_name = N.SAFERPLACES_API_TOOL_HANDLER,
    tool_interrupt_node_name = N.SAFERPLACES_API_TOOL_INTERRUPT,
    tools = saferplaces_api_tools_dict,
    additional_ouput_state = { 'requested_agent': None, 'node_params': dict() }
)


# DOC: Base tool interrupt node: handle tool interrupt by type and go back to tool hndler with updatet state to rerun tool
saferplaces_api_tool_interrupt = BaseToolInterruptNode(
    state = BaseGraphState,
    tool_handler_node_name = N.SAFERPLACES_API_TOOL_HANDLER,
    tool_interrupt_node_name = N.SAFERPLACES_API_TOOL_INTERRUPT,
    tools = saferplaces_api_tools_dict,
    custom_tool_interupt_handlers = dict()     # DOC: use default 
)

# DOC: State
saferplaces_api_graph_builder = StateGraph(BaseGraphState)

# DOC: Nodes
saferplaces_api_graph_builder.add_node(N.SAFERPLACES_API_TOOL_HANDLER, saferplaces_api_tool_handler)
saferplaces_api_graph_builder.add_node(N.SAFERPLACES_API_TOOL_INTERRUPT, saferplaces_api_tool_interrupt)

# DOC: Edges
saferplaces_api_graph_builder.add_edge(START, N.SAFERPLACES_API_TOOL_HANDLER)

# DOC: Compile
saferplaces_api_subgraph = saferplaces_api_graph_builder.compile()
saferplaces_api_subgraph.name = N.SAFERPLACES_API_SUBGRAPH