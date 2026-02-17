
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .common.names import NN
from .common.states import MABaseGraphState
from .ma.chat.request_parser import RequestParser
from .ma.chat.final_responder import FinalResponder
from .ma.orchestrator.supervisor import SupervisorAgent, SupervisorRouter
from .ma.specialized.safercast_agent import DataRetrieverAgent


def build_supervisor_subgraph():
    """Build the supervisor subgraph with planning and routing logic."""
    supervisor_builder = StateGraph(MABaseGraphState)
    
    supervisor_builder.add_node("supervisor_agent", SupervisorAgent())
    supervisor_builder.add_node("supervisor_router", SupervisorRouter())
    
    supervisor_builder.add_edge(START, "supervisor_agent")
    supervisor_builder.add_edge("supervisor_agent", "supervisor_router")
    
    return supervisor_builder.compile()


def build_multiagent_graph():
    """Build the main multi-agent graph with all nodes and edges."""
    graph_builder = StateGraph(MABaseGraphState)
    
    # Add nodes
    graph_builder.add_node("chat_agent", RequestParser())
    graph_builder.add_node("supervisor_subgraph", build_supervisor_subgraph())
    graph_builder.add_node("retrieval_agent", DataRetrieverAgent())
    graph_builder.add_node("chat_final", FinalResponder())
    
    # Add edges
    graph_builder.add_edge(START, "chat_agent")
    graph_builder.add_edge("chat_agent", "supervisor_subgraph")
    
    # Conditional edges from supervisor
    graph_builder.add_conditional_edges(
        "supervisor_subgraph",
        lambda state: state.get("supervisor_next_node", END),
        {
            "retrieval_agent": "retrieval_agent",
            "chat_final": "chat_final",
            END: END,
        }
    )
    
    # Link specialized agents back to supervisor
    graph_builder.add_edge("retrieval_agent", "supervisor_subgraph")
    
    # Final edge
    graph_builder.add_edge("chat_final", END)
    
    return graph_builder.compile(checkpointer=InMemorySaver())


# Build and export the graph
graph = build_multiagent_graph()
graph.name = NN.GRAPH
