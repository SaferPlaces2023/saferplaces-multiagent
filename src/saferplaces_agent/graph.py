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
    # demo_weather_subgraph,
    # create_project_subgraph,
    # flooding_rainfall_subgraph,
    saferplaces_api_subgraph,
    safercast_api_subgraph
)


# DOC: define state
graph_builder = StateGraph(BaseGraphState)


# DOC: define nodes

graph_builder.add_node(chatbot)
graph_builder.add_node(N.CHATBOT_UPDATE_MESSAGES, chatbot_update_messages)
graph_builder.add_node(N.FIX_ORPHAN_TOOL_CALLS, fix_orphan_tool_calls)

def state_updater(state: BaseGraphState):
    """
    Update the state with the new messages.
    """
    state_update = state.get("node_params", dict()).get(N.STATE_UPDATER, dict()).get("state_update", [])
    goto = state.get("node_params", dict()).get(N.STATE_UPDATER, dict()).get("goto", None)
    return Command(goto=goto, update=state_update)

# graph_builder.add_node(N.DEMO_SUBGRAPH, demo_weather_subgraph)

# graph_builder.add_node(N.CREATE_PROJECT_SUBGRAPH, create_project_subgraph)

# graph_builder.add_node(N.FLOODING_RAINFALL_SUBGRAPH, flooding_rainfall_subgraph)

graph_builder.add_node(N.SAFERPLACES_API_SUBGRAPH, saferplaces_api_subgraph)

graph_builder.add_node(N.SAFERCAST_API_SUBGRAPH, safercast_api_subgraph)

graph_builder.add_node(N.STATE_UPDATER, state_updater)


# DOC: define edges

graph_builder.add_edge(START, N.CHATBOT)
graph_builder.add_edge(N.CHATBOT_UPDATE_MESSAGES, N.CHATBOT)
graph_builder.add_edge(N.FIX_ORPHAN_TOOL_CALLS, N.CHATBOT)

# graph_builder.add_edge(N.DEMO_SUBGRAPH, N.CHATBOT)
# graph_builder.add_edge(N.CREATE_PROJECT_SUBGRAPH, N.CHATBOT)
# graph_builder.add_edge(N.FLOODING_RAINFALL_SUBGRAPH, N.CHATBOT)
graph_builder.add_edge(N.SAFERPLACES_API_SUBGRAPH, N.CHATBOT)
graph_builder.add_edge(N.SAFERCAST_API_SUBGRAPH, N.CHATBOT)

# DOC: build graph
graph = graph_builder.compile(checkpointer = InMemorySaver())   # REF: when launch with `langgraph dev` command a message says it is not necessary ... 
graph.name = N.GRAPH