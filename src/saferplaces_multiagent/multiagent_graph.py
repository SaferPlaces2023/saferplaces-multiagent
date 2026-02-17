
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .common.names import NN
from .common.states import MABaseGraphState
from .ma.names import NodeNames
from .ma.chat.request_parser import RequestParser
from .ma.chat.final_responder import FinalResponder
from .ma.orchestrator.supervisor import SupervisorAgent, SupervisorRouter
from .ma.specialized.safercast_agent import DataRetrieverAgent


def build_supervisor_subgraph():
    """Build the supervisor subgraph with planning and routing logic."""
    supervisor_builder = StateGraph(MABaseGraphState)
    
    supervisor_agent = SupervisorAgent()
    supervisor_router = SupervisorRouter()
    
    supervisor_builder.add_node(supervisor_agent.name, supervisor_agent)
    supervisor_builder.add_node(supervisor_router.name, supervisor_router)
    
    supervisor_builder.add_edge(START, supervisor_agent.name)
    supervisor_builder.add_edge(supervisor_agent.name, supervisor_router.name)
    
    return supervisor_builder.compile()


def build_multiagent_graph():
    """Build the main multi-agent graph with all nodes and edges."""
    graph_builder = StateGraph(MABaseGraphState)
    
    # Initialize agents
    request_parser = RequestParser()
    final_responder = FinalResponder()
    retrieval_agent = DataRetrieverAgent()
    
    # Add nodes using NodeNames constants
    graph_builder.add_node(NodeNames.REQUEST_PARSER, request_parser)
    graph_builder.add_node(NodeNames.SUPERVISOR_SUBGRAPH, build_supervisor_subgraph())
    graph_builder.add_node(NodeNames.RETRIEVAL_AGENT, retrieval_agent)
    graph_builder.add_node(NodeNames.FINAL_RESPONDER, final_responder)
    
    # Add edges
    graph_builder.add_edge(START, NodeNames.REQUEST_PARSER)
    graph_builder.add_edge(NodeNames.REQUEST_PARSER, NodeNames.SUPERVISOR_SUBGRAPH)
    
    # Conditional edges from supervisor
    graph_builder.add_conditional_edges(
        NodeNames.SUPERVISOR_SUBGRAPH,
        lambda state: state.get("supervisor_next_node", END),
        {
            NodeNames.RETRIEVAL_AGENT: NodeNames.RETRIEVAL_AGENT,
            NodeNames.FINAL_RESPONDER: NodeNames.FINAL_RESPONDER,
            END: END,
        }
    )
    
    # Link specialized agents back to supervisor
    graph_builder.add_edge(NodeNames.RETRIEVAL_AGENT, NodeNames.SUPERVISOR_SUBGRAPH)
    
    # Final edge
    graph_builder.add_edge(NodeNames.FINAL_RESPONDER, END)
    
    return graph_builder.compile(checkpointer=InMemorySaver())


# Build and export the graph
graph = build_multiagent_graph()
graph.name = NN.GRAPH
