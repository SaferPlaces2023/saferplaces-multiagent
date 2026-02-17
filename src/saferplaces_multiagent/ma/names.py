"""
Constants for multi-agent graph node and agent names.
These names are used consistently across the graph definition and agent registry.
"""


class NodeNames:
    """Standard node names used in the multi-agent graph."""
    
    # Chat nodes
    REQUEST_PARSER = "request_parser"
    FINAL_RESPONDER = "final_responder"
    
    # Orchestrator nodes
    SUPERVISOR_SUBGRAPH = "supervisor_subgraph"
    SUPERVISOR_AGENT = "supervisor_agent"
    SUPERVISOR_ROUTER = "supervisor_router"
    
    # Specialized agent nodes
    RETRIEVAL_AGENT = "retrieval_agent"
    DIGITAL_TWIN_AGENT = "digital_twin_agent"
    SIMULATIONS_AGENT = "simulations_agent"
    OPERATIONAL_AGENT = "operational_agent"


class AgentNames:
    """Standard agent class names."""
    
    REQUEST_PARSER = "RequestParser"
    FINAL_RESPONDER = "FinalResponder"
    SUPERVISOR_AGENT = "SupervisorAgent"
    SUPERVISOR_ROUTER = "SupervisorRouter"
    DATA_RETRIEVER_AGENT = "DataRetrieverAgent"
