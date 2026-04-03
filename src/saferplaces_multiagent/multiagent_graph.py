
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from langchain_core.messages import HumanMessage

from .common.names import NN
from .common.states import MABaseGraphState
from .ma.names import NodeNames
from .ma.chat.request_parser import RequestParser
from .ma.chat.final_responder import FinalResponder
from .ma.chat.state_processor import StateProcessor
from .ma.orchestrator.supervisor import SupervisorAgent, SupervisorRouter, SupervisorPlannerConfirm, PlanConfirmationLabels
from .ma.specialized.safercast_agent import DataRetrieverAgent, DataRetrieverInvocationConfirm, DataRetrieverExecutor
from .ma.specialized.models_agent import ModelsAgent, ModelsExecutor, ModelsInvocationConfirm
from .ma.specialized.layers_agent import LayersAgent
from .ma.specialized.map_agent import MapAgent


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
    supervisor_builder.add_conditional_edges(
        supervisor_planner_confirm.name,
        lambda state: state.get('plan_confirmation') if state.get('plan') else END,
        {
            PlanConfirmationLabels.ACCEPTED: supervisor_router.name,
            PlanConfirmationLabels.MODIFY: supervisor_agent.name,
            PlanConfirmationLabels.ABORTED: supervisor_router.name,
            END: END
        }
    )
    
    return supervisor_builder.compile()


def build_specialized_retriever_subgraph():
    """Build the specialized retriever subgraph."""
    print(f"[Graph] Building specialized retriever subgraph...")
    retriever_builder = StateGraph(MABaseGraphState)
    
    retriever_agent = DataRetrieverAgent()
    retriever_invocation_confirm = DataRetrieverInvocationConfirm(enabled=False)
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
    models_invocation_confirm = ModelsInvocationConfirm(enabled=False)
    models_executor = ModelsExecutor()

    models_builder.add_node(models_agent.name, models_agent)
    models_builder.add_node(models_invocation_confirm.name, models_invocation_confirm)
    models_builder.add_node(models_executor.name, models_executor)

    models_builder.add_edge(START, models_agent.name)
    
    models_builder.add_edge(models_agent.name, models_invocation_confirm.name)
    
    models_builder.add_conditional_edges(
        models_invocation_confirm.name,
        lambda state: state.get('models_invocation_confirmation') if state.get('models_invocation') else END,
        {
            'accepted': models_executor.name,
            'modify': models_agent.name,
            'aborted': models_executor.name,
            END: END
        }
    )
    
    
    # models_builder.add_conditional_edges(
    #     models_invocation_confirm.name,
    #     lambda state: state.get('models_invocation_confirmation') == 'rejected',
    #     {
    #         True: models_agent.name,
    #         False: models_executor.name,
    #     }
    # )
    
    return models_builder.compile()


def build_multiagent_graph():
    
    """Build the main multi-agent graph with all nodes and edges."""
    
    print(f"[Graph] Building multiagent graph...")
    
    graph_builder = StateGraph(MABaseGraphState)
    
    def _human_start(state):
        messages = state.get("messages") or []
        if messages and isinstance(messages[-1], HumanMessage):
            return NodeNames.REQUEST_PARSER
        return NodeNames.STATE_PROCESSOR

    graph_builder.add_conditional_edges(START, _human_start, {
        NodeNames.REQUEST_PARSER: NodeNames.REQUEST_PARSER,
        NodeNames.STATE_PROCESSOR: NodeNames.STATE_PROCESSOR,
    })

    # Initialize agents
    request_parser = RequestParser()
    state_processor = StateProcessor()
    supervisor_subgraph = build_supervisor_subgraph()
    models_subgraph = build_specialized_models_subgraph()
    retriever_subgraph = build_specialized_retriever_subgraph()
    map_agent = MapAgent()
    layer_agent = LayersAgent()
    final_responder = FinalResponder()
    
    graph_builder.add_node(NodeNames.REQUEST_PARSER, request_parser)
    graph_builder.add_node(NodeNames.STATE_PROCESSOR, state_processor)
    graph_builder.add_node(NodeNames.SUPERVISOR_SUBGRAPH, supervisor_subgraph)
    graph_builder.add_node(NodeNames.RETRIEVER_SUBGRAPH, retriever_subgraph)
    graph_builder.add_node(NodeNames.MODELS_SUBGRAPH, models_subgraph)
    graph_builder.add_node(NodeNames.MAP_AGENT, map_agent)
    graph_builder.add_node(NodeNames.LAYERS_AGENT, layer_agent)
    graph_builder.add_node(NodeNames.FINAL_RESPONDER, final_responder)
    
    # Add edges
    # graph_builder.add_edge(START, NodeNames.STATE_PROCESSOR)

    # # def _route_from_state_processor(state):
        
    # #     messages = state.get("messages") or []
    # #     if messages and isinstance(messages[-1], HumanMessage):
    # #         return NodeNames.REQUEST_PARSER
    # #     if state.get("map_request"):
    # #         return NodeNames.MAP_AGENT
    # #     return END

    # graph_builder.add_conditional_edges(
    #     NodeNames.STATE_PROCESSOR,
    #     _route_from_state_processor,
    #     {
    #         NodeNames.REQUEST_PARSER: NodeNames.REQUEST_PARSER,
    #         NodeNames.MAP_AGENT: NodeNames.MAP_AGENT,
    #         END: END,
    #     },
    # )
    graph_builder.add_edge(NodeNames.REQUEST_PARSER, NodeNames.SUPERVISOR_SUBGRAPH)
    
    # Conditional edges from supervisor
    graph_builder.add_conditional_edges(
        NodeNames.SUPERVISOR_SUBGRAPH,
        lambda state: state.get("supervisor_next_node", NodeNames.FINAL_RESPONDER).lower(),
        {
            NodeNames.RETRIEVER_SUBGRAPH: NodeNames.RETRIEVER_SUBGRAPH,
            NodeNames.RETRIEVER_AGENT: NodeNames.RETRIEVER_SUBGRAPH,

            NodeNames.MODELS_SUBGRAPH: NodeNames.MODELS_SUBGRAPH,
            NodeNames.MODELS_AGENT: NodeNames.MODELS_SUBGRAPH,
            
            NodeNames.MAP_AGENT: NodeNames.MAP_AGENT,
            NodeNames.LAYERS_AGENT: NodeNames.LAYERS_AGENT,
            
            NodeNames.FINAL_RESPONDER: NodeNames.FINAL_RESPONDER,
            
            END: END,
        }
    )
    
    # Link specialized agents back to supervisor
    graph_builder.add_edge(NodeNames.RETRIEVER_SUBGRAPH, NodeNames.SUPERVISOR_SUBGRAPH)
    graph_builder.add_edge(NodeNames.MODELS_SUBGRAPH, NodeNames.SUPERVISOR_SUBGRAPH)

    graph_builder.add_edge(NodeNames.LAYERS_AGENT, NodeNames.SUPERVISOR_SUBGRAPH)

    # MAP_AGENT loops to supervisor when called from a plan, or ends for state-only invocations
    graph_builder.add_conditional_edges(
        NodeNames.MAP_AGENT,
        lambda state: (
            NodeNames.SUPERVISOR_SUBGRAPH
            if state.get("parsed_request") and not state.get("map_request")
            else END
        ),
        {
            NodeNames.SUPERVISOR_SUBGRAPH: NodeNames.SUPERVISOR_SUBGRAPH,
            END: END,
        },
    )
    # Final edge
    graph_builder.add_edge(NodeNames.FINAL_RESPONDER, END)
    
    compiled_graph = graph_builder.compile(checkpointer=InMemorySaver())
    # compiled_graph = graph_builder.compile()

    print(f"[Graph] ✓ Multiagent graph ready")
    return compiled_graph


# Build and export the graph
graph = build_multiagent_graph()
graph.name = NN.GRAPH
