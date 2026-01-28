from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START

from ...common import utils
from ...common import names as N
from ...common.states import BaseGraphState
from ...nodes.tools import (
    DPCRetrieverTool,
    ICON2IIngestorTool,
    ICON2IRetrieverTool,
    MeteoblueRetrieverTool
)
from ...nodes.base import BaseToolHandlerNode, BaseToolInterruptNode, BaseToolHandlerNodeCallback



# DOC: SAFERCAST API subgraph

dpc_retriever_tool = DPCRetrieverTool()
icon2i_ingestor_tool = ICON2IIngestorTool()
icon2i_retriever_tool = ICON2IRetrieverTool()
meteoblue_retriever_tool = MeteoblueRetrieverTool()
safercast_api_tools_dict = {
    dpc_retriever_tool.name: dpc_retriever_tool,
    icon2i_ingestor_tool.name: icon2i_ingestor_tool,
    icon2i_retriever_tool.name: icon2i_retriever_tool,
    meteoblue_retriever_tool.name: meteoblue_retriever_tool,
}
safercast_api_tool_names = list(safercast_api_tools_dict.keys())
safercast_api_tools = list(safercast_api_tools_dict.values())

llm_with_safercast_api_tools = utils._base_llm.bind_tools(safercast_api_tools)


# DOC: Base tool handler: runs the tool, if tool interrupt go to interrupt node handler
safercast_api_tool_handler = BaseToolHandlerNode(
    state = BaseGraphState,
    tool_handler_node_name = N.SAFERCAST_API_TOOL_HANDLER,
    tool_interrupt_node_name = N.SAFERCAST_API_TOOL_INTERRUPT,
    tools = safercast_api_tools_dict,
    additional_ouput_state = { 'requested_agent': None, 'node_params': dict() },
)


# DOC: Base tool interrupt node: handle tool interrupt by type and go back to tool hndler with updatet state to rerun tool
safercast_api_tool_interrupt = BaseToolInterruptNode(
    state = BaseGraphState,
    tool_handler_node_name = N.SAFERCAST_API_TOOL_HANDLER,
    tool_interrupt_node_name = N.SAFERCAST_API_TOOL_INTERRUPT,
    tools = safercast_api_tools_dict,
    custom_tool_interupt_handlers = dict()     # DOC: use default 
)

# DOC: State
safercast_api_graph_builder = StateGraph(BaseGraphState)

# DOC: Nodes
safercast_api_graph_builder.add_node(N.SAFERCAST_API_TOOL_HANDLER, safercast_api_tool_handler)
safercast_api_graph_builder.add_node(N.SAFERCAST_API_TOOL_INTERRUPT, safercast_api_tool_interrupt)

# DOC: Edges
safercast_api_graph_builder.add_edge(START, N.SAFERCAST_API_TOOL_HANDLER)

# DOC: Compile
safercast_api_subgraph = safercast_api_graph_builder.compile()
safercast_api_subgraph.name = N.SAFERCAST_API_SUBGRAPH