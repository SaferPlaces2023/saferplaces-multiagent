
from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames
from ..prompts import final_responder_prompts
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage



class FinalResponder(MultiAgentNode):
    """Agent that generates the final user-facing response."""

    def __init__(self, name: str = NodeNames.FINAL_RESPONDER, log_state: bool = True):
        super().__init__(name, log_state)
        self.llm = _base_llm

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.FINAL_RESPONDER}] → Generating response...")

        prompt_response = final_responder_prompts.FinalResponderPrompts.Response.stable()
        prompt_context = final_responder_prompts.FinalResponderPrompts.Context.Formatted.stable(state)

        invoke_messages = [
            SystemMessage(content=prompt_response.message),
            HumanMessage(content=prompt_context.message),
            *state["messages"],
        ]

        response = self.llm.invoke(invoke_messages)

        state["messages"] = [AIMessage(content=response.content)]

        print(f"[{NodeNames.FINAL_RESPONDER}] ✓ Response ready")
        
        # Cleanup state: reset cycle-specific keys, keep persistent data
        StateManager.cleanup_on_final_response(state)
        
        return state
