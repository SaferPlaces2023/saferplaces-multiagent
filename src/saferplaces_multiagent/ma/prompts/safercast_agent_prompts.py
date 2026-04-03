"""SaferCast Agent prompts for data retrieval and tool orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json
import datetime

from typing import Dict

from langchain_core.messages import HumanMessage, SystemMessage

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from .request_parser_prompts import RequestParserInstructions


class SaferCastInstructions:

    class InvokeTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a data retrieval specialist operating within the SaferPlaces platform.\n"
                        "Your task is to select and configure the correct tool to retrieve meteorological or observational data to accomplish a specific goal.\n"
                        "You produce tool call arguments only — you do not interpret results, generate narratives, or communicate directly with the user.\n"
                        "\n"
                        "AVAILABLE TOOLS (summary):\n"
                        "- dpc_retriever: fetches real-time and historical radar/meteorological products from the Italian Civil Protection (DPC).\n"
                        "  Coverage: Italy only. Data: past/recent (up to 7 days back). NOT for forecasts.\n"
                        "- meteoblue_retriever: fetches weather forecast data from Meteoblue.\n"
                        "  Coverage: global. Data: future forecasts up to 14 days ahead. NOT for historical data.\n"
                        "\n"
                        "DECISION RULES:\n"
                        "- Goal mentions past/current/historical + Italy → use dpc_retriever.\n"
                        "- Goal mentions forecast/future/prediction → use meteoblue_retriever.\n"
                        "- Goal mentions area outside Italy → use meteoblue_retriever.\n"
                        "- Goal mentions radar, SRI, VMI, or other DPC product codes → use dpc_retriever.\n"
                        "- When in doubt about temporal scope, prefer the tool that fits the time direction of the goal.\n"
                        "\n"
                        "COMMON MISTAKES TO AVOID:\n"
                        "- Do NOT use dpc_retriever for areas outside Italy.\n"
                        "- Do NOT use dpc_retriever with future timestamps.\n"
                        "- Do NOT use meteoblue_retriever for past/historical data.\n"
                        "- Do NOT leave bbox empty when the goal specifies a location — infer it from context.\n"
                        "- Do NOT fabricate product codes or variable names not in the allowed lists.\n"
                    )
                    return Prompt(dict(
                        header="[ROLE and SCOPE]",
                        message=message
                    ))

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    parsed_request_context = RequestParserInstructions.Prompts._ParsedRequest.stable(state)
                    layer_context = LayersAgentPrompts.BasicLayerSummary.stable(state)
                    shapes_context = LayersAgentPrompts.BasicShapesSummary.stable(state)

                    conversation_context = Prompt(dict(
                        header="[CONVERSATION HISTORY]",
                        message=ContextBuilder.conversation_history(state, max_messages=5)
                    ))

                    goal_context = Prompt(dict(
                        header="[GOAL]",
                        message=state["plan"][state["current_step"]]["goal"]
                    ))

                    nowtime = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

                    message = (
                        f"[CURRENT UTC0 DATETIME] {nowtime}\n"
                        "\n"
                        f"{parsed_request_context.header}\n"
                        f"{parsed_request_context.message}\n"
                        "\n"
                        f"{layer_context.header}\n"
                        f"{layer_context.message}\n"
                        "\n"
                        f"{shapes_context.header}\n"
                        f"{shapes_context.message}\n"
                        "\n"
                        f"{conversation_context.header}\n"
                        f"{conversation_context.message}\n"
                        "\n"
                        f"{goal_context.header}\n"
                        f"{goal_context.message}\n"
                    )

                    return Prompt(dict(
                        header="[GLOBAL CONTEXT]",
                        message=message
                    ))

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "Propose the single tool call required to accomplish the retrieval goal.\n"
                        "\n"
                        "Decision rules:\n"
                        "- Select the tool that matches the temporal and geographic scope of the goal.\n"
                        "- Populate all required parameters. For optional parameters (bbox, time range), "
                        "infer reasonable values from context when not explicitly stated.\n"
                        "- If a required argument (e.g. product code or variable) cannot be inferred, "
                        "do not fabricate a value — propose no tool call.\n"
                        "- Prefer specificity: if the goal mentions a product code (e.g. SRI, SRT24), use it directly.\n"
                    )
                    return Prompt(dict(
                        header="[TASK INSTRUCTION]",
                        message=message
                    ))

        class Invocation:

            class InvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:

                    role_and_scope = SaferCastInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
                    global_context = SaferCastInstructions.InvokeTools.Prompts._GlobalContext.stable(state)
                    task_instruction = SaferCastInstructions.InvokeTools.Prompts._TaskInstruction.stable(state)

                    message = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )

                    return [SystemMessage(content=message)]


    class CorrectToolsInvocation:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SaferCastInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SaferCastInstructions.InvokeTools.Prompts._GlobalContext.stable(state)

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "The previous tool call contained invalid or incomplete arguments. The user has provided corrections in the conversation history.\n"
                        "\n"
                        "Your task:\n"
                        "1. Retrieve the user's corrections from the most recent messages.\n"
                        "2. Apply those corrections to the failing argument(s) only — keep all other arguments unchanged.\n"
                        "3. Re-validate the tool choice: if the goal's temporal or geographic scope has changed, switch to the appropriate tool.\n"
                        "4. Do not propose a tool call if required arguments remain incomplete after applying corrections.\n"
                        "\n"
                        "Propose the corrected tool call.\n"
                    )
                    return Prompt(dict(
                        header="[TASK INSTRUCTION]",
                        message=message
                    ))

        class Invocation:

            class ReInvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:

                    role_and_scope = SaferCastInstructions.CorrectToolsInvocation.Prompts._RoleAndScope.stable(state)
                    global_context = SaferCastInstructions.CorrectToolsInvocation.Prompts._GlobalContext.stable(state)
                    task_instruction = SaferCastInstructions.CorrectToolsInvocation.Prompts._TaskInstruction.stable(state)

                    message = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )

                    return [SystemMessage(content=message)]


    class AutoCorrectToolsInvocation:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SaferCastInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SaferCastInstructions.InvokeTools.Prompts._GlobalContext.stable(state)

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "The previous tool call contained invalid or incomplete arguments. The user has requested automatic correction.\n"
                        "\n"
                        "Your task:\n"
                        "1. Identify which arguments failed validation (listed in the error context).\n"
                        "2. Infer the most plausible correct values using: the goal statement, the layer registry, the parsed user request, and conversation history — in that priority order.\n"
                        "3. Apply corrections to the failing arguments only — keep all other arguments unchanged.\n"
                        "4. Re-validate the tool choice: if temporal/geographic scope requires a different tool, switch accordingly.\n"
                        "5. Do not fabricate product codes, variable names, or bbox values that cannot be reasonably inferred.\n"
                        "\n"
                        "Propose the auto-corrected tool call.\n"
                    )
                    return Prompt(dict(
                        header="[TASK INSTRUCTION]",
                        message=message
                    ))

        class Invocation:

            class AutoReInvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:

                    role_and_scope = SaferCastInstructions.AutoCorrectToolsInvocation.Prompts._RoleAndScope.stable(state)
                    global_context = SaferCastInstructions.AutoCorrectToolsInvocation.Prompts._GlobalContext.stable(state)
                    task_instruction = SaferCastInstructions.AutoCorrectToolsInvocation.Prompts._TaskInstruction.stable(state)

                    message = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )

                    return [SystemMessage(content=message)]


    class InvalidInvocationInterrupt:

        class StaticMessage:

            @staticmethod
            def stable(state: MABaseGraphState) -> Prompt:

                def format_invocation_errors(
                    invocation_errors: list,
                ) -> str:
                    tool_name = invocation_errors[0]["tool_name"]
                    error_args = invocation_errors[0]["error_args"]

                    lines = [f"⚠️ Errori di validazione per il tool {tool_name}", ""]

                    for arg_name, error_msg in error_args.items():
                        lines.append(f"    - {arg_name}: {error_msg}")

                    lines.append("")
                    lines.append("Rispondi:")
                    lines.append("  ✏️ fornisci i valori corretti")
                    lines.append('  🔧 "correggi" per correzione automatica')
                    lines.append('  ⏭️ "salta" per rimuovere il tool problematico')
                    lines.append('  ❌ "annulla" per cancellare')

                    return "\n".join(lines)

                invocation_errors = state["retriever_invocation_errors"]
                message = format_invocation_errors(invocation_errors)

                return Prompt(dict(
                    header="[INVALID INVOCATION]",
                    message=message
                ))

        class LLMInvalidInvocationInterrupt:

            class Invocation:

                class NotifyOneShot:

                    @staticmethod
                    def stable(state: MABaseGraphState) -> list:
                        static_message = SaferCastInstructions.InvalidInvocationInterrupt.StaticMessage.stable(state)

                        system_prompt = (
                            "You are a conversational assistant presenting a tool validation error to the user.\n"
                            "Convert the structured error report below into a short, conversational message in flowing prose.\n"
                            "Rules:\n"
                            "- Do NOT use bullet lists, emoji, or structured formatting — write full sentences only.\n"
                            "- Keep all parameter names and error details intact.\n"
                            "- Use the same language as the user's conversation.\n"
                            "- Close with a single natural-language sentence summarising the four available actions: "
                            "provide the correct values, request automatic correction, skip the failing tool, or cancel.\n"
                            "- Do NOT add any information that is not in the original report.\n"
                        )

                        return [
                            SystemMessage(content=system_prompt),
                            HumanMessage(content=static_message.message),
                        ]


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
