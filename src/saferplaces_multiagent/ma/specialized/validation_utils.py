"""Shared utilities for tool validation response handling across specialized agents."""

from typing import Dict, Any
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm


# ============================================================================
# Constants
# ============================================================================

# Validation response labels for zero-shot classification
VALIDATION_RESPONSE_LABELS = {
    "provide_corrections": (
        "User provides specific corrections for the invalid parameters. "
        "Examples: 'change bbox to [10,40,12,42]', 'use time_start = 2024-01-01', 'fix these args'"
    ),
    "clarify_requirements": (
        "User asks for explanation of validation rules or parameter requirements. "
        "Examples: 'what is the correct bbox format?', 'what time range is valid?', 'explain these errors'"
    ),
    "auto_correct": (
        "User wants the agent to automatically fix/correct the invalid parameters. "
        "Examples: 'fix it', 'auto-correct', 'you decide', 'make it work', 'fill in correctly'"
    ),
    "acknowledge": (
        "User acknowledges understanding and wants to proceed with automatic correction. "
        "Examples: 'ok', 'yes', 'understood', 'got it', 'thanks', 'proceed'"
    ),
    "skip_tool": (
        "User wants to skip/remove this specific tool call. "
        "Examples: 'skip this tool', 'remove it', 'ignore this one', 'delete this call'"
    ),
    "abort": (
        "User wants to cancel the entire invocation. "
        "Examples: 'cancel', 'stop', 'nevermind', 'abort', 'skip this step'"
    )
}

# Validation states
INVOCATION_REJECTED = "rejected"


# ============================================================================
# Tool Validation Response Handler
# ============================================================================

class ToolValidationResponseHandler:
    """Centralizes tool validation response handling logic for all specialized agents."""

    def __init__(self, llm=None):
        """Initialize handler with LLM for classification."""
        self.llm = llm or _base_llm

    def classify_validation_response(self, response: str, is_post_clarification: bool = False) -> str:
        """Classify user intent for validation failures using zero-shot classification.
        
        Args:
            response: User's response string
            is_post_clarification: Whether this is a response after clarification
            
        Returns:
            Label name: "provide_corrections" | "clarify_requirements" | "auto_correct" | "acknowledge" | "skip_tool" | "abort"
        """
        classification_prompt = (
            f"Classify the user's response into one of these categories:\n\n"
            f"{json.dumps(VALIDATION_RESPONSE_LABELS, indent=2)}\n\n"
            f"User response: {response}\n\n"
        )
        
        if is_post_clarification:
            classification_prompt += (
                f"Note: This is a response AFTER receiving an explanation. "
                f"If the user acknowledges (e.g., 'ok', 'yes'), classify as 'acknowledge'.\n\n"
            )
        
        classification_prompt += "Return ONLY the label name (provide_corrections/clarify_requirements/auto_correct/acknowledge/skip_tool/abort)."

        messages = [
            SystemMessage(content="You are a precise intent classifier."),
            HumanMessage(content=classification_prompt)
        ]

        llm_response = self.llm.invoke(messages)
        label = llm_response.content.strip().lower()

        # Validate label
        if label not in VALIDATION_RESPONSE_LABELS:
            print(f"⚠ Unknown classification '{label}', defaulting to 'auto_correct'")
            return "auto_correct"

        return label

    def process_validation_response(
        self,
        state: MABaseGraphState,
        user_response: str,
        validation_errors: Dict[str, Dict[str, str]],
        confirmation_key: str,
        reinvocation_key: str,
        invocation_key: str,
        current_step_key: str,
        max_clarify_iterations: int = 3
    ) -> MABaseGraphState:
        """Main entry point for validation response handling - dispatch based on classification.
        
        Args:
            state: Current graph state
            user_response: User's response to validation error
            validation_errors: Dict of validation errors by tool name
            confirmation_key: State key for confirmation status
            reinvocation_key: State key for reinvocation request
            invocation_key: State key for tool invocation
            current_step_key: State key for current step
            max_clarify_iterations: Maximum number of clarification loops
            
        Returns:
            Updated state
        """
        # Check if this is a post-clarification response
        is_post_clarification = state.get("validation_clarify_iteration_count", 0) > 0
        
        label = self.classify_validation_response(user_response, is_post_clarification)
        print(f"[ValidationHandler] Classification: {label} (post_clarification={is_post_clarification})")

        if label == "provide_corrections":
            return self._handle_validation_provide_corrections(
                state, confirmation_key, reinvocation_key, current_step_key, user_response
            )
        elif label == "clarify_requirements":
            return self._handle_validation_clarify_requirements(
                state, validation_errors, user_response,
                confirmation_key, reinvocation_key, invocation_key, current_step_key,
                max_clarify_iterations
            )
        elif label == "auto_correct" or label == "acknowledge":
            # Reset clarify counter when proceeding
            state["validation_clarify_iteration_count"] = 0
            return self._handle_validation_auto_correct(
                state, validation_errors, confirmation_key, reinvocation_key, current_step_key
            )
        elif label == "skip_tool":
            return self._handle_validation_skip_tool(
                state, validation_errors, invocation_key, current_step_key
            )
        elif label == "abort":
            return self._handle_abort(state, confirmation_key)

        # Fallback (should never reach here)
        return self._handle_validation_auto_correct(
            state, validation_errors, confirmation_key, reinvocation_key, current_step_key
        )

    def _handle_validation_provide_corrections(
        self,
        state: MABaseGraphState,
        confirmation_key: str,
        reinvocation_key: str,
        current_step_key: str,
        user_response: str
    ) -> MABaseGraphState:
        """Handle provide_corrections: user provides specific corrections."""
        state[current_step_key] = 0
        state[confirmation_key] = INVOCATION_REJECTED
        state[reinvocation_key] = HumanMessage(content=user_response)
        
        # Reset clarify counter
        state["validation_clarify_iteration_count"] = 0
        
        print("[ValidationHandler] → Provide corrections: preparing re-invocation with user fixes")
        return state

    def _handle_validation_clarify_requirements(
        self,
        state: MABaseGraphState,
        validation_errors: Dict[str, Dict[str, str]],
        user_question: str,
        confirmation_key: str,
        reinvocation_key: str,
        invocation_key: str,
        current_step_key: str,
        max_iterations: int
    ) -> MABaseGraphState:
        """Handle clarify_requirements: recursive explanation loop for validation rules.
        
        Args:
            state: Current graph state
            validation_errors: Dict of validation errors
            user_question: User's question
            confirmation_key: Key for confirmation status
            reinvocation_key: Key for reinvocation request
            invocation_key: Key for tool invocation
            current_step_key: Key for current step
            max_iterations: Maximum clarification iterations
            
        Returns:
            Updated state (may recursively call process_validation_response)
        """
        # Check iteration count
        clarify_count = state.get("validation_clarify_iteration_count", 0)
        if clarify_count >= max_iterations:
            print(f"[ValidationHandler] ⚠ Max clarify iterations ({max_iterations}) reached, forcing provide_corrections")
            return self._handle_validation_provide_corrections(
                state, confirmation_key, reinvocation_key, current_step_key, user_question
            )

        state["validation_clarify_iteration_count"] = clarify_count + 1
        print(f"[ValidationHandler] → Clarify iteration {state['validation_clarify_iteration_count']}/{max_iterations}")

        # Generate explanation
        explanation = self._generate_validation_explanation(validation_errors, user_question)

        # Re-interrupt with explanation
        interruption = interrupt({
            "content": f"{explanation}\n\nHow would you like to proceed?",
            "interrupt_type": "validation-clarification"
        })

        new_response = interruption.get("response", "User did not provide any response.")

        # Recursive call with new response
        return self.process_validation_response(
            state, new_response, validation_errors,
            confirmation_key, reinvocation_key, invocation_key, current_step_key,
            max_iterations
        )

    def _handle_validation_auto_correct(
        self,
        state: MABaseGraphState,
        validation_errors: Dict[str, Dict[str, str]],
        confirmation_key: str,
        reinvocation_key: str,
        current_step_key: str
    ) -> MABaseGraphState:
        """Handle auto_correct: agent automatically fixes invalid parameters."""
        state[current_step_key] = 0
        state[confirmation_key] = INVOCATION_REJECTED
        
        # Build auto-correct request for agent
        error_summary = "\n".join(
            f"  {tool_name}: {', '.join(f'{arg}={error}' for arg, error in tool_errors.items())}"
            for tool_name, tool_errors in validation_errors.items()
        )
        
        auto_correct_message = (
            f"Please automatically correct these validation errors:\n{error_summary}\n\n"
            f"Use reasonable defaults or infer correct values from context."
        )
        
        state[reinvocation_key] = HumanMessage(content=auto_correct_message)
        print("[ValidationHandler] → Auto-correct: preparing re-invocation with auto-fix request")
        return state

    def _handle_validation_skip_tool(
        self,
        state: MABaseGraphState,
        validation_errors: Dict[str, Dict[str, str]],
        invocation_key: str,
        current_step_key: str
    ) -> MABaseGraphState:
        """Handle skip_tool: remove problematic tool calls and proceed with remaining ones."""
        invocation = state.get(invocation_key)
        if not invocation or not hasattr(invocation, 'tool_calls'):
            print("[ValidationHandler] ⚠ No tool calls to skip")
            state["current_step"] += 1
            return state

        # Filter out tool calls with validation errors
        failed_tool_names = set(validation_errors.keys())
        remaining_tool_calls = [
            tc for tc in invocation.tool_calls
            if tc["name"] not in failed_tool_names
        ]

        if not remaining_tool_calls:
            # All tool calls failed, skip entire step
            print("[ValidationHandler] ⚠ All tool calls failed validation, skipping step")
            state["current_step"] += 1
            return state

        # Update invocation with only remaining tool calls
        invocation.tool_calls = remaining_tool_calls
        state[invocation_key] = invocation
        state[current_step_key] = 0
        
        # Reset clarify counter
        state["validation_clarify_iteration_count"] = 0
        
        print(f"[ValidationHandler] → Skip tool: removed {len(failed_tool_names)} tool(s), proceeding with {len(remaining_tool_calls)}")
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
            print(f"[ValidationHandler] ⚠ Abort: skipping to step {state['current_step']}")
        else:
            # Mark as complete if no more steps
            state["current_step"] = plan_length
            print("[ValidationHandler] ⚠ Abort: no more steps, marking as complete")
        
        # Reset clarify counter
        state["validation_clarify_iteration_count"] = 0
        
        return state

    def _generate_validation_explanation(
        self,
        validation_errors: Dict[str, Dict[str, str]],
        user_question: str
    ) -> str:
        """Generate explanation about validation errors using LLM.
        
        Args:
            validation_errors: Dict of validation errors by tool name
            user_question: User's specific question
            
        Returns:
            Explanation string
        """
        errors_str = "\n".join(
            f"  {tool_name}:\n" + "\n".join(
                f"    - {arg}: {error}"
                for arg, error in tool_errors.items()
            )
            for tool_name, tool_errors in validation_errors.items()
        )

        prompt = (
            f"User asked: '{user_question}'\n"
            f"\n"
            f"About these validation errors:\n{errors_str}\n"
            f"\n"
            f"Provide a clear, concise explanation that answers the user's specific question. "
            f"Explain what the validation rules are and why these errors occurred. "
            f"Be informative but brief."
        )

        messages = [
            SystemMessage(content="You are a helpful assistant explaining validation requirements."),
            HumanMessage(content=prompt)
        ]

        response = self.llm.invoke(messages)
        return response.content
