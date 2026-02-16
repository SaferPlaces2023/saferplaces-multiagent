from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START

from langgraph.types import Command

from ...common import utils
from ...common import names as N
from ...common.states import BaseGraphState
from ..tools import (
    DigitalTwinTool,
    SaferRainTool,
    SaferBuildingsTool, 
    GeospatialOpsTool
)



# DOC: SAFERPLACES API subgraph

digital_twin_tool = DigitalTwinTool()
safer_rain_tool = SaferRainTool()
saferbuildings_tool = SaferBuildingsTool()


saferplaces_api_tools_dict = {
    digital_twin_tool.name: digital_twin_tool,
    safer_rain_tool.name: safer_rain_tool,
    saferbuildings_tool.name: saferbuildings_tool,
}
saferplaces_api_tool_names = list(saferplaces_api_tools_dict.keys())
saferplaces_api_tools = list(saferplaces_api_tools_dict.values())

llm_with_saferplaces_api_tools = utils._base_llm.bind_tools(saferplaces_api_tools)

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