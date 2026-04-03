"""
Constants for multi-agent graph node and agent names.
These names are used consistently across the graph definition and agent registry.
"""


class NodeNames:
    """Standard node names used in the multi-agent graph."""
    
    # Chat nodes
    STATE_PROCESSOR = "state_processor"
    REQUEST_PARSER = "request_parser"
    FINAL_RESPONDER = "final_responder"
    
    # Orchestrator nodes
    SUPERVISOR_SUBGRAPH = "supervisor_subgraph"
    SUPERVISOR_AGENT = "supervisor_agent"
    SUPERVISOR_PLANNER_CONFIRM = "supervisor_planner_confirm"
    SUPERVISOR_ROUTER = "supervisor_router"
    
    # Specialized agent nodes
    RETRIEVER_SUBGRAPH = "retriever_subgraph"
    RETRIEVER_AGENT = "retriever_agent"
    RETRIEVER_INVOCATION_CONFIRM = "retriever_invocation_confirm"
    RETRIEVER_EXECUTOR = "retriever_executor"

    MODELS_SUBGRAPH = "models_subgraph"
    MODELS_AGENT = "models_agent"
    MODELS_INVOCATION_CONFIRM = "models_invocation_confirm"
    MODELS_EXECUTOR = "models_executor"

    LAYERS_AGENT = "layers_agent"
    LAYERS_EXECUTOR = "layers_executor"

    MAP_AGENT = "map_agent"  # PLN-014

    
    DIGITAL_TWIN_AGENT = "digital_twin_agent"
    OPERATIONAL_AGENT = "operational_agent"


# class NodeNames:
#     """Standard agent class names."""
    
#     REQUEST_PARSER = "RequestParser"
#     FINAL_RESPONDER = "FinalResponder"
#     SUPERVISOR_AGENT = "SupervisorAgent"
#     SUPERVISOR_ROUTER = "SupervisorRouter"
#     RETRIEVER_AGENT = "DataRetrieverAgent"
#     MODELS_AGENT = "ModelsAgent"
