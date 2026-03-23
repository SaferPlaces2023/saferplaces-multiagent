"""Shared utilities for tool invocation confirmation across specialized agents."""

from typing import Dict, Any
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ...common.response_classifier import ResponseClassifier


# ============================================================================
# Constants
# ============================================================================

# Invocation response labels for zero-shot classification
INVOCATION_RESPONSE_LABELS = {
    "accept": (
        "User accepts the tool calls and wants to proceed immediately. "
        "Examples: 'ok', 'yes', 'proceed', 'looks good', 'go ahead', 'do it'"
    ),
    "modify": (
        "User wants changes to tool call arguments but still intends to execute. "
        "Examples: 'change bbox', 'use different time', 'modify parameter X', 'adjust args'"
    ),
    "clarify": (
        "User needs more information before deciding (asking questions, not rejecting). "
        "Examples: 'what does this tool do?', 'explain parameter X', 'why these args?'"
    ),
    "reject": (
        "User wants completely different tools or approach. "
        "Examples: 'no, use tool Y instead', 'wrong approach', 'try different method'"
    ),
    "abort": (
        "User wants to cancel the operation entirely. "
        "Examples: 'cancel', 'stop', 'nevermind', 'abort', 'skip this step'"
    )
}

# Confirmation states (reusable across agents)
INVOCATION_PENDING = "pending"
INVOCATION_ACCEPTED = "accepted"
INVOCATION_REJECTED = "rejected"


# ============================================================================
# Tool Invocation Confirmation Handler
# ============================================================================

class ToolInvocationConfirmationHandler:
    """Centralizes tool invocation confirmation logic for all specialized agents."""

    def __init__(self, llm=None):
        """Initialize handler with LLM for classification."""
        self.llm = llm or _base_llm
        self._classifier = ResponseClassifier(self.llm)

    def classify_user_response(self, response: str) -> str:
        """Classify user intent using hybrid rule-based + LLM classification.
        
        Args:
            response: User's response string
            
        Returns:
            Label name: "accept" | "modify" | "clarify" | "reject" | "abort"
        """
        return self._classifier.classify_invocation_response(response)

    def process_confirmation(
        self,
        state: MABaseGraphState,
        user_response: str,
        confirmation_key: str,
        reinvocation_key: str,
        invocation_key: str,
        max_clarify_iterations: int = 3
    ) -> MABaseGraphState:
        """Main entry point - dispatch based on classification.
        
        Args:
            state: Current graph state
            user_response: User's response to confirmation prompt
            confirmation_key: State key for confirmation status (e.g., "retriever_invocation_confirmation")
            reinvocation_key: State key for reinvocation request (e.g., "retriever_reinvocation_request")
            invocation_key: State key for tool invocation (e.g., "retriever_invocation")
            max_clarify_iterations: Maximum number of clarification loops
            
        Returns:
            Updated state
        """
        label = self.classify_user_response(user_response)
        print(f"[ConfirmationHandler] Classification: {label}")

        # Record user response in conversation history so all downstream LLMs can see it
        state["messages"] = [HumanMessage(content=user_response)]

        if label == "accept":
            return self._handle_accept(state, confirmation_key, reinvocation_key)
        elif label == "modify":
            return self._handle_modify(state, confirmation_key, reinvocation_key, user_response)
        elif label == "reject":
            return self._handle_reject(state, confirmation_key, reinvocation_key, user_response)
        elif label == "abort":
            return self._handle_abort(state, confirmation_key)
        elif label == "clarify":
            return self._handle_clarify(
                state, invocation_key, user_response,
                confirmation_key, reinvocation_key,
                max_clarify_iterations
            )

        # Fallback (should never reach here)
        return self._handle_reject(state, confirmation_key, reinvocation_key, user_response)

    def _handle_accept(
        self,
        state: MABaseGraphState,
        confirmation_key: str,
        reinvocation_key: str
    ) -> MABaseGraphState:
        """Handle accept: mark as accepted and clear reinvocation."""
        state[confirmation_key] = INVOCATION_ACCEPTED
        state[reinvocation_key] = None
        print("[ConfirmationHandler] ✓ Accepted")
        return state

    def _handle_modify(
        self,
        state: MABaseGraphState,
        confirmation_key: str,
        reinvocation_key: str,
        user_response: str
    ) -> MABaseGraphState:
        """Handle modify: prepare for incremental re-invocation."""
        state[confirmation_key] = INVOCATION_REJECTED
        state[reinvocation_key] = HumanMessage(content=user_response)
        print("[ConfirmationHandler] → Modify: preparing re-invocation")
        return state

    def _handle_reject(
        self,
        state: MABaseGraphState,
        confirmation_key: str,
        reinvocation_key: str,
        user_response: str
    ) -> MABaseGraphState:
        """Handle reject: prepare for total re-invocation."""
        state[confirmation_key] = INVOCATION_REJECTED
        state[reinvocation_key] = HumanMessage(content=user_response)
        print("[ConfirmationHandler] → Reject: preparing total re-invocation")
        return state

    def _handle_abort(
        self,
        state: MABaseGraphState,
        confirmation_key: str
    ) -> MABaseGraphState:
        """Handle abort: cancel tool execution, skip this step."""
        state[confirmation_key] = INVOCATION_REJECTED
        
        # Safely increment current_step only if there are more steps
        plan_length = len(state.get("plan", []))
        current_step = state.get("current_step", 0)
        
        if current_step + 1 < plan_length:
            state["current_step"] += 1
            print(f"[ConfirmationHandler] ⚠ Abort: skipping to step {state['current_step']}")
        else:
            # Mark as complete if no more steps
            state["current_step"] = plan_length
            print("[ConfirmationHandler] ⚠ Abort: no more steps, marking as complete")
        
        return state

    def _handle_clarify(
        self,
        state: MABaseGraphState,
        invocation_key: str,
        user_question: str,
        confirmation_key: str,
        reinvocation_key: str,
        max_iterations: int
    ) -> MABaseGraphState:
        """Handle clarify: recursive explanation loop.
        
        Args:
            state: Current graph state
            invocation_key: Key to get tool invocation (contains tool_calls)
            user_question: User's question
            confirmation_key: Key for confirmation status
            reinvocation_key: Key for reinvocation request
            max_iterations: Maximum clarification iterations
            
        Returns:
            Updated state (may recursively call process_confirmation)
        """
        # Check interaction budget
        interaction_count = state.get("interaction_count", 0)
        interaction_budget = state.get("interaction_budget", 8)
        if interaction_count >= interaction_budget:
            print(f"[ConfirmationHandler] ⚠ Interaction budget exhausted ({interaction_budget}), forcing reject")
            return self._handle_reject(state, confirmation_key, reinvocation_key, user_question)

        state["interaction_count"] = interaction_count + 1
        print(f"[ConfirmationHandler] → Clarify iteration (interaction {state['interaction_count']}/{interaction_budget})")

        # Generate explanation
        invocation = state.get(invocation_key)
        if not invocation or not hasattr(invocation, 'tool_calls'):
            print("[ConfirmationHandler] ⚠ No tool calls to explain")
            return self._handle_reject(state, confirmation_key, reinvocation_key, user_question)

        explanation = self._generate_tool_call_explanation(invocation.tool_calls, user_question)

        # Re-interrupt with explanation
        interruption = interrupt({
            "content": f"{explanation}\n\nDo you want to proceed with these tool calls?",
            "interrupt_type": "invocation-clarification"
        })

        new_response = interruption.get("response", "User did not provide any response.")

        # Recursive call with new response
        return self.process_confirmation(
            state, new_response,
            confirmation_key, reinvocation_key, invocation_key,
            max_iterations
        )

    def _generate_tool_call_explanation(
        self,
        tool_calls: list,
        user_question: str
    ) -> str:
        """Generate explanation about tool calls using LLM.
        
        Args:
            tool_calls: List of tool call dicts
            user_question: User's specific question
            
        Returns:
            Explanation string
        """
        tool_calls_str = "\n".join(
            f"  - {tc['name']}({json.dumps(tc.get('args', {}), indent=4)})"
            for tc in tool_calls
        )

        prompt = (
            f"User asked: '{user_question}'\n"
            f"\n"
            f"About these tool calls:\n{tool_calls_str}\n"
            f"\n"
            f"Provide a clear, concise explanation that answers the user's specific question. "
            f"Focus on helping them understand what these tools will do. "
            f"Be informative but brief."
        )

        messages = [
            SystemMessage(content="You are a helpful assistant explaining tool invocations."),
            HumanMessage(content=prompt)
        ]

        response = self.llm.invoke(messages)
        return response.content
