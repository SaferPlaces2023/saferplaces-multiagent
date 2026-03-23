
from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ...common.execution_narrative import ExecutionNarrative
from ..names import NodeNames, NodeNames
from ..prompts import final_responder_prompts
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import datetime


class FinalResponder(MultiAgentNode):
    """Agent that generates the final user-facing response."""

    def __init__(self, name: str = NodeNames.FINAL_RESPONDER, log_state: bool = True):
        super().__init__(name, log_state)
        self.llm = _base_llm

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.FINAL_RESPONDER}] → Generating response...")

        prompt_response = final_responder_prompts.FinalResponderPrompts.Response.stable(state)
        prompt_context = final_responder_prompts.FinalResponderPrompts.Context.Formatted.stable(state)

        # PHASE 1: Finalize execution narrative (§3 PLN-013)
        narrative = state.get("execution_narrative")
        if narrative:
            narrative.completed_at = datetime.datetime.utcnow().isoformat()
            print(f"[{NodeNames.FINAL_RESPONDER}] ✓ Execution narrative finalized")

        # Filter conversation history: exclude internal tool messages
        filtered_messages = self._filter_conversation_history(state)

        invoke_messages = [
            SystemMessage(content=prompt_response.message),
            HumanMessage(content=prompt_context.message),
            *filtered_messages,
        ]

        response = self.llm.invoke(invoke_messages)

        state["messages"] = [AIMessage(content=response.content)]

        print(f"[{NodeNames.FINAL_RESPONDER}] ✓ Response ready")
        
        # Cleanup state: reset cycle-specific keys, keep persistent data
        StateManager.cleanup_on_final_response(state)
        
        return state

    @staticmethod
    def _filter_conversation_history(state: MABaseGraphState) -> list:
        """
        Filter conversation history to exclude internal tool messages.
        Keep only:
        - HumanMessage (user inputs)
        - AIMessage without tool_calls (final responses)
        
        Exclude:
        - ToolMessage (internal)
        - AIMessage with tool_calls (internal agent planning)
        - SystemMessage
        """
        messages = state.get("messages", [])
        filtered = []
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                filtered.append(msg)
            elif isinstance(msg, AIMessage):
                # Only include if no tool_calls (it's a final response)
                if not msg.tool_calls:
                    filtered.append(msg)
        
        return filtered

