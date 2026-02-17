from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from saferplaces_multiagent.ma.safercast_agent import DataRetrieverAgent

from .common.names import NN
from .common.states import BaseGraphState, MABaseGraphState
from .ma.chat_agent import ChatAgent, FinalChatAgent
from .ma.supervisor_agent import SupervisorAgent, build_supervisor_subgraph


class GraphNodes:
    
    chat_agent = ChatAgent()
    supervisor_agent = SupervisorAgent()
    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)

# DOC: Initial chat node
graph_builder.add_node("chat_agent", ChatAgent())

# DOC: Supervisor|Orchestrator router node
supervisor_subgraph = build_supervisor_subgraph()
graph_builder.add_node("supervisor_subgraph", supervisor_subgraph)
graph_builder.add_node("retrieval_agent", DataRetrieverAgent())

# DOC: Final chat node
graph_builder.add_node("chat_final", FinalChatAgent())

# -----

graph_builder.add_edge(START, "chat_agent")
graph_builder.add_edge("chat_agent", "supervisor_subgraph")

graph_builder.add_conditional_edges(
    "supervisor_subgraph",
    lambda state: state.get("supervisor_next_node", END),
    {
        "retrieval_agent": "retrieval_agent",
        "chat_final": "chat_final",
        END: END,
    }
)
# DOC: Link to specialized agents
# graph_builder.add_edge("digital_twin_agent", "supervisor_subgraph")
# graph_builder.add_edge("simulations_agent", "supervisor_subgraph")
graph_builder.add_edge("retrieval_agent", "supervisor_subgraph")
# graph_builder.add_edge("operational_agent", "supervisor_subgraph")


graph_builder.add_edge("chat_final", END)


graph = graph_builder.compile(checkpointer=InMemorySaver())
graph.name = NN.GRAPH