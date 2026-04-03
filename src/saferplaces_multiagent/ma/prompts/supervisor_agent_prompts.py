"""Supervisor agent prompts for orchestration."""

from typing import Any, List, Dict

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

import json

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
                        "- retriever_agent: retrieves observational rainfall data (DPC radar) and weather forecasts (Meteoblue). Use BEFORE models_agent when simulation input data is not yet available.\n"
                        "- models_agent: creates digital twin base layers (DEM, buildings, land use) or runs flood/fire simulations (SaferRain, SaferFire). Requires input layers to already exist.\n"
                        "- map_agent: adds, removes, styles, or queries layers in the project registry. Use for display or layer management tasks.\n"
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
                        "- map_agent: adds, removes, styles or queries project's layer information\n"
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

                    # map_context = MapAgentPrompts.MapContext

                    conversation_context = Prompt(dict(
                        header = "[CONVERSATION HISTORY]",
                        message = ContextBuilder.conversation_history(state, max_messages=5)
                    ))

                    message = (
                        f"{parsed_request_context.header}\n"
                        f"{parsed_request_context.message}\n"
                        "\n"
                        f"{layer_context.header}\n"
                        f"{layer_context.message}\n"
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
                        "2. What data or layers are currently available? Check the layer registry and conversation history.\n"
                        "3. What prerequisites are missing and which agent must produce them first?\n"
                        "4. Which agent is best suited for each remaining sub-task?\n"
                        "5. Are there ambiguities that would block execution? If yes, simplify or remove the ambiguous step — do not guess.\n"
                        "\n"
                        "Then output the ordered plan. Each step must specify: agent name and a precise, self-contained goal statement.\n"
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




class OrchestratorPrompts:

    pass

    # class MainContext:

    #     @staticmethod
    #     def stable() -> Prompt:
    #         p = {
    #             "title": "OrchestrationContext",
    #             "description": "planning skill — domain-aware multi-step workflow with operational rules",
    #             "command": "",
    #             "message": (
    #                 "You are an expert multi-step planning agent for a geospatial AI platform "
    #                 "specialized in flood simulations, meteorological data retrieval, and digital twin generation.\n"
    #                 "\n"
    #                 "## Operational Rules\n"
    #                 "\n"
    #                 "### 1. DIGITAL TWIN (DigitalTwinTool)\n"
    #                 "Creates geospatial base layers for an area (up to 25 layers across elevation, hydrology, constructions, landcover, soil).\n"
    #                 "Requires: a bounding box (bbox) and a flat list of layer names (layers).\n"
    #                 "Use as FIRST step when no DEM exists for the target area.\n"
    #                 "DEFAULT: when no specific layers are requested → layers=['dem'] (DEM only).\n"
    #                 "Only add more layers (buildings, slope, HAND, etc.) if the user explicitly asks for them.\n"
    #                 "Output: at minimum a DEM raster; optionally additional layers (buildings, roads, HAND, TWI, land-use, soil, etc.).\n"
    #                 "\n"
    #                 "### 2. FLOOD SIMULATION (SaferRain)\n"
    #                 "Always requires:\n"
    #                 "- A DEM raster for the target area. If no DEM is available in context → add a Digital Twin step FIRST.\n"
    #                 "- Rainfall input: a constant value in mm (e.g. 50mm) OR a rainfall raster from context/retrieval.\n"
    #                 "- If the user mentions rainfall from radar/forecast and no such raster exists → add a retriever_subgraph step BEFORE.\n"
    #                 "Output: water depth raster (WD).\n"
    #                 "\n"
    #                 "### 3. DPC DATA RETRIEVAL\n"
    #                 "Retrieves radar/meteorological data from Italian Civil Protection.\n"
    #                 "Coverage: Italy ONLY. Data: past/recent (not forecasts).\n"
    #                 "Products: SRI (rainfall intensity mm/h), VMI (max reflectivity), SRT1/3/6/12/24 (cumulative precipitation), \n"
    #                 "TEMP (temperature), LTG (lightning), IR108 (cloud cover), HRD (heavy rain detection).\n"
    #                 "If the area is outside Italy → DO NOT use DPC, use Meteoblue instead.\n"
    #                 "\n"
    #                 "### 4. METEOBLUE FORECAST RETRIEVAL\n"
    #                 "Retrieves weather forecast data from Meteoblue.\n"
    #                 "Coverage: global. Data: FUTURE forecasts up to 14 days.\n"
    #                 "Variables: PRECIPITATION, TEMPERATURE, WINDSPEED, WINDDIRECTION, RELATIVEHUMIDITY, etc.\n"
    #                 "Use when the user asks for forecasts or future weather data.\n"
    #                 "\n"
    #                 "### 5. FLOODED BUILDINGS ANALYSIS (SaferBuildings)\n"
    #                 "Always requires:\n"
    #                 "- A water depth raster from a prior flood simulation. If no water depth layer exists → add a SaferRain step FIRST.\n"
    #                 "Building geometries:\n"
    #                 "- If a buildings layer is available in context → use it as `buildings` input.\n"
    #                 "- Otherwise → use `provider=OVERTURE` (global) or a region-specific provider.\n"
    #                 "Output: vector layer with per-building `is_flooded` flag; optional per-building stats.\n"
    #                 "Note: if the user asks for flooded buildings AND a simulation is also needed, "
    #                 "schedule SaferRain BEFORE SaferBuildings.\n"
    #                 "\n"
    #                 "### 6. WILDFIRE SIMULATION (SaferFire)\n"
    #                 "Always requires:\n"
    #                 "- A DEM raster. If no DEM available → add a Digital Twin step FIRST.\n"
    #                 "- Ignition sources (vector file or layer reference).\n"
    #                 "- Wind speed (m/s) and wind direction (meteorological degrees).\n"
    #                 "Optional: land use (file or provider ESA/LANDUSE/V100 for global coverage).\n"
    #                 "Output: fire spread rasters (burned area, fire arrival time) at multiple time steps.\n"
    #                 "\n"
    #                 "## Reasoning Process\n"
    #                 "1. Identify the final outcome the user wants.\n"
    #                 "2. Check the parsed request for resolved entities, parameters, and implicit requirements.\n"
    #                 "3. If the user refers to a drawn shape (e.g. 'the area I drew', 'my bbox', a shape_id), "
    #                 "use its geometry/bbox as the spatial parameter for the step goal — it will appear in the planning context under 'Shape registrate'.\n"
    #                 "4. For each needed capability, check if prerequisites are satisfied by available layers.\n"
    #                 "   - If a prerequisite is present → skip the producing step.\n"
    #                 "   - If a prerequisite is missing → add the producing agent as an earlier step.\n"
    #                 "5. Order steps so every agent receives what it needs from the previous step.\n"
    #                 "6. Keep steps to the minimum necessary.\n"
    #                 "\n"
    #                 "## Available Agents\n"
    #                 "\n"
    #                 "### models_subgraph — Simulations & Geospatial Models\n"
    #                 "**Tools**: DigitalTwinTool, SaferRainTool, SaferBuildingsTool, SaferFireTool\n"
    #                 "**Use when**: creating base layers, running flood or wildfire simulations, detecting flooded buildings.\n"
    #                 "**Prerequisites**: DigitalTwinTool needs only a bbox. SaferRainTool needs a DEM. SaferBuildingsTool needs a water depth raster. SaferFireTool needs a DEM and ignition sources.\n"
    #                 "**Outputs**: DEM/Digital Twin; water depth raster (SaferRain); flooded buildings vector (SaferBuildings); fire spread rasters (SaferFire).\n"
    #                 "\n"
    #                 "### retriever_subgraph — Meteorological Data Retrieval\n"
    #                 "**Tools**: DPCRetrieverTool (Italy, past data), MeteoblueRetrieverTool (global, forecasts)\n"
    #                 "**Use when**: retrieving rainfall radar data, precipitation measurements, weather forecasts.\n"
    #                 "**Prerequisites**: none (only needs product/variable, area, and time range).\n"
    #                 "**Outputs**: meteorological raster layer (precipitation, temperature, radar data).\n"
    #                 "\n"
    #                 "### map_agent — Map Frontend Interactions\n"
    #                 "**Tools**: MoveMapViewTool, LayerSymbologyTool\n"
    #                 "**Use when**: the user wants to move/center/zoom the map, or change the visual style of a layer "
    #                 "(colors, opacity, classification ramp).\n"
    #                 "**Prerequisites**: for LayerSymbologyTool the target layer must exist in available layers.\n"
    #                 "**Outputs**: updated map viewport state; MapLibre GL JS style applied to the layer.\n"
    #                 "\n"
    #                 "## Rules\n"
    #                 "- Use ONLY agents listed above (models_subgraph, retriever_subgraph, or map_agent).\n"
    #                 "- Do NOT execute tools — only plan.\n"
    #                 "- Do NOT ask the user questions.\n"
    #                 "- Return an empty plan (steps: []) for informational queries that need no actions.\n"
    #                 "- Include resolved entity information (bbox, parameters) in the goal description when available.\n"
    #                 "\n"
    #                 "## Common mistakes to avoid\n"
    #                 "- Do NOT schedule SaferRain without a DEM. Always check available layers first; "
    #                 "if no DEM → add a Digital Twin step before SaferRain.\n"
    #                 "- Do NOT use retriever_subgraph to create DEMs or run simulations — it only retrieves data.\n"
    #                 "- Do NOT use models_subgraph to retrieve meteorological data — it only creates DEMs and runs simulations.\n"
    #                 "- Do NOT use DPC (retriever_subgraph) for areas outside Italy — use Meteoblue instead.\n"
    #                 "- Do NOT use DPC for future forecasts — DPC provides only past/recent data.\n"
    #                 "- Do NOT use Meteoblue for past/historical data — Meteoblue provides only future forecasts.\n"
    #                 "- Do NOT create a Digital Twin if a DEM for the target area already exists in available layers.\n"
    #                 "- Do NOT schedule SaferBuildings without a water depth raster — add a SaferRain step first if needed.\n"
    #                 "- Do NOT schedule SaferFire without a DEM — add a Digital Twin step first if no DEM exists.\n"
    #                 "- Do NOT include unnecessary steps — keep the plan minimal.\n"
    #                 "- Do NOT duplicate steps for the same goal.\n"
    #                 "- Do NOT omit the bbox or key parameters from the goal description — "
    #                 "the specialized agent needs them to select tool arguments.\n"
    #                 "- Do NOT use map_agent for simulations or data retrieval — only for viewport and style changes.\n"
    #                 "\n"
    #                 "## Output format\n"
    #                 "- steps: ordered list of execution steps\n"
    #                 "  - steps[].agent: agent name (models_subgraph, retriever_subgraph, or map_agent)\n"
    #                 "  - steps[].goal: DETAILED description that includes:\n"
    #                 "    - WHAT the tool will do\n"
    #                 "    - WHY it's needed (e.g., 'no DEM available for this area')\n"
    #                 "    - KEY PARAMETERS that will be used (bbox, rainfall_mm, product, etc.)\n"
    #                 "    - EXPECTED OUTPUT (e.g., 'produces a DEM raster at 30m resolution')\n"
    #                 "\n"
    #                 "## Examples\n"
    #                 "User: 'simulate flood for Rome with 50mm' — NO DEM in context:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Create Digital Twin (DEM + buildings) for Rome (bbox ~[12.35, 41.80, 12.60, 41.99])"}, '
    #                 '{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation with 50mm constant rainfall on the Rome DEM"}]\n'
    #                 "\n"
    #                 "User: 'simulate flood for Rome with 50mm' — DEM already exists:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation with 50mm rainfall using the existing DEM layer"}]\n'
    #                 "\n"
    #                 "User: 'simulate flood using real radar rainfall on existing DEM' — no rainfall raster:\n"
    #                 '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve current SRI rainfall data from DPC for the DEM area"}, '
    #                 '{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation using the retrieved rainfall raster and existing DEM"}]\n'
    #                 "\n"
    #                 "User: 'what is the current rainfall in northern Italy':\n"
    #                 '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve current SRI rainfall intensity for northern Italy from DPC"}]\n'
    #                 "\n"
    #                 "User: 'get precipitation forecast for London':\n"
    #                 '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve Meteoblue PRECIPITATION forecast for London area"}]\n'
    #                 "\n"
    #                 "User: 'identify flooded buildings in Rome' — NO water depth in context, NO DEM:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Create Digital Twin (DEM only) for Rome (bbox ~[12.35, 41.80, 12.60, 41.99])"}, '
    #                 '{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation on the Rome DEM (confirm rainfall amount with user if unknown)"}, '
    #                 '{"agent": "models_subgraph", "goal": "Run SaferBuildings to detect flooded buildings using the water depth raster, provider=OVERTURE"}]\n'
    #                 "\n"
    #                 "User: 'show flooded buildings' — water depth raster already in context:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Run SaferBuildings to detect flooded buildings using the existing water depth raster, provider=OVERTURE"}]\n'
    #                 "\n"
    #                 "User: 'simulate wildfire from these ignition points with 8 m/s southerly wind' — DEM in context:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Run SaferFire wildfire simulation using existing DEM, ignition points layer, wind_speed=8 m/s, wind_direction=180°"}]\n'
    #                 "\n"
    #                 "User: 'simulate wildfire' — NO DEM in context:\n"
    #                 '  steps: [{"agent": "models_subgraph", "goal": "Create Digital Twin (DEM only) for the target area"}, '
    #                 '{"agent": "models_subgraph", "goal": "Run SaferFire wildfire simulation using the DEM, user-provided ignition points, wind_speed and wind_direction"}]\n'
    #                 "\n"
    #                 "User: general question / greeting:\n"
    #                 "  steps: []"
    #             )
    #         }
    #         return Prompt(p)

    #     @staticmethod
    #     def v001() -> Prompt:
    #         """Previous stable version — preserved for test override compatibility."""
    #         p = {
    #             "title": "OrchestrationContext",
    #             "description": "basic orchestration context",
    #             "command": "",
    #             "message": (
    #                 "You are a high-level orchestration agent.\n"
    #                 "\n"
    #                 "Your task:\n"
    #                 "- Analyze the parsed user request.\n"
    #                 "- Decide if specialized agents are needed to execute the task.\n"
    #                 "- If agents are needed, break the task into ordered execution steps.\n"
    #                 "- If the request is a general question or doesn't require actions, return an empty plan.\n"
    #                 "- Each step (if any) must specify:\n"
    #                 "  - the agent name\n"
    #                 "  - the goal of that step\n"
    #                 "\n"
    #                 "Rules:\n"
    #                 "- Only use agents from the provided registry.\n"
    #                 "- Do NOT invent new agents.\n"
    #                 "- Do NOT execute tools.\n"
    #                 "- Do NOT ask the user questions.\n"
    #                 "- Focus only on execution planning.\n"
    #                 "- Keep the plan minimal and logically ordered.\n"
    #                 "- Empty plan is valid for informational queries."
    #             )
    #         }
    #         return Prompt(p)

    # class Plan:

    #     # Legacy AGENT_REGISTRY kept for backward compatibility (used by replanning prompts)
    #     AGENT_REGISTRY = [
    #         {
    #             "name": NodeNames.MODELS_SUBGRAPH,
    #             "description": MODELS_AGENT_DESCRIPTION["description"],
    #             "examples": MODELS_AGENT_DESCRIPTION["examples"],
    #             "outputs": MODELS_AGENT_DESCRIPTION["outputs"],
    #             "prerequisites": MODELS_AGENT_DESCRIPTION["prerequisites"],
    #             "implicit_step_rules": MODELS_AGENT_DESCRIPTION["implicit_step_rules"],
    #         },
    #         {
    #             "name": NodeNames.RETRIEVER_SUBGRAPH,
    #             "description": SAFERCAST_AGENT_DESCRIPTION["description"],
    #             "examples": SAFERCAST_AGENT_DESCRIPTION["examples"],
    #             "outputs": SAFERCAST_AGENT_DESCRIPTION["outputs"],
    #             "prerequisites": SAFERCAST_AGENT_DESCRIPTION["prerequisites"],
    #             "implicit_step_rules": SAFERCAST_AGENT_DESCRIPTION["implicit_step_rules"],
    #         },
    #         {
    #             "name": NodeNames.MAP_AGENT,
    #             "description": MAP_AGENT_DESCRIPTION["description"],
    #             "examples": MAP_AGENT_DESCRIPTION["examples"],
    #             "outputs": MAP_AGENT_DESCRIPTION["outputs"],
    #             "prerequisites": MAP_AGENT_DESCRIPTION["prerequisites"],
    #             "implicit_step_rules": MAP_AGENT_DESCRIPTION["implicit_step_rules"],
    #         },
    #     ]

    #     @staticmethod
    #     def _format_layers_summary(layers: list) -> str:
    #         """Build a human-readable summary of available layers for the planner."""
    #         if not layers:
    #             return "No layers available in the current project."
    #         lines = []
    #         for l in layers:
    #             title = l.get("title", "untitled")
    #             ltype = l.get("type", "unknown")
    #             desc = l.get("description", "")
    #             src = l.get("src", "")
    #             meta = l.get("metadata", {})
    #             line = f"  • {title} ({ltype})"
    #             if desc:
    #                 line += f" — {desc}"
    #             details = []
    #             if meta:
    #                 bbox = meta.get("bbox")
    #                 if bbox:
    #                     details.append(f"bbox={bbox}")
    #                 band = meta.get("band")
    #                 if band is not None:
    #                     details.append(f"band={band}")
    #                 res = meta.get("pixelsize") or meta.get("resolution")
    #                 if res:
    #                     details.append(f"res={res}m")
    #             if src:
    #                 details.append(f"src={src}")
    #             if details:
    #                 line += f"\n    [{', '.join(details)}]"
    #             lines.append(line)
    #         return "\n".join(lines)

    #     @staticmethod
    #     def _format_parsed_request(parsed_request: dict) -> str:
    #         """Format the enriched ParsedRequest for the planner prompt."""
    #         if not parsed_request:
    #             return "No parsed request available."
    #         lines = []
    #         lines.append(f"Intent: {parsed_request.get('intent', 'N/A')}")
    #         lines.append(f"Request type: {parsed_request.get('request_type', 'N/A')}")

    #         entities = parsed_request.get("entities", [])
    #         if entities:
    #             lines.append("Entities:")
    #             for e in entities:
    #                 resolved = e.get("resolved")
    #                 res_str = f" → {resolved}" if resolved else ""
    #                 lines.append(f"  • {e.get('name', '?')} ({e.get('entity_type', '?')}){res_str}")

    #         params = parsed_request.get("parameters", {})
    #         if params:
    #             lines.append("Parameters:")
    #             for k, v in params.items():
    #                 lines.append(f"  • {k}: {v}")

    #         implicit = parsed_request.get("implicit_requirements", [])
    #         if implicit:
    #             lines.append("Implicit requirements:")
    #             for r in implicit:
    #                 lines.append(f"  • {r}")

    #         lines.append(f"Raw text: {parsed_request.get('raw_text', '')}")
    #         return "\n".join(lines)

    #     @staticmethod
    #     def _format_plan_readable(plan: list) -> str:
    #         """Format a plan (list of step dicts) into human-readable text."""
    #         if not plan:
    #             return "No plan generated."
    #         _AGENT_LABELS = {
    #             "models_subgraph": "Simulazione/Modelli",
    #             "retriever_subgraph": "Recupero Dati",
    #         }
    #         lines = []
    #         for i, step in enumerate(plan, 1):
    #             agent = step.get("agent", "unknown")
    #             goal = step.get("goal", "no goal")
    #             label = _AGENT_LABELS.get(agent, agent)
    #             lines.append(f"  Step {i}: [{label}] {goal}")
    #         return "\n".join(lines)

    #     class CreatePlan:

    #         @staticmethod
    #         def stable(state: MABaseGraphState, **kwargs) -> Prompt:
    #             parsed_request = state.get("parsed_request", {})
    #             # Priority: relevant_layers (processed) > layer_registry (raw)
    #             layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
    #             if not layers:
    #                 layers = state.get("layer_registry", [])
    #             conversation_context = _get_conversation_context(state)

    #             # Use human-readable formatting instead of JSON dumps
    #             request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
    #             layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

    #             message = (
    #                 f"## Parsed Request\n{request_text}\n"
    #                 f"\n"
    #                 f"## Available Layers\n{layers_text}\n"
    #             )
    #             if conversation_context:
    #                 message = (
    #                     f"## Conversation Context\n{conversation_context}\n\n"
    #                 ) + message

    #             p = {
    #                 "title": "PlanCreation",
    #                 "description": "structured plan creation with enriched request",
    #                 "command": "",
    #                 "message": message
    #             }
    #             return Prompt(p)
                
    #     class IncrementalReplanning:

    #         @staticmethod
    #         def stable(state: MABaseGraphState, **kwargs) -> Prompt:
    #             """Generate prompt for incremental modifications (modify label)."""
    #             parsed_request = state.get("parsed_request", {})
    #             current_plan = state.get("plan", [])
    #             replan_request = state.get("replan_request")
    #             user_feedback = replan_request.content if replan_request else "No feedback"
    #             conversation_context = _get_conversation_context(state)

    #             request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
    #             plan_text = OrchestratorPrompts.Plan._format_plan_readable(current_plan)

    #             # Include available layers for reference
    #             layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
    #             if not layers:
    #                 layers = state.get("layer_registry", [])
    #             layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

    #             message = (
    #                 f"User requested modifications to the existing plan.\n"
    #                 f"\n"
    #                 f"## Original Request\n{request_text}\n"
    #                 f"\n"
    #                 f"## Current Plan\n{plan_text}\n"
    #                 f"\n"
    #                 f"## Available Layers\n{layers_text}\n"
    #                 f"\n"
    #                 f"## User Feedback\n{user_feedback}\n"
    #                 f"\n"
    #                 f"Adjust the plan incrementally based on user feedback:\n"
    #                 f"- Keep steps not mentioned by the user\n"
    #                 f"- Modify only what's explicitly requested\n"
    #                 f"- If the user refers to a step by number, map it to the correct step above\n"
    #                 f"- If the user mentions using an existing layer, check Available Layers\n"
    #                 f"- Minimize disruption to the overall approach"
    #             )
    #             if conversation_context:
    #                 message = (
    #                     f"## Conversation Context\n{conversation_context}\n\n"
    #                 ) + message

    #             p = {
    #                 "title": "IncrementalReplanning",
    #                 "description": "incremental plan modification",
    #                 "command": "",
    #                 "message": message
    #             }
    #             return Prompt(p)
            
    #     class TotalReplanning:

    #         @staticmethod
    #         def stable(state: MABaseGraphState, **kwargs) -> Prompt:
    #             """Generate prompt for total replanning (reject label)."""
    #             parsed_request = state.get("parsed_request", {})
    #             previous_plan = state.get("plan", [])
    #             replan_request = state.get("replan_request")
    #             user_feedback = replan_request.content if replan_request else "No feedback"
    #             conversation_context = _get_conversation_context(state)

    #             request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
    #             plan_text = OrchestratorPrompts.Plan._format_plan_readable(previous_plan)

    #             # Include available layers for the new plan
    #             layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
    #             if not layers:
    #                 layers = state.get("layer_registry", [])
    #             layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

    #             message = (
    #                 f"User rejected the entire plan approach and wants a different strategy.\n"
    #                 f"\n"
    #                 f"## Original Request\n{request_text}\n"
    #                 f"\n"
    #                 f"## Previous Plan (REJECTED)\n{plan_text}\n"
    #                 f"\n"
    #                 f"## Available Layers\n{layers_text}\n"
    #                 f"\n"
    #                 f"## User Feedback\n{user_feedback}\n"
    #                 f"\n"
    #                 f"Create a completely new plan from scratch. "
    #                 f"Take a fundamentally different approach based on user requirements. "
    #                 f"Do not repeat the rejected strategy."
    #             )
    #             if conversation_context:
    #                 message = (
    #                     f"## Conversation Context\n{conversation_context}\n\n"
    #                 ) + message

    #             p = {
    #                 "title": "TotalReplanning",
    #                 "description": "total plan modification",
    #                 "command": "",
    #                 "message": message
    #             }
    #             return Prompt(p)
            
    #     class PlanExplanation:
            
    #         class ExplainerMainContext:
                
    #             @staticmethod
    #             def stable() -> Prompt:
    #                 p = {
    #                     "title": "ExplainerMainContext",
    #                     "description": "main context for plan explanation",
    #                     "command": "",
    #                     "message": (
    #                         "You are a helpful assistant that provides clear and concise explanations of execution plans."
    #                     )
    #                 }
    #                 return Prompt(p)

    #         class RequestExplanation:

    #             @staticmethod
    #             def stable(state: MABaseGraphState, user_question: str, **kwargs) -> Prompt:
    #                 """Generate prompt to explain the plan (clarify label)."""
    #                 plan = state.get("plan", [])
    #                 parsed_request = state.get("parsed_request", {})
                    
    #                 p = {
    #                     "title": "PlanExplanation",
    #                     "description": "explain the plan",
    #                     "command": "",
    #                     "message": (
    #                         f"User asked about the execution plan: '{user_question}'\n"
    #                         f"\n"
    #                         f"Original request:\n{parsed_request}\n"
    #                         f"\n"
    #                         f"Current plan:\n{plan}\n"
    #                         f"\n"
    #                         f"Provide a clear, concise explanation that answers the user's specific question. "
    #                         f"Focus on helping them understand the plan without changing it. "
    #                         f"Be informative but brief."
    #                     )
    #                 }
    #                 return Prompt(p)

    #     class PlanConfirmation:

    #         class RequestMainContext:

    #             @staticmethod
    #             def stable() -> Prompt:
    #                 p = {
    #                     "title": "ConfirmationRequesterMainContext",
    #                     "description": "request user confirmation for main context",
    #                     "command": "",
    #                     "message": (
    #                         "You are a helpful assistant that communicates execution plans clearly and concisely."
    #                     )
    #                 }
    #                 return Prompt(p)
                
    #         class RequestGenerator:

    #             @staticmethod
    #             def _format_plan_for_display(plan: List[Dict]) -> str:
    #                 """Format plan steps into a readable string."""
    #                 formatted_steps = []
    #                 for i, step in enumerate(plan, 1):
    #                     agent = step.get("agent", "Unknown")
    #                     goal = step.get("goal", "No description")
    #                     formatted_steps.append(f"{i}. [{agent}] {goal}")
    #                 return "\n".join(formatted_steps)

    #             @staticmethod
    #             def stable(state: MABaseGraphState, **kwargs) -> Prompt:
    #                 plan = state.get("plan", [])

    #                 plan_text = OrchestratorPrompts.Plan.PlanConfirmation.RequestGenerator._format_plan_for_display(plan)

    #                 p = {
    #                     "title": "ConfirmationRequesterGenerator",
    #                     "description": "request user confirmation for generator",
    #                     "command": "",
    #                     "message": (
    #                         f"Generate a clear, concise confirmation message for the user about the following execution plan.\n"
    #                         f"The message should be:\n"
    #                         f"- Schematic and organized (use bullet points or numbering)\n"
    #                         f"- Concise but complete\n"
    #                         f"- End with a clear question asking if they want to proceed\n"
    #                         f"\n"
    #                         f"Plan:\n{plan_text}\n"
    #                         f"\n"
    #                         f"Generate the confirmation message (be brief and well-formatted):"
    #                     )
    #                 }
    #                 return Prompt(p)
                
    #         class ResponseClassifier:

    #             PLAN_RESPONSE_LABELS = {
    #                 "accept": (
    #                     "User accepts the plan and wants to proceed immediately. "
    #                     "Examples: 'ok', 'yes', 'proceed', 'looks good', 'go ahead', 'do it', 'perfect'"
    #                 ),
    #                 "modify": (
    #                     "User wants changes to the plan but still intends to execute something. "
    #                     "Examples: 'change step 2', 'skip retriever', 'add more detail', 'swap order', "
    #                     "'do only step 1', 'remove the last step'"
    #                 ),
    #                 "clarify": (
    #                     "User needs more information before deciding (asking questions, not rejecting). "
    #                     "Examples: 'what does step 1 do?', 'explain retriever', 'why two steps?', "
    #                     "'what is DPC?', 'how long will this take?'"
    #                 ),
    #                 "reject": (
    #                     "User rejects the plan approach and wants a completely different strategy. "
    #                     "Examples: 'no that's wrong', 'different approach please', 'not what I meant', "
    #                     "'try another way', 'that won't work'"
    #                 ),
    #                 "abort": (
    #                     "User wants to cancel the entire operation without alternatives. "
    #                     "Examples: 'cancel', 'stop', 'nevermind', 'forget it', 'abort', 'no thanks'"
    #                 )
    #             }
                
    #             class ClassifierContext:

    #                 @staticmethod
    #                 def stable() -> Prompt:
    #                     p = {
    #                         "title": "ResponseClassifierContext",
    #                         "description": "context for classifying user responses",
    #                         "command": "",
    #                         "message": (
    #                             "You are a precise intent classifier. Return only the label name."
    #                         )
    #                     }
    #                     return Prompt(p)
                    
    #             class ZeroShotClassifier:
                    
    #                 @staticmethod
    #                 def stable(user_response: str) -> Prompt:
    #                     plan_response_labels = OrchestratorPrompts.Plan.PlanConfirmation.ResponseClassifier.PLAN_RESPONSE_LABELS
    #                     plan_response_labels_names = list(plan_response_labels.keys())

    #                     p = {
    #                         "title": "ZeroShotClassifier",
    #                         "description": "zero-shot classifier for user responses",
    #                         "command": "",
    #                         "message": (
    #                             "Classify the user's response into ONE of these categories:\n\n"
    #                             f"{json.dumps(plan_response_labels, indent=2)}\n\n"
    #                             f"User response: '{user_response}'\n\n"
    #                             f"Return ONLY the label name ({'/'.join(plan_response_labels_names)}) as a single word."
    #                         )
    #                     }
    #                     return Prompt(p)

    #     class StepCheckpoint:
    #         """Prompts for the mid-plan step-checkpoint interrupt in SupervisorRouter."""

    #         CHECKPOINT_RESPONSE_LABELS = {
    #             "continue": (
    #                 "User wants to proceed with the next step. "
    #                 "Examples: 'ok', 'yes', 'continue', 'proceed', 'go ahead', 'next', 'fine', 'do it'"
    #             ),
    #             "abort": (
    #                 "User wants to stop execution and cancel remaining steps. "
    #                 "Examples: 'stop', 'abort', 'cancel', 'halt', 'no', 'enough', 'nevermind', 'that's enough'"
    #             ),
    #         }

    #         class CheckpointContext:

    #             @staticmethod
    #             def stable() -> Prompt:
    #                 p = {
    #                     "title": "StepCheckpointContext",
    #                     "description": "context for step-checkpoint classifier",
    #                     "command": "",
    #                     "message": (
    #                         "You are a precise intent classifier. Return only the label name."
    #                     ),
    #                 }
    #                 return Prompt(p)

    #         class CheckpointClassifier:

    #             @staticmethod
    #             def stable(user_response: str) -> Prompt:
    #                 labels = OrchestratorPrompts.Plan.StepCheckpoint.CHECKPOINT_RESPONSE_LABELS
    #                 label_names = list(labels.keys())
    #                 p = {
    #                     "title": "StepCheckpointClassifier",
    #                     "description": "zero-shot classifier for step-checkpoint responses",
    #                     "command": "",
    #                     "message": (
    #                         "Classify the user's response into ONE of these categories:\n\n"
    #                         f"{json.dumps(labels, indent=2)}\n\n"
    #                         f"User response: '{user_response}'\n\n"
    #                         f"Return ONLY the label name ({'/'.join(label_names)}) as a single word."
    #                     ),
    #                 }
    #                 return Prompt(p)

    #         @staticmethod
    #         def stable(state: MABaseGraphState, completed_step: dict) -> Prompt:
    #             """Generate the step-checkpoint interrupt message shown to the user.

    #             Args:
    #                 state: current graph state.
    #                 completed_step: the plan step that just finished
    #                     (dict with keys ``agent`` and ``goal``).
    #             """
    #             plan = state.get("plan", [])
    #             current_step_idx = state.get("current_step", 0)
    #             tool_results = state.get("tool_results", {})

    #             completed_agent = completed_step.get("agent", "unknown agent")
    #             completed_goal = completed_step.get("goal", "no goal specified")

    #             # Summarise tool_results for the completed agent key
    #             agent_key = "retriever" if "retriever" in completed_agent else "models"
    #             agent_results = tool_results.get(agent_key, {})
    #             result_summary = str(agent_results) if agent_results else "No result detail available."

    #             # Remaining steps (from current_step_idx onward — already incremented by executor)
    #             remaining = plan[current_step_idx:] if plan else []
    #             if remaining:
    #                 remaining_lines = "\n".join(
    #                     f"  {i + 1}. [{s.get('agent', '?')}] {s.get('goal', '')}"
    #                     for i, s in enumerate(remaining)
    #                 )
    #                 remaining_text = f"Remaining steps ({len(remaining)}):\n{remaining_lines}"
    #             else:
    #                 remaining_text = "No further steps — this was the last step."

    #             p = {
    #                 "title": "StepCheckpoint",
    #                 "description": "mid-plan checkpoint message for the user",
    #                 "command": "",
    #                 "message": (
    #                     f"Step completed: [{completed_agent}] {completed_goal}\n"
    #                     f"\n"
    #                     f"Result summary:\n{result_summary}\n"
    #                     f"\n"
    #                     f"{remaining_text}\n"
    #                     f"\n"
    #                     f"Do you want to continue with the next step, or stop here?"
    #                 ),
    #             }
    #             return Prompt(p)