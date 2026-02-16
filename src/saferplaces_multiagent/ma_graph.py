from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .common.names import NN
from .common.states import BaseGraphState, MABaseGraphState
from .ma.initial_chat_agent import InitialChatAgent
from .ma.supervisor_agent import SupervisorAgent


class GraphNodes:
    
    initial_chat_agent = InitialChatAgent()
    supervisor_agent = SupervisorAgent()
    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)


graph_builder.add_node(GN.initial_chat_agent.name, GN.initial_chat_agent)
graph_builder.add_node(GN.supervisor_agent.name, GN.supervisor_agent)

graph_builder.add_edge(START, GN.initial_chat_agent.name)
graph_builder.add_edge(GN.initial_chat_agent.name, GN.supervisor_agent.name)


graph = graph_builder.compile(checkpointer=InMemorySaver())

graph.name = NN.GRAPH