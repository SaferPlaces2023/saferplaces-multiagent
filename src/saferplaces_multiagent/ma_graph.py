from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .common.names import NN
from .common.states import BaseGraphState, MABaseGraphState
from .ma.chat_agent import ChatAgent
from .ma.supervisor_agent import SupervisorAgent, build_supervisor_subgraph


class GraphNodes:
    
    chat_agent = ChatAgent()
    supervisor_agent = SupervisorAgent()
    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)

# DOC: Initial chat node
graph_builder.add_node("chat_agent", ChatAgent())

# DOC: Supervisor|Orchestrator router node
def retrieval_agent(state: MABaseGraphState)->MABaseGraphState:
    return state
graph_builder.add_node("retrieval_agent", retrieval_agent)

supervisor_subgraph = build_supervisor_subgraph()

graph_builder.add_node("supervisor_subgraph", supervisor_subgraph)

# -----

graph_builder.add_edge(START, "chat_agent")
graph_builder.add_edge("chat_agent", "supervisor_subgraph")


def parent_route(state):
    return state.get("next_node", "retrieval_agent")
graph_builder.add_conditional_edges(
    "supervisor_subgraph",
    parent_route,
    {
        "retrieval_agent": "retrieval_agent",
        END: END,
    }
)

# DOC: Link to specialized agents
    
# graph_builder.add_edge("digital_twin_agent", "supervisor_subgraph")
# graph_builder.add_edge("simulations_agent", "supervisor_subgraph")
graph_builder.add_edge("retrieval_agent", END)
# graph_builder.add_edge("operational_agent", "supervisor_subgraph")


graph = graph_builder.compile(checkpointer=InMemorySaver())

graph.name = NN.GRAPH