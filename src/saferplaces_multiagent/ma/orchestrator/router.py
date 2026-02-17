
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

from saferplaces_multiagent.common.states import MABaseGraphState

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState



class SupervisorRouterNode:


    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        def supervisor_next_node(state: MABaseGraphState) -> str:

            if state.get("awaiting_user"):
                return END

            plan = state.get("plan")
            step = state.get("current_step")

            if not plan:
                return "chat_final"

            if step is not None and step < len(plan):
                return plan[step]["agent"]

            return "chat_final"
        
        state['supervisor_next_node'] = supervisor_next_node(state)
        
        return state