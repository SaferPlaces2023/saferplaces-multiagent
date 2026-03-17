"""Models Agent prompts for environmental simulations and model orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage

from ...common.states import MABaseGraphState

from . import Prompt


# State key constants (referenced for clarity, imported from models_agent.py at runtime)
STATE_MODELS_INVOCATION = "models_invocation"
STATE_MODELS_CONFIRMATION = "models_invocation_confirmation"
STATE_MODELS_REINVOCATION_REQUEST = "models_reinvocation_request"


def _get_conversation_context(state: MABaseGraphState, n: int = 5) -> str:
    """Return last n HumanMessage/AIMessage (no tool_calls) as a readable block."""
    messages = state.get("messages") or []
    relevant = [
        m for m in messages
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    if not relevant:
        return ""
    lines = []
    for m in relevant[-n:]:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)


class ModelsPrompts:
    """Prompts for specialized models/simulations agent.
    
    Follows the F009 pattern with hierarchical organization and static method versioning.
    """

    class MainContext:
        """System-level contextualization for simulation tool selection."""

        @staticmethod
        def stable() -> Prompt:
            """Main context prompt — stable version.
            
            Instructs the agent on its role and constraints for model/simulation selection.
            """
            p = {
                "title": "SimulationToolSelectionContext",
                "description": "system role for environmental models and simulations",
                "command": "",
                "message": (
                    "You are a specialized simulations agent.\n"
                    "\n"
                    "Your task:\n"
                    "- Analyze the simulation/model goal provided by the orchestrator.\n"
                    "- Choose the best model or tool to execute the required simulation.\n"
                    "- Only call tools that are provided in your tool list.\n"
                    "- If a tool requires a layer input, select it from Relevant layers when available.\n"
                    "- If no suitable layer exists, do not invent one; state what layer is missing.\n"
                    "\n"
                    "Rules:\n"
                    "- Do NOT invent tools or tool names.\n"
                    "- Do NOT execute commands directly; only propose tool calls.\n"
                    "- Prioritize accuracy and completeness of arguments.\n"
                    "- Use available context (parsed request, relevant layers) to inform choices."
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Alternative version — stricter tool compliance.
            
            For testing scenarios where tool calls must be highly predictable.
            """
            p = {
                "title": "SimulationToolSelectionContext",
                "description": "strict system role for environmental models",
                "command": "",
                "message": (
                    "You are a specialized simulations agent.\n"
                    "Your task: analyze the goal and choose the correct model/tool.\n"
                    "Rules:\n"
                    "- ONLY call tools from the provided list.\n"
                    "- Every call must have complete arguments (no inference from context).\n"
                    "- If arguments cannot be determined, ask for clarification instead of guessing."
                )
            }
            return Prompt(p)

    class ToolSelection:
        """Tool-specific prompts for invocation and feedback loops."""

        class InitialRequest:
            """Prompt for the initial model/tool invocation."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Initial invocation prompt — stable version.
                
                Includes goal, parsed request, and available layers for context-aware model selection.
                """
                goal = state.get("plan", [{}])[state.get("current_step", 0)].get("goal", "N/A")
                parsed_request = state.get("parsed_request", "")
                relevant_layers = (
                    state.get("additional_context", {})
                    .get("relevant_layers", {})
                    .get("layers", [])
                )
                conversation_context = _get_conversation_context(state)

                message = (
                    f"Goal: {goal}\n"
                    f"\nParsed request: {parsed_request}\n"
                    "\nRelevant layers (use these as inputs if available):\n"
                    f"{json.dumps(relevant_layers, ensure_ascii=False, indent=2)}\n"
                )
                if conversation_context:
                    message += f"\nConversation context (last messages):\n{conversation_context}\n"
                message += "\nNow select and invoke the appropriate model/tool(s) to accomplish the goal."

                p = {
                    "title": "InitialModelInvocation",
                    "description": "prompt for initial model/tool selection and invocation",
                    "command": "",
                    "message": message,
                }
                return Prompt(p)

            @staticmethod
            def v001(state: MABaseGraphState, **kwargs) -> Prompt:
                """Alternative version — minimal context.
                
                For testing with reduced contextual information.
                """
                goal = state.get("plan", [{}])[state.get("current_step", 0)].get("goal", "N/A")

                message = f"Goal: {goal}\n\nSelect the model/tool that best matches this goal."

                p = {
                    "title": "InitialModelInvocation",
                    "description": "minimal prompt for model invocation",
                    "command": "",
                    "message": message,
                }
                return Prompt(p)

        class ReinvocationRequest:
            """Prompt for model/tool re-invocation after user feedback."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Reinvocation prompt after feedback — stable version.
                
                Incorporates user feedback to refine tool call arguments or selection.
                """
                goal = state.get("plan", [{}])[state.get("current_step", 0)].get("goal", "N/A")
                invocation = state.get(STATE_MODELS_INVOCATION)
                reinvocation_request = state.get(STATE_MODELS_REINVOCATION_REQUEST)
                conversation_context = _get_conversation_context(state)

                tool_calls_str = "No tool calls found."
                if invocation and hasattr(invocation, "tool_calls"):
                    tool_calls_str = "\n".join(
                        f"  - {tc.get('name', 'unknown')}: {json.dumps(tc.get('args', {}))}"
                        for tc in invocation.tool_calls
                    )

                user_feedback = (
                    reinvocation_request.content 
                    if reinvocation_request 
                    else "No feedback provided."
                )

                context_section = (
                    f"\nConversation context (last messages):\n{conversation_context}\n"
                    if conversation_context else ""
                )
                message = (
                    f"Goal: {goal}\n"
                    f"\nSome tools need to be reviewed or corrected.\n"
                    f"\nCurrent invocation:\n{tool_calls_str}\n"
                    f"\nUser feedback: {user_feedback}\n"
                    f"{context_section}"
                    "\nProduce a new sequence of tool calls based on the user's feedback.\n"
                    "You can modify arguments, reorder, add, or delete tool calls."
                )

                p = {
                    "title": "ReinvocationAfterFeedback",
                    "description": "prompt for model/tool call refinement after user feedback",
                    "command": "",
                    "message": message,
                }
                return Prompt(p)

            @staticmethod
            def v001(state: MABaseGraphState, **kwargs) -> Prompt:
                """Alternative version — stricter feedback incorporation.
                
                For testing scenarios requiring explicit feedback integration.
                """
                user_feedback = (
                    state.get(STATE_MODELS_REINVOCATION_REQUEST, {}).content 
                    if state.get(STATE_MODELS_REINVOCATION_REQUEST) 
                    else "No feedback."
                )

                message = (
                    f"User feedback: {user_feedback}\n"
                    "Modify your previous tool calls to address this feedback exactly."
                )

                p = {
                    "title": "ReinvocationAfterFeedback",
                    "description": "strict feedback incorporation prompt",
                    "command": "",
                    "message": message,
                }
                return Prompt(p)
