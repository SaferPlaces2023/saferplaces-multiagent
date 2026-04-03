
from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames
from ..prompts.final_responder_prompts import FinalResponderInstructions
from langchain_core.messages import AIMessage


class FinalResponder(MultiAgentNode):
    """Agent that generates the final user-facing response."""

    def __init__(self, name: str = NodeNames.FINAL_RESPONDER, log_state: bool = True):
        super().__init__(name, log_state)
        self.llm = _base_llm

    @staticmethod
    def _select_invocation(state: MABaseGraphState):
        """Select the correct Invocation class based on execution state."""
        plan_confirmation = state.get("plan_confirmation")
        plan = state.get("plan")

        if plan_confirmation == "aborted":
            return FinalResponderInstructions.GenerateAbortResponse.Invocation.RespondOneShot

        if plan:
            return FinalResponderInstructions.GenerateResponse.Invocation.RespondOneShot

        return FinalResponderInstructions.GenerateInfoResponse.Invocation.RespondOneShot

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.FINAL_RESPONDER}] → Generating response...")

        invocation_cls = self._select_invocation(state)
        invoke_messages = invocation_cls.stable(state)

        response = self.llm.invoke(invoke_messages)

        # Capture map_commands before cleanup_on_final_response zeroes them
        map_commands = list(state.get("map_commands") or [])
        additional_kwargs = {"map_commands": map_commands} if map_commands else {}

        state["messages"] = [AIMessage(content=response.content, additional_kwargs=additional_kwargs)]

        print(f"[{NodeNames.FINAL_RESPONDER}] ✓ Response ready")

        StateManager.cleanup_on_final_response(state)

        return state


