"""Final responder prompts for generating user-facing responses."""

import datetime

from typing import Dict, Any

from langchain_core.messages import SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from .map_agent_prompts import MapAgentPrompts
from .request_parser_prompts import RequestParserInstructions


class FinalResponderInstructions:

    class GenerateResponse:
        """Scenario: plan executed (fully or partially) — report results to the user."""

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a geospatial AI assistant on the SaferPlaces platform.\n"
                        "Your task is to generate the final response to the user after an execution plan has run.\n"
                        "You do NOT execute tools. You synthesize results and communicate clearly.\n"
                        "\n"
                        "Rules:\n"
                        "- Respond in the same language as the user's original request.\n"
                        "- Base your answer strictly on the context and conversation provided — do not invent data.\n"
                        "- Be concise and helpful.\n"
                    )
                    return Prompt(dict(header="[ROLE and SCOPE]", message=message))

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    parsed_request_context = RequestParserInstructions.Prompts._ParsedRequest.stable(state)
                    layer_context = LayersAgentPrompts.BasicLayerSummary.stable(state)
                    shapes_context = LayersAgentPrompts.BasicShapesSummary.stable(state)
                    map_context = Prompt(dict(
                        header="[MAP CONTEXT]",
                        message=MapAgentPrompts._viewport_context(state)
                    ))
                    conversation_context = Prompt(dict(
                        header="[CONVERSATION HISTORY]",
                        message=ContextBuilder.conversation_history(state, max_messages=10)
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
                    )
                    return Prompt(dict(header="[GLOBAL CONTEXT]", message=message))

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "Summarize what was accomplished based on the conversation history:\n"
                        "- Which steps completed successfully and what they produced.\n"
                        "- If new layers were created, inform the user they are visible on the map.\n"
                        "- If any step failed, explain it briefly in non-technical terms.\n"
                        "- Suggest one or two concrete next steps the user could take.\n"
                    )
                    return Prompt(dict(header="[TASK INSTRUCTION]", message=message))

        class Invocation:

            class RespondOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role_and_scope = FinalResponderInstructions.GenerateResponse.Prompts._RoleAndScope.stable(state)
                    global_context = FinalResponderInstructions.GenerateResponse.Prompts._GlobalContext.stable(state)
                    task_instruction = FinalResponderInstructions.GenerateResponse.Prompts._TaskInstruction.stable(state)

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

    class GenerateInfoResponse:
        """Scenario: empty plan — user asked a question, no actions were executed."""

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a geospatial AI assistant on the SaferPlaces platform.\n"
                        "No actions were executed. Your task is to answer the user's question directly.\n"
                        "\n"
                        "Platform capabilities:\n"
                        "- Flood simulation (SaferRain): simulate flooding from rainfall input. Requires a DEM.\n"
                        "- Digital Twin creation: generate DEM and base layers for any area from a bounding box.\n"
                        "- DPC meteorological data (Italy only, past/recent): radar rainfall, precipitation, temperature.\n"
                        "- Meteoblue weather forecasts (global, future up to 14 days): precipitation, wind, temperature.\n"
                        "- Flooded buildings detection (SaferBuildings): requires a water depth raster.\n"
                        "- Wildfire simulation (SaferFire): requires a DEM and ignition sources.\n"
                        "\n"
                        "Rules:\n"
                        "- Respond in the same language as the user's original request.\n"
                        "- Base your answer strictly on the context and conversation provided — do not invent data.\n"
                        "- Be concise and helpful.\n"
                    )
                    return Prompt(dict(header="[ROLE and SCOPE]", message=message))

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return FinalResponderInstructions.GenerateResponse.Prompts._GlobalContext.stable(state)

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "Answer the user's question directly using the available context.\n"
                        "If the user could benefit from a platform action (simulation, data retrieval), suggest it.\n"
                    )
                    return Prompt(dict(header="[TASK INSTRUCTION]", message=message))

        class Invocation:

            class RespondOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role_and_scope = FinalResponderInstructions.GenerateInfoResponse.Prompts._RoleAndScope.stable(state)
                    global_context = FinalResponderInstructions.GenerateInfoResponse.Prompts._GlobalContext.stable(state)
                    task_instruction = FinalResponderInstructions.GenerateInfoResponse.Prompts._TaskInstruction.stable(state)

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

    class GenerateAbortResponse:
        """Scenario: plan aborted by the user."""

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return FinalResponderInstructions.GenerateResponse.Prompts._RoleAndScope.stable(state)

            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return FinalResponderInstructions.GenerateResponse.Prompts._GlobalContext.stable(state)

            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "The user cancelled the execution plan.\n"
                        "- Acknowledge the cancellation politely.\n"
                        "- If any steps completed before the abort, briefly summarize what was accomplished.\n"
                        "- Offer to help with a different approach or a new request.\n"
                    )
                    return Prompt(dict(header="[TASK INSTRUCTION]", message=message))

        class Invocation:

            class RespondOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role_and_scope = FinalResponderInstructions.GenerateAbortResponse.Prompts._RoleAndScope.stable(state)
                    global_context = FinalResponderInstructions.GenerateAbortResponse.Prompts._GlobalContext.stable(state)
                    task_instruction = FinalResponderInstructions.GenerateAbortResponse.Prompts._TaskInstruction.stable(state)

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
