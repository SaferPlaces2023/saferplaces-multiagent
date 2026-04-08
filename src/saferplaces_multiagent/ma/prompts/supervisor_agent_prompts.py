"""Supervisor agent prompts for orchestration."""

from typing import Any, List, Dict

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

import json
import datetime

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder
from ..names import NodeNames

# from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
# from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION
# from ..specialized.map_agent import MAP_AGENT_DESCRIPTION

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from .request_parser_prompts import RequestParserInstructions

from ...common.utils import get_conversation_context as _get_conversation_context


class SupervisorInstructions:

    class PlanGeneration:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState, *args, **kwds) -> Prompt:

                    message = (
                        "You are the orchestrator of a multi-agent AI system for flood risk analysis on the SaferPlaces platform.\n"
                        "Your role is to decompose a user request into an ordered list of atomic steps, each assigned to exactly one specialized agent.\n"
                        "\n"
                        "AVAILABLE AGENTS:\n"
                        "- retriever_agent: retrieves observational rainfall data (DPC radar) and weather forecasts (Meteoblue).\n"
                        "- models_agent: creates digital twin base layers (DEM, buildings, land use) or runs flood/fire simulations (SaferRain, SaferFire). Requires input layers to already exist.\n"
                        "- map_agent: support agent for map frontend interactions — moves the viewport, generates layer symbology styles (MapLibre GL JS), "
                        "and registers shapes drawn by the user. Does NOT run simulations, retrieve data, or modify the layer registry.\n"
                        "  Use map_agent when: the user wants to navigate/zoom the map, change visual appearance of a layer, "
                        "register a drawn shape, or query the current map view or shapes context.\n"
                        "- layers_agent: support agent that reads, queries and updates the layer registry. Does NOT run simulations or retrieve external data.\n"
                        "  Use layers_agent when: (a) the user asks about available layers, layer metadata, or layer status; "
                        "(b) a required layer attribute (bbox, src, type, metadata) is not already present in the current context summary. "
                        "Do NOT add a layers_agent step if the layer information is already visible in the [AVAILABLE LAYERS] context.\n"
                        "\n"
                        "RULES:\n"
                        "1. Each step references exactly one agent from the list above.\n"
                        "2. Split tasks into atomic steps when a single agent handles multiple independent sub-goals.\n"
                        "3. Preserve data dependencies: a step that requires output from a previous step must appear after it (e.g., fetch radar data BEFORE running SaferRain).\n"
                        "4. If the request requires no agent action (conversational, out-of-scope, or already answered), output an empty plan [].\n"
                        "5. Never fabricate agents or actions outside the platform's documented capabilities.\n"
                        "6. layers_agent is a support step — prefer it only when layer information is genuinely missing from context. Never use it redundantly.\n"
                    )

                    return Prompt(dict(
                        header = "[ROLE and SCOPE]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState, *args, **kwds) -> Prompt:

                    message = (
                        "You are the orchestrator of a multi-agent system for flood risk analysis.\n"
                        "You receive a parsed user request and must produce an ordered execution plan that delegates work\n"
                        "to specialized sub-agents. You reason step by step before producing the plan.\n"
                        "\n"
                        "Available agents:\n"
                        "- retriever_agent: fetches observational rainfall data (DPC radar) and weather forecasts (Meteoblue)\n"
                        "- models_agent: generate digital twins layers or runs meteorological simulation (SaferRain) on a given scenario\n"
                        "- map_agent: support agent for map frontend — viewport navigation, layer symbology styles, shape registration. Does NOT run simulations or modify layer data.\n"
                        "- layers_agent: support agent — reads, queries and updates the layer registry without running simulations or fetching external data. "
                        "Use it when the user asks about existing layers or when a required layer attribute is not already available in context.\n"
                        "\n"
                        "Rules:\n"
                        "- A plan step can only reference one of the agents above.\n"
                        "- If one agent can be used for multiple sub-tasks, split the task in multiple atomic steps.\n"
                        "- Order steps so that dependencies are respected (e.g. retrieve data before running a model).\n"
                        "- If a step requires data from a previous step, ensure the previous step is included in the plan.\n"
                        "- If the request is self-contained and needs no sub-agent, produce an empty plan [].\n"
                        "- Never include actions outside the platform's scope.\n"
                        "- Use layers_agent only when layer information is genuinely missing from the current context — do not add it redundantly.\n"
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

                    # map_context = MapAgentPrompts.MapContext

                    conversation_context = Prompt(dict(
                        header = "[CONVERSATION HISTORY]",
                        message = ContextBuilder.conversation_history(state, max_messages=5)
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
                    )

                    return Prompt(dict(
                        header = "[GLOBAL CONTEXT]",
                        message = message
                    ))
                
            class _TaskInstruction:
                
                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Reason step by step before producing the plan:\n"
                        "\n"
                        "1. What is the user's ultimate goal? Identify the concrete deliverable they expect.\n"
                        "2. Is the user's goal something that can be achieved with the available agents and resources?\n"
                        "3. What data or layers are currently available? Check the layer registry and conversation history.\n"
                        "4. What prerequisites are missing and which agent must produce them first?\n"
                        "5. Which agent is best suited for each remaining sub-task?\n"
                        "6. Are there ambiguities that would block execution? If yes, simplify or remove the ambiguous step — do not guess.\n"
                        "\n"
                        "Then output the ordered plan. Each step must specify: agent name and a precise, self-contained goal statement.\n"
                        "You can output an empty plan if no valid steps can be determined."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:

                    message = (
                        "Think step by step:\n"
                        "1. What is the user ultimately trying to achieve?\n"
                        "2. What data or preconditions are needed before each step?\n"
                        "3. Which agent is best suited for each sub-task?\n"
                        "4. Are there any ambiguities that would block execution?\n"
                        "\n"
                        "Then output the plan."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
                
        class Invocations:
        
            class PlanOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanGeneration.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanGeneration.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanGeneration.Prompts._TaskInstruction.stable(state)

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
                
            class PlanMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanGeneration.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanGeneration.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanGeneration.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]

    
    class PlanModification:
        
        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanGeneration.Prompts._RoleAndScope.stable(state)
                
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanGeneration.Prompts._GlobalContext.stable(state)
                
            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "The user has requested a modification to the current execution plan.\n"
                        "\n"
                        "Reason step by step:\n"
                        "\n"
                        "1. What is the user's intended change? Identify precisely which step(s) must be added, replaced, or removed.\n"
                        "2. Does the requested change affect downstream steps (dependencies, ordering)?\n"
                        "3. Is the modified plan still internally consistent and executable with the available agents?\n"
                        "4. Are there ambiguities in the requested change? If yes, apply the most conservative interpretation.\n"
                        "\n"
                        "Then output the complete revised plan from step 0, incorporating all changes. Do not output only the delta.\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        "Think step by step:\n"
                        "1. What is the user ultimately trying to achieve?\n"
                        "2. Which steps need to be modified, replaced, or removed?\n"
                        "3. Are there any ambiguities that would block desired modification?\n"
                        "\n"
                        "Then output the new plan."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
                
        class Invocations:

            class ReplanOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModification.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModification.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModification.Prompts._TaskInstruction.stable(state)

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
                
            class ReplanMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModification.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModification.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModification.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]
                

    class PlanModificationDueStepNoTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._RoleAndScope.stable(state)
                
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._GlobalContext.stable(state)
                
            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, step {state['current_step']} assigned to {state['plan'][state['current_step']]['agent']} (goal: \"{state['plan'][state['current_step']]['goal']}\") returned no tool calls.\n"
                        "This means the agent could not identify a valid tool to accomplish the goal with the data currently available.\n"
                        "\n"
                        "Reason step by step:\n"
                        "\n"
                        "1. Is this agent actually capable of performing the stated goal? Verify against known agent capabilities.\n"
                        "2. Is the goal underspecified, ambiguous, or missing a required input (e.g., a layer that does not exist yet)?\n"
                        "3. Can the goal be reformulated, split, or assigned to a different agent to become executable?\n"
                        "4. If no fix is possible with available information, should the plan be aborted (output []) so the system can ask the user for missing details?\n"
                        "\n"
                        "Then output the corrected plan, or an empty plan [] if additional user input is required before proceeding."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, of step {state['current_step']} with agent {state['plan'][state['current_step']]['agent']} and goal {state['plan'][state['current_step']]['goal']}, no tools were available.\n"
                        "Think step by step:\n"
                        "1. The selected specialized agent is really capable of performing the task?\n"
                        "2. There are more details that we know to be specified in the task goal?\n"
                        "3. Which steps need to be modified, replaced, or removed?.\n"
                        "\n"
                        "Then output the new plan or an empty plan if more need to ask new details to proceed."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
        
        class Invocations:

            class ReplanDueNoToolsOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._TaskInstruction.stable(state)

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
                
            class ReplanDueNoToolsMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepNoTools.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]
                

    
    class PlanModificationDueStepSkip:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._RoleAndScope.stable(state)
                
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._GlobalContext.stable(state)
                
            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"The user chose to skip step {state['current_step']} assigned to {state['plan'][state['current_step']]['agent']} (goal: \"{state['plan'][state['current_step']]['goal']}\").\n"
                        "The skipped step's output will NOT be available for any subsequent steps.\n"
                        "\n"
                        "Reason step by step:\n"
                        "\n"
                        "1. Which subsequent steps depend (directly or indirectly) on the output of the skipped step?\n"
                        "2. For each dependent step: can it be adapted to work without that output, or must it be removed?\n"
                        "3. Do any remaining steps still form a coherent, executable sequence?\n"
                        "\n"
                        "Then output the revised plan with the skipped step removed and any necessary adjustments to downstream steps.\n"
                        "If no remaining steps can execute without the skipped step's output, output an empty plan [].\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, of step {state['current_step']} with agent {state['plan'][state['current_step']]['agent']} and goal {state['plan'][state['current_step']]['goal']}, user chose to skip the step.\n"
                        "Think step by step:\n"
                        "1. Can next steps, if any, be executed without the skipped step?\n"
                        "2. Which steps need to be modified, replaced, or removed?.\n"
                        "\n"
                        "Then output the new plan or an empty plan if no steps can be executed without the skipped step."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
        
        class Invocations:

            class ReplanDueSkipOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._TaskInstruction.stable(state)

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
                
            class ReplanDueSkipMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepSkip.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]



    class PlanModificationDueStepError:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._RoleAndScope.stable(state)
                
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanModification.Prompts._GlobalContext.stable(state)
                
            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, step {state['current_step']} assigned to {state['plan'][state['current_step']]['agent']} (goal: \"{state['plan'][state['current_step']]['goal']}\") failed with an error.\n"
                        "The error details are visible in the conversation history.\n"
                        "\n"
                        "Decide:\n"
                        "- Can the user's original goal still be achieved through an alternative plan that avoids the failed step? If yes, output the revised plan.\n"
                        "- Is the failed step blocking — meaning the original goal cannot be achieved without it? If yes, output an empty plan [].\n"
                        "\n"
                        "Do not introduce steps unrelated to the original user goal.\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def stable_backup(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, step {state['current_step']} assigned to {state['plan'][state['current_step']]['agent']} (goal: \"{state['plan'][state['current_step']]['goal']}\") failed with an error.\n"
                        "The error details are provided in the [ERROR DETAILS] section of the context.\n"
                        "\n"
                        "Reason step by step:\n"
                        "\n"
                        "1. What is the root cause of the error? Is it a missing input, an invalid parameter, or a service failure?\n"
                        "2. Is the error transient (retry may succeed) or structural (the step cannot succeed with current inputs)?\n"
                        "3. Do subsequent steps depend on the output of this failed step?\n"
                        "4. What is the best recovery action: fix and retry the step, replace it with an alternative, remove the step, or abort the plan?\n"
                        "\n"
                        "Then output the revised plan that accounts for the error. If recovery is not possible, output an empty plan [].\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        f"During plan execution, the step {state['current_step']} with agent {state['plan'][state['current_step']]['agent']} and goal {state['plan'][state['current_step']]['goal']}, couldn't be completed due to an error.\n"
                        "Think step by step:\n"
                        "1. What is the cause of the error or issue?\n"
                        "2. Does this error affect subsequent steps in a blocking way?\n"
                        "3. Is there a workaround or steps that need to be modified, replaced, or removed?\n"
                        "4. In case the error is not immediately resolvable, there are alternative actions that can be taken or is it better to abort?\n"
                        "\n"
                        "Then output the new plan, if possible, otherwise output an empty plan."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
        
        class Invocations:

            class ReplanDueErrorOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepError.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepError.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepError.Prompts._TaskInstruction.stable(state)

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
                
            class ReplanDueErrorMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanModificationDueStepError.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanModificationDueStepError.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanModificationDueStepError.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]
        



    class PlanClarification:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanGeneration.Prompts._RoleAndScope.stable(state)
                
            class _GlobalContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return SupervisorInstructions.PlanGeneration.Prompts._GlobalContext.stable(state)
                
            class _TaskInstruction:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "The user has asked a question or requested clarification about the proposed execution plan.\n"
                        "Your response will be shown directly to the user — write in clear, concise language without technical jargon.\n"
                        "\n"
                        "Reason step by step:\n"
                        "\n"
                        "1. What specific aspect of the plan is the user asking about?\n"
                        "2. Why was each relevant step included? What outcome does it produce?\n"
                        "3. Are there alternative approaches? If yes, briefly note why the current plan was chosen.\n"
                        "\n"
                        "Then provide a direct, user-facing explanation that answers the question and ends by asking whether they wish to proceed with the plan.\n"
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))

                @staticmethod
                def generic(state: MABaseGraphState) -> Prompt:
                    message = (
                        "Think step by step:\n"
                        "1. What is the user ultimately trying to achieve?\n"
                        "2. What the current plan is about?\n"
                        "3. Which details need to be clarified or expanded?\n"
                        "\n"
                        "Then explain the requested details and provide any additional context."
                    )
            
                    return Prompt(dict(
                        header = "[TASK INSTRUCTION]",
                        message = message
                    ))
                
        class Invocations:

            class PlanClarifyOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanClarification.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanClarification.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanClarification.Prompts._TaskInstruction.stable(state)

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
                
            class PlanClarifyMultiPrompt:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:

                    role_and_scope = SupervisorInstructions.PlanClarification.Prompts._RoleAndScope.stable(state)
                    global_context = SupervisorInstructions.PlanClarification.Prompts._GlobalContext.stable(state)
                    task_instruction = SupervisorInstructions.PlanClarification.Prompts._TaskInstruction.stable(state)

                    system_prompt = (
                        f"{role_and_scope.header}\n"
                        f"{role_and_scope.message}\n"
                        "\n"
                        f"{global_context.header}\n"
                        f"{global_context.message}\n"
                        "\n"
                        f"{task_instruction.header}\n"
                        f"{task_instruction.message}\n"
                    )
                    user_prompt = state['messages'][-1].content

                    return [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt)
                    ]


    class PlanConfirmation:

        class ConfirmationInterrupt:

            class StaticMessage:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    
                    def format_plan_confirmation(plan: List[Dict[str, Any]], parsed_request: dict = None) -> str:
                        """Build a deterministic confirmation message for an execution plan.
                        
                        Args:
                            plan: List of dicts with keys 'agent' and 'goal'.
                            parsed_request: Optional parsed request dict with parameters.
                            
                        Returns:
                            Formatted confirmation string ready for the user.
                        """
                        AGENT_LABELS = {
                            "models_subgraph": "Simulazione",
                            "retriever_subgraph": "Recupero dati",
                            "layers_agent": "Gestione layer",
                            "digital_twin_agent": "Digital Twin",
                            "operational_agent": "Operazioni",
                        }

                        n = len(plan)
                        lines = [f"📋 Piano di esecuzione ({n} step):", ""]

                        for i, step in enumerate(plan, 1):
                            label = AGENT_LABELS.get(step.get("agent", "unknown"), step.get("agent", "unknown"))
                            goal = step.get("goal", "")
                            lines.append(f"  {i}. [{label}] {goal}")

                        # Show extracted parameters if available
                        if parsed_request:
                            params = parsed_request.get("parameters", {})
                            if params:
                                non_null = {k: v for k, v in params.items() if v is not None}
                                if non_null:
                                    lines.append("")
                                    lines.append("📎 Parametri rilevati:")
                                    for k, v in non_null.items():
                                        lines.append(f"  • {k}: {v}")

                        lines.append("")
                        lines.append("Rispondi:")
                        lines.append('  ✓ "ok" per procedere')
                        lines.append("  ✏️ descrivi le modifiche desiderate")
                        lines.append('  ❌ "annulla" per cancellare')

                        return "\n".join(lines)
                    
                    message = format_plan_confirmation(state['plan'], state.get('parsed_request'))

                    return Prompt(dict(
                        message = message
                    ))

        class LLMConfirmationInterrupt:
            """Parallel to ConfirmationInterrupt — feeds the static plan summary to an LLM
            so the confirmation message is rendered in fluent natural language."""

            class Invocation:

                class ConfirmOneShot:

                    @staticmethod
                    def stable(state: MABaseGraphState) -> list:
                        static_message = SupervisorInstructions.PlanConfirmation.ConfirmationInterrupt.StaticMessage.stable(state)

                        system_prompt = (
                            "You are a conversational assistant presenting an execution plan to the user.\n"
                            "Rewrite the plan summary below in clear, fluent natural language.\n"
                            "\n"
                            "Rules:\n"
                            "- Keep all steps and parameters — do not omit or invent anything.\n"
                            "- Use the same language as the user's conversation.\n"
                            "- End with a short, friendly question asking the user whether to proceed.\n"
                        )

                        return [
                            SystemMessage(content=system_prompt),
                            HumanMessage(content=static_message.message),
                        ]