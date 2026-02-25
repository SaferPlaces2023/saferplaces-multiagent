
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver


from .common.names import NN
from .common.states import MABaseGraphState
from .ma.names import NodeNames
from .ma.chat.request_parser import RequestParser
from .ma.chat.final_responder import FinalResponder
from .ma.orchestrator.supervisor import SupervisorAgent, SupervisorRouter, SupervisorPlannerConfirm
from .ma.specialized.safercast_agent import DataRetrieverAgent, DataRetrieverInvocationConfirm, DataRetrieverExecutor
from .ma.specialized.models_agent import ModelsAgent, ModelsExecutor, ModelsInvocationConfirm


def build_supervisor_subgraph():
    """Build the supervisor subgraph with planning and routing logic."""
    print(f"[Graph] Building supervisor subgraph...")
    supervisor_builder = StateGraph(MABaseGraphState)
    
    supervisor_agent = SupervisorAgent()
    supervisor_planner_confirm = SupervisorPlannerConfirm(enabled=True)
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


def build_specialized_retriever_subgraph():
    """Build the specialized retriever subgraph."""
    print(f"[Graph] Building specialized retriever subgraph...")
    retriever_builder = StateGraph(MABaseGraphState)
    
    retriever_agent = DataRetrieverAgent()
    retriever_invocation_confirm = DataRetrieverInvocationConfirm(enabled=True)
    retriever_executor = DataRetrieverExecutor()

    retriever_builder.add_node(retriever_agent.name, retriever_agent)
    retriever_builder.add_node(retriever_invocation_confirm.name, retriever_invocation_confirm)
    retriever_builder.add_node(retriever_executor.name, retriever_executor)

    retriever_builder.add_edge(START, retriever_agent.name)
    retriever_builder.add_edge(retriever_agent.name, retriever_invocation_confirm.name)
    # retriever_builder.add_edge(retriever_invocation_confirm.name, retriever_executor.name)
    retriever_builder.add_conditional_edges(
        retriever_invocation_confirm.name,
        lambda state: state.get('retriever_invocation_confirmation') == 'rejected',
        {
            True: retriever_agent.name,
            False: retriever_executor.name,
        }
    )
    
    return retriever_builder.compile()


def build_specialized_models_subgraph():
    """Build the specialized models subgraph."""
    print(f"[Graph] Building specialized models subgraph...")
    models_builder = StateGraph(MABaseGraphState)
    
    models_agent = ModelsAgent()
    models_invocation_confirm = ModelsInvocationConfirm(enabled=True)
    models_executor = ModelsExecutor()

    models_builder.add_node(models_agent.name, models_agent)
    models_builder.add_node(models_invocation_confirm.name, models_invocation_confirm)
    models_builder.add_node(models_executor.name, models_executor)

    models_builder.add_edge(START, models_agent.name)
    models_builder.add_edge(models_agent.name, models_invocation_confirm.name)
    # models_builder.add_edge(models_invocation_confirm.name, models_executor.name)
    models_builder.add_conditional_edges(
        models_invocation_confirm.name,
        lambda state: state.get('models_invocation_confirmation') == 'rejected',
        {
            True: models_agent.name,
            False: models_executor.name,
        }
    )
    
    return models_builder.compile()


def build_multiagent_graph():
    """Build the main multi-agent graph with all nodes and edges."""
    print(f"[Graph] Building multiagent graph...")
    graph_builder = StateGraph(MABaseGraphState)
    
    # Initialize agents
    request_parser = RequestParser()
    
    supervisor_subgraph = build_supervisor_subgraph()
    retriever_subgraph = build_specialized_retriever_subgraph()
    models_subgraph = build_specialized_models_subgraph()
    
    final_responder = FinalResponder()
    
    # Add nodes using NodeNames constants
    graph_builder.add_node(NodeNames.REQUEST_PARSER, request_parser)
    graph_builder.add_node(NodeNames.SUPERVISOR_SUBGRAPH, supervisor_subgraph)
    graph_builder.add_node(NodeNames.RETRIEVER_SUBGRAPH, retriever_subgraph)
    graph_builder.add_node(NodeNames.MODELS_SUBGRAPH, models_subgraph)
    graph_builder.add_node(NodeNames.FINAL_RESPONDER, final_responder)
    
    # Add edges
    graph_builder.add_edge(START, NodeNames.REQUEST_PARSER)
    graph_builder.add_edge(NodeNames.REQUEST_PARSER, NodeNames.SUPERVISOR_SUBGRAPH)
    
    # Conditional edges from supervisor
    graph_builder.add_conditional_edges(
        NodeNames.SUPERVISOR_SUBGRAPH,
        lambda state: state.get("supervisor_next_node", END),
        {
            NodeNames.RETRIEVER_SUBGRAPH: NodeNames.RETRIEVER_SUBGRAPH,
            NodeNames.MODELS_SUBGRAPH: NodeNames.MODELS_SUBGRAPH,
            NodeNames.FINAL_RESPONDER: NodeNames.FINAL_RESPONDER,
            END: END,
        }
    )
    
    # Link specialized agents back to supervisor
    graph_builder.add_edge(NodeNames.RETRIEVER_SUBGRAPH, NodeNames.SUPERVISOR_SUBGRAPH)
    graph_builder.add_edge(NodeNames.MODELS_SUBGRAPH, NodeNames.SUPERVISOR_SUBGRAPH)
    # Final edge
    graph_builder.add_edge(NodeNames.FINAL_RESPONDER, END)
    
    compiled_graph = graph_builder.compile(checkpointer=InMemorySaver())
    print(f"[Graph] ✓ Multiagent graph ready")
    return compiled_graph


# Build and export the graph
graph = build_multiagent_graph()
graph.name = NN.GRAPH
