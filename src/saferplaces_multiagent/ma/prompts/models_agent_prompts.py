"""Models Agent prompts for environmental simulations and model orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json
import datetime

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from .request_parser_prompts import RequestParserInstructions
from .map_agent_prompts import MapAgentPrompts

from ...common.utils import get_conversation_context as _get_conversation_context



class ModelsInstructions:

    class InvokeTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a simulation specialist operating within the SaferPlaces platform.\n"
                        "Your task is to select and configure the correct tool(s) to accomplish a specific simulation goal.\n"
                        "You produce tool call arguments only — you do not interpret results, generate narratives, or communicate directly with the user.\n"
                        "\n"
                        "AVAILABLE TOOLS (summary):\n"
                        "- digital_twin: generates base geospatial layers (DEM, buildings, land use, etc.) for a bounding box.\n"
                        "- safer_rain: runs a flood depth simulation on a DEM using a rainfall input (uniform mm or raster).\n"
                        "- saferbuildings_tool: detects flooded buildings by intersecting a water depth raster with building footprints.\n"
                        "- safer_fire_tool: simulates wildland fire propagation over a DEM using wind and ignition inputs.\n"
                        "\n"
                        "PRECONDITION RULES:\n"
                        "- safer_rain requires an existing DEM layer. If none is available, call digital_twin first.\n"
                        "- saferbuildings_tool requires an existing water depth raster. If none is available, call safer_rain first.\n"
                        "- safer_fire_tool requires an existing DEM and an ignitions layer.\n"
                        "- Always use the `src` value from the layer registry when referencing existing layers.\n"
                        "- If a required input is unavailable and cannot be inferred, do not fabricate it — propose no tool call.\n"
                    )

                    return Prompt(dict(
                        header = "[ROLE and SCOPE]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a flood simulation specialist for SaferPlaces.\n"
                        "You operate the SaferRain hydraulic model.\n"
                        "Your task is to propose one tool call that configures and runs a flood simulation\n"
                        "to accomplish a given goal. You do NOT interpret results or communicate with the user.\n"
                        "\n"
                        "Key concepts:\n"
                        "\n"
                        "- A simulation requires: a DEM layer, a rainfall scenario (intensity + duration), output resolution.\n"
                        "- Rainfall input can come from: radar data (already fetched), manual scenario, or Meteoblue forecast.\n"
                        "- You must verify that required input layers are available before proposing a run.\n"
                    )

                    return Prompt(dict(
                        header = "[ROLE and SCOPE]",
                        message = message
                    ))

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    parsed_request_context = RequestParserInstructions.Prompts._ParsedRequest.stable(state)

                    layer_context = LayersAgentPrompts.BasicLayerSummary.stable(state)
                    shapes_context = LayersAgentPrompts.BasicShapesSummary.stable(state)

                    map_context = Prompt(dict(
                        header = "[MAP CONTEXT]",
                        message = MapAgentPrompts._viewport_context(state)
                    ))

                    conversation_context = Prompt(dict(
                        header = "[CONVERSATION HISTORY]",
                        message = ContextBuilder.conversation_history(state, max_messages=5)
                    ))

                    goal_context = Prompt(dict(
                        header = "[GOAL]",
                        message = state['plan'][state['current_step']]['goal']
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
                        f"{map_context.header}\n"
                        f"{map_context.message}\n"
                        "\n"
                        f"{conversation_context.header}\n"
                        f"{conversation_context.message}\n"
                        "\n"
                        f"{goal_context.header}\n"
                        f"{goal_context.message}\n"
                    )

                    return Prompt(dict(
                        header = "[GLOBAL CONTEXT]",
                        message = message
                    ))

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Propose the minimal set of tool calls required to accomplish the goal.\n"
                        "\n"
                        "Decision rules:\n"
                        "- If all required inputs are available in the layer registry: propose the target tool directly.\n"
                        "- If a prerequisite layer is missing: propose the preparation tool first, then the target tool.\n"
                        "- If multiple required inputs are missing and cannot all be produced in one step: propose only the first missing prerequisite and let the orchestrator schedule subsequent steps.\n"
                        "- If the goal cannot be accomplished with the available tools and inputs: propose no tool call.\n"
                        "\n"
                        "For each tool call, populate all required parameters. Leave optional parameters unset unless the goal explicitly specifies them.\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Propose the necessary tool calls to run the simulation.\n"
                        "Verify preconditions: if a required input layer is missing, call the appropriate\n"
                        "preparation tool first. Do not run the simulation if inputs are incomplete."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

        class Invocation:

            class InvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = ModelsInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
                    global_context = ModelsInstructions.InvokeTools.Prompts._GlobalContext.stable(state)
                    task_instruction = ModelsInstructions.InvokeTools.Prompts._TaskInstruction.stable(state)

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

                    return [ SystemMessage(content=message) ]

    
    class CorrectToolsInvocation:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return ModelsInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
    
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return ModelsInstructions.InvokeTools.Prompts._GlobalContext.stable(state)
          

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    message = (
                        "The previous tool call contained invalid or incomplete arguments. The user has provided corrections in the conversation history.\n"
                        "\n"
                        "Your task:\n"
                        "1. Retrieve the user's corrections from the most recent messages.\n"
                        "2. Apply those corrections to the failing argument(s) only — keep all other arguments unchanged.\n"
                        "3. Re-validate preconditions: if a required input layer is still missing after corrections, call the appropriate preparation tool first.\n"
                        "4. Do not run the simulation if required inputs remain incomplete after applying corrections.\n"
                        "\n"
                        "Propose the corrected tool call(s).\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Correct the tool calls according user provided indications.\n"
                        "Verify preconditions: if a required input layer is missing, call the appropriate\n"
                        "preparation tool first. Do not run the simulation if inputs are incomplete."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

        class Invocation:

            class ReInvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = ModelsInstructions.CorrectToolsInvocation.Prompts._RoleAndScope.stable(state)
                    global_context = ModelsInstructions.CorrectToolsInvocation.Prompts._GlobalContext.stable(state)
                    task_instruction = ModelsInstructions.CorrectToolsInvocation.Prompts._TaskInstruction.stable(state)

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

                    return [ SystemMessage(content=message) ]


    class AutoCorrectToolsInvocation:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return ModelsInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
    
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return ModelsInstructions.InvokeTools.Prompts._GlobalContext.stable(state)
          

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
                        "4. Re-validate preconditions: if a required input layer is missing, call the appropriate preparation tool first.\n"
                        "5. Do not fabricate layer references or numerical values that cannot be reasonably inferred.\n"
                        "\n"
                        "Propose the auto-corrected tool call(s).\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Correct the tool calls basing on your knowledge according the user desire.\n"
                        "Verify preconditions: if a required input layer is missing, call the appropriate\n"
                        "preparation tool first. Do not run the simulation if inputs are incomplete."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
                
        class Invocation:

            class AutoReInvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = ModelsInstructions.AutoCorrectToolsInvocation.Prompts._RoleAndScope.stable(state)
                    global_context = ModelsInstructions.AutoCorrectToolsInvocation.Prompts._GlobalContext.stable(state)
                    task_instruction = ModelsInstructions.AutoCorrectToolsInvocation.Prompts._TaskInstruction.stable(state)

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

                    return [ SystemMessage(content=message) ]




    class InvalidInvocationInterrupt:

        class StaticMessage:

            @staticmethod
            def stable(state: MABaseGraphState) -> Prompt:

                def format_invocation_errors(
                    invocation_errors: Dict[str, str],
                ) -> str:
                    """Build a deterministic message showing validation errors to the user.
                    
                    Args:
                        validation_errors: {arg_name: error_message}
                        
                    Returns:
                        Formatted error report string.
                    """
                    tool_name = invocation_errors[0]['tool_name']
                    error_args = invocation_errors[0]['error_args']

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
                
                invocation_errors = state['models_invocation_errors']

                message = format_invocation_errors(invocation_errors)
        
                return Prompt(dict(
                    header = "[INVALID INVOCATION]",
                    message = message
                ))

        class LLMInvalidInvocationInterrupt:

            class Invocation:

                class NotifyOneShot:

                    @staticmethod
                    def stable(state: MABaseGraphState) -> list:
                        static_message = ModelsInstructions.InvalidInvocationInterrupt.StaticMessage.stable(state)

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
