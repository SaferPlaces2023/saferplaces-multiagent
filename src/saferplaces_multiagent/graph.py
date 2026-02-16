"""
Defining agent graph
"""

from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .common import names as N

from .common.states import BaseGraphState

from .nodes import (
    chatbot, chatbot_update_messages, fix_orphan_tool_calls
)
from .nodes.subgraphs import (
    saferplaces_api_subgraph,
    safercast_api_subgraph
)


# DOC: define state
graph_builder = StateGraph(BaseGraphState)


# graph_builder.




# DOC: define nodes
graph_builder.add_node(chatbot)
graph_builder.add_node(N.CHATBOT_UPDATE_MESSAGES, chatbot_update_messages)
graph_builder.add_node(N.FIX_ORPHAN_TOOL_CALLS, fix_orphan_tool_calls)

graph_builder.add_node(N.SAFERPLACES_API_SUBGRAPH, saferplaces_api_subgraph)

graph_builder.add_node(N.SAFERCAST_API_SUBGRAPH, safercast_api_subgraph)

# DOC: define edges

graph_builder.add_edge(START, N.CHATBOT)
graph_builder.add_edge(N.CHATBOT_UPDATE_MESSAGES, N.CHATBOT)
graph_builder.add_edge(N.FIX_ORPHAN_TOOL_CALLS, N.CHATBOT)

graph_builder.add_edge(N.SAFERPLACES_API_SUBGRAPH, N.CHATBOT)
graph_builder.add_edge(N.SAFERCAST_API_SUBGRAPH, N.CHATBOT)

# DOC: build graph
graph = graph_builder.compile(checkpointer = InMemorySaver())   # REF: when launch with `langgraph dev` command a message says it is not necessary ... 
graph.name = N.GRAPH