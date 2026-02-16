from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from ..common.names import NN
from ..common.states import BaseGraphState, MABaseGraphState




class SaferCastSupervisor():
    
    def __init__(self):
        self.name = 'SaferCastSupervisor'
    
    @staticmethod
    def __call__(state: MABaseGraphState) -> MABaseGraphState:
        return SaferCastSupervisor.run(state)
    
    @staticmethod
    def run(state: MABaseGraphState) -> MABaseGraphState:
        
        intent_supervisor = state["intent_supervisor"]



class GraphNodes:
    pass
    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)


graph_builder.add_node(GN.initial_chat_agent.name, GN.initial_chat_agent)

graph_builder.add_edge(START, GN.initial_chat_agent.name)


graph = graph_builder.compile()
graph.name = NN.safercast_agent