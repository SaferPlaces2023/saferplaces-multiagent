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