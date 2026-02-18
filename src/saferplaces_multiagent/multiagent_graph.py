
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver


from .common.names import NN
from .common.states import MABaseGraphState
from .ma.names import NodeNames
from .ma.chat.request_parser import RequestParser
from .ma.chat.final_responder import FinalResponder
from .ma.orchestrator.supervisor import SupervisorAgent, SupervisorRouter, SupervisorPlannerConfirm
from .ma.specialized.safercast_agent import DataRetrieverAgent
from .ma.specialized.models_agent import ModelsAgent


def build_supervisor_subgraph():
    """Build the supervisor subgraph with planning and routing logic."""
    print(f"[Graph] Building supervisor subgraph...")
    supervisor_builder = StateGraph(MABaseGraphState)
    
    supervisor_agent = SupervisorAgent()
    supervisor_planner_confirm = SupervisorPlannerConfirm()
    supervisor_router = SupervisorRouter()
    
    supervisor_builder.add_node(supervisor_agent.name, supervisor_agent)
    supervisor_builder.add_node(supervisor_planner_confirm.name, supervisor_planner_confirm)
    supervisor_builder.add_node(supervisor_router.name, supervisor_router)
    
    
    supervisor_builder.add_edge(START, supervisor_agent.name)
    supervisor_builder.add_edge(supervisor_agent.name, supervisor_planner_confirm.name)
    # supervisor_builder.add_edge(supervisor_planner_confirm.name, supervisor_router.name)
    supervisor_builder.add_conditional_edges(
        supervisor_planner_confirm.name,
        lambda state: state.get('plan_confirmation') == 'rejected',
        {
            True: supervisor_agent.name,
            False: supervisor_router.name,
        }
    )
    
    return supervisor_builder.compile()


def build_multiagent_graph():
    """Build the main multi-agent graph with all nodes and edges."""
    print(f"[Graph] Building multiagent graph...")
    graph_builder = StateGraph(MABaseGraphState)
    
    # Initialize agents
    request_parser = RequestParser()
    final_responder = FinalResponder()
    retrieval_agent = DataRetrieverAgent()
    models_agent = ModelsAgent()
    
    # Add nodes using NodeNames constants
    graph_builder.add_node(NodeNames.REQUEST_PARSER, request_parser)
    graph_builder.add_node(NodeNames.SUPERVISOR_SUBGRAPH, build_supervisor_subgraph())
    graph_builder.add_node(NodeNames.RETRIEVER_AGENT, retrieval_agent)
    graph_builder.add_node(NodeNames.MODELS_AGENT, models_agent)
    graph_builder.add_node(NodeNames.FINAL_RESPONDER, final_responder)
    
    # Add edges
    graph_builder.add_edge(START, NodeNames.REQUEST_PARSER)
    graph_builder.add_edge(NodeNames.REQUEST_PARSER, NodeNames.SUPERVISOR_SUBGRAPH)
    
    # Conditional edges from supervisor
    graph_builder.add_conditional_edges(
        NodeNames.SUPERVISOR_SUBGRAPH,
        lambda state: state.get("supervisor_next_node", END),
        {
            NodeNames.RETRIEVER_AGENT: NodeNames.RETRIEVER_AGENT,
            NodeNames.MODELS_AGENT: NodeNames.MODELS_AGENT,
            NodeNames.FINAL_RESPONDER: NodeNames.FINAL_RESPONDER,
            END: END,
        }
    )
    
    # Link specialized agents back to supervisor
    graph_builder.add_edge(NodeNames.RETRIEVER_AGENT, NodeNames.SUPERVISOR_SUBGRAPH)
    graph_builder.add_edge(NodeNames.MODELS_AGENT, NodeNames.SUPERVISOR_SUBGRAPH)
    # Final edge
    graph_builder.add_edge(NodeNames.FINAL_RESPONDER, END)
    
    compiled_graph = graph_builder.compile(checkpointer=InMemorySaver())
    print(f"[Graph] ✓ Multiagent graph ready")
    return compiled_graph


# Build and export the graph
graph = build_multiagent_graph()
graph.name = NN.GRAPH
