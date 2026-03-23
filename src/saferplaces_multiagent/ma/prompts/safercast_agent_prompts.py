"""SaferCast Agent prompts for data retrieval and tool orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json

from ...common.states import MABaseGraphState

from . import Prompt

from ...common.utils import get_conversation_context as _get_conversation_context


# State key constants (referenced for clarity, imported from safercast_agent.py at runtime)
STATE_RETRIEVER_INVOCATION = "retriever_invocation"
STATE_RETRIEVER_CONFIRMATION = "retriever_invocation_confirmation"
STATE_RETRIEVER_REINVOCATION_REQUEST = "retriever_reinvocation_request"


class SaferCastPrompts:
    """Prompts for specialized data retrieval agent (SaferCast / Data Retriever).
    
    Follows the F009 pattern with hierarchical organization and static method versioning.
    """

    class MainContext:
        """System-level contextualization for tool selection and retrieval."""

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "ToolSelectionContext",
                "description": "system role for data retrieval with tool-specific guides",
                "command": "",
                "message": (
                    "You are a specialized data retrieval agent for a geospatial AI platform.\n"
                    "\n"
                    "## Your task\n"
                    "1. Analyze the retrieval goal provided by the orchestrator.\n"
                    "2. Select the correct retrieval tool and provide accurate arguments.\n"
                    "3. Use available context (parsed request, relevant layers, conversation history) "
                    "to infer missing arguments.\n"
                    "4. When required arguments are not explicitly stated, propose the most likely values "
                    "based on context — best-effort inference is expected.\n"
                    "\n"
                    "## Tool: dpc_retriever\n"
                    "Retrieves radar/meteorological data from Italian Civil Protection (DPC).\n"
                    "\n"
                    "Coverage: **Italy only**. Data: **past/recent** — NOT forecasts.\n"
                    "Data delay: ~10 minutes from real-time.\n"
                    "Time range: last 7 days maximum.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `product` (required): DPC product code. Common products:\n"
                    "  • SRI: Surface Rainfall Intensity (mm/h) — most common for \"current rainfall\"\n"
                    "  • VMI: Vertical Maximum Intensity (max reflectivity, dBZ)\n"
                    "  • SRT1/3/6/12/24: Cumulative precipitation over 1/3/6/12/24 hours\n"
                    "  • TEMP: Temperature map\n"
                    "  • LTG: Lightning strike frequency\n"
                    "  • IR108: Cloud cover from satellite\n"
                    "  • HRD: Heavy Rain Detection index\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `bbox`: bounding box {west, south, east, north} in EPSG:4326. Default: all Italy.\n"
                    "- `time_start`, `time_end`: ISO8601 timestamps. Must be in the past (within 7 days).\n"
                    "  → If not specified, infer a reasonable recent window (e.g. last 1-6 hours).\n"
                    "\n"
                    "## Tool: meteoblue_retriever\n"
                    "Retrieves weather forecast data from Meteoblue.\n"
                    "\n"
                    "Coverage: **global**. Data: **future forecasts** up to 14 days ahead.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `variable` (required): meteorological variable. Common variables:\n"
                    "  • PRECIPITATION: precipitation amount (mm)\n"
                    "  • TEMPERATURE: air temperature (°C)\n"
                    "  • WINDSPEED: wind speed (m/s)\n"
                    "  • WINDDIRECTION: wind direction (degrees)\n"
                    "  • RELATIVEHUMIDITY: relative humidity (%)\n"
                    "  • PRECIPITATION_PROBABILITY: probability of precipitation (%)\n"
                    "  • FELTTEMPERATURE: apparent temperature (°C)\n"
                    "  • SEALEVELPRESSURE: sea level pressure (hPa)\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `bbox`: bounding box {west, south, east, north} in EPSG:4326.\n"
                    "- `time_start`, `time_end`: ISO8601 timestamps. Must be in the future.\n"
                    "  → If not specified, infer a reasonable forecast window (e.g. next 24 hours).\n"
                    "\n"
                    "## Decision guide\n"
                    "- Goal mentions past/current/historical data + Italy → use **dpc_retriever**\n"
                    "- Goal mentions forecast/future/prediction → use **meteoblue_retriever**\n"
                    "- Goal mentions area outside Italy → use **meteoblue_retriever**\n"
                    "- Goal mentions \"rainfall radar\" or \"radar\" → use **dpc_retriever** (SRI or VMI)\n"
                    "- Goal mentions specific DPC product (SRI, VMI, SRT*) → use **dpc_retriever**\n"
                    "\n"
                    "## Common mistakes to avoid\n"
                    "- Do NOT use dpc_retriever for areas outside Italy\n"
                    "- Do NOT use dpc_retriever with future timestamps\n"
                    "- Do NOT use meteoblue_retriever for past/historical data\n"
                    "- Do NOT leave bbox empty when the goal specifies a location — infer it\n"
                    "\n"
                    "## Rules\n"
                    "- Use only tools from the provided list.\n"
                    "- Do NOT execute commands directly; only propose tool calls.\n"
                    "- Prioritize completeness: always produce a tool call, even with inferred arguments."
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Previous stable version — preserved for test override compatibility."""
            p = {
                "title": "ToolSelectionContext",
                "description": "system role for data retrieval and tool selection",
                "command": "",
                "message": (
                    "You are a specialized agent for data retrieval.\n"
                    "\n"
                    "Your task:\n"
                    "- Analyze the retrieval goal provided by the orchestrator.\n"
                    "- Choose the best tool(s) to retrieve the required data.\n"
                    "- Only call tools that are provided in your tool list.\n"
                    "- If needed information is missing (e.g., time range, bbox), "
                    "still propose the most likely tool call with best-effort arguments.\n"
                    "\n"
                    "Rules:\n"
                    "- Do NOT invent tools or tool names.\n"
                    "- Do NOT execute commands directly; only propose tool calls.\n"
                    "- Prioritize accuracy and completeness of arguments.\n"
                    "- Use available context (parsed request, relevant layers) to inform choices."
                )
            }
            return Prompt(p)

    class ToolSelection:
        """Tool-specific prompts for invocation and feedback loops."""

        class InitialRequest:
            """Prompt for the initial tool invocation."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Initial invocation prompt — stable version.
                
                Includes goal, parsed request, and available layers for context-aware tool selection.
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
                message += "\nNow select and invoke the appropriate tool(s) to accomplish the goal."

                p = {
                    "title": "InitialToolInvocation",
                    "description": "prompt for initial tool selection and invocation",
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

                message = f"Goal: {goal}\n\nSelect the tool that best matches this goal."

                p = {
                    "title": "InitialToolInvocation",
                    "description": "minimal prompt for tool invocation",
                    "command": "",
                    "message": message,
                }
                return Prompt(p)

        class ReinvocationRequest:
            """Prompt for tool re-invocation after user feedback."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Reinvocation prompt after feedback — stable version.
                
                Incorporates user feedback to refine tool call arguments or selection.
                """
                goal = state.get("plan", [{}])[state.get("current_step", 0)].get("goal", "N/A")
                invocation = state.get(STATE_RETRIEVER_INVOCATION)
                reinvocation_request = state.get(STATE_RETRIEVER_REINVOCATION_REQUEST)
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
                    "description": "prompt for tool call refinement after user feedback",
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
                    state.get(STATE_RETRIEVER_REINVOCATION_REQUEST, {}).content 
                    if state.get(STATE_RETRIEVER_REINVOCATION_REQUEST) 
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
