"""Supervisor agent prompts for orchestration."""

from typing import Optional, TypedDict, Union, List, Dict, Any, Literal

import json

from ...common.states import MABaseGraphState
from ..names import NodeNames

from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION

from . import Prompt


class OrchestratorPrompts:

    class MainContext:

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "OrchestrationContext",
                "description": "basic orchestration context",
                "command": "",
                "message": (
                    "You are a high-level orchestration agent.\n"
                    "\n"
                    "Your task:\n"
                    "- Analyze the parsed user request.\n"
                    "- Decide if specialized agents are needed to execute the task.\n"
                    "- If agents are needed, break the task into ordered execution steps.\n"
                    "- If the request is a general question or doesn't require actions, return an empty plan.\n"
                    "- Each step (if any) must specify:\n"
                    "  - the agent name\n"
                    "  - the goal of that step\n"
                    "\n"
                    "Rules:\n"
                    "- Only use agents from the provided registry.\n"
                    "- Do NOT invent new agents.\n"
                    "- Do NOT execute tools.\n"
                    "- Do NOT ask the user questions.\n"
                    "- Focus only on execution planning.\n"
                    "- Keep the plan minimal and logically ordered.\n"
                    "- Empty plan is valid for informational queries."
                )
            }
            return Prompt(p)
        
        @staticmethod
        def v001() -> Prompt:
            p = {
                "title": "OrchestrationContext",
                "description": "basic orchestration context",
                "command": "",
                "message": (
                    "You are a high-level orchestration agent.\n"
                    "\n"
                    "Your task:\n"
                    "- Analyze the parsed user request.\n"
                    "- Decide if specialized agents are needed to execute the task.\n"
                    "- If agents are needed, break the task into ordered execution steps.\n"
                    "- If the request is a general question or doesn't require actions, return an empty plan.\n"
                    "- Each step (if any) must specify:\n"
                    "  - the agent name\n"
                    "  - the goal of that step\n"
                    "\n"
                    "Rules:\n"
                    "- Only use agents from the provided registry.\n"
                    "- Do NOT invent new agents.\n"
                    "- Do NOT execute tools.\n"
                    "- Do NOT ask the user questions.\n"
                    "- Focus only on execution planning.\n"
                    "- Keep the plan minimal and logically ordered.\n"
                    "- Empty plan is valid for informational queries."
                )
            }
            return Prompt(p)

    class Plan:

        AGENT_REGISTRY = [
            {
                "name": NodeNames.MODELS_SUBGRAPH,
                "description": MODELS_AGENT_DESCRIPTION["description"],
                "examples": MODELS_AGENT_DESCRIPTION["examples"],
            },
            {
                "name": NodeNames.RETRIEVER_SUBGRAPH,
                "description": SAFERCAST_AGENT_DESCRIPTION["description"],
                "examples": SAFERCAST_AGENT_DESCRIPTION["examples"],
            },
        ]

        class CreatePlan:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                parsed_request = state.get("parsed_request", "No parsed request available")
                layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
                additional_context = str(layers) if layers else "No additional context available"
                agent_registry_str = str(OrchestratorPrompts.Plan.AGENT_REGISTRY)

                p = {
                    "title": "PlanCreation",
                    "description": "basic plan creation",
                    "command": "",
                    "message": (
                        f"Parsed request:\n{parsed_request}\n"
                        f"\n"
                        f"Additional context:\n{additional_context}\n"
                        f"\n"
                        f"Available agents:\n{agent_registry_str}"
                    )
                }
                return Prompt(p)
                
        class IncrementalReplanning:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Generate prompt for incremental modifications (modify label)."""
                parsed_request = state.get("parsed_request", "No parsed request available")
                current_plan = state.get("plan", "No plan available")
                replan_request = state.get("replan_request")
                user_feedback = replan_request.content if replan_request else "No feedback"
    
                p = {
                    "title": "IncrementalReplanning",
                    "description": "incremental plan modification",
                    "command": "",
                    "message": (
                        f"User requested modifications to the existing plan.\n"
                        f"\n"
                        f"Original request:\n{parsed_request}\n"
                        f"\n"
                        f"Current plan:\n{current_plan}\n"
                        f"\n"
                        f"User feedback:\n{user_feedback}\n"
                        f"\n"
                        f"Adjust the plan incrementally based on user feedback. "
                        f"Keep what works and is not mentioned, modify only what's explicitly requested. "
                        f"Minimize disruption to the overall approach."
                    )
                }
                return Prompt(p)
            
        class TotalReplanning:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Generate prompt for total replanning (reject label)."""
                parsed_request = state.get("parsed_request", "No parsed request available")
                previous_plan = state.get("plan", "No plan available")
                replan_request = state.get("replan_request")
                user_feedback = replan_request.content if replan_request else "No feedback"
    
                p = {
                    "title": "TotalReplanning",
                    "description": "total plan modification",
                    "command": "",
                    "message": (
                        f"User rejected the entire plan approach and wants a different strategy.\n"
                        f"\n"
                        f"Original request:\n{parsed_request}\n"
                        f"\n"
                        f"Previous plan (REJECTED):\n{previous_plan}\n"
                        f"\n"
                        f"User feedback:\n{user_feedback}\n"
                        f"\n"
                        f"Create a completely new plan from scratch. "
                        f"Take a fundamentally different approach based on user requirements. "
                        f"Do not repeat the rejected strategy."
                    )
                }
                return Prompt(p)
            
        class PlanExplanation:
            
            class ExplainerMainContext:
                
                @staticmethod
                def stable() -> Prompt:
                    p = {
                        "title": "ExplainerMainContext",
                        "description": "main context for plan explanation",
                        "command": "",
                        "message": (
                            "You are a helpful assistant that provides clear and concise explanations of execution plans."
                        )
                    }
                    return Prompt(p)

            class RequestExplanation:

                @staticmethod
                def stable(state: MABaseGraphState, user_question: str, **kwargs) -> Prompt:
                    """Generate prompt to explain the plan (clarify label)."""
                    plan = state.get("plan", [])
                    parsed_request = state.get("parsed_request", {})
                    
                    p = {
                        "title": "PlanExplanation",
                        "description": "explain the plan",
                        "command": "",
                        "message": (
                            f"User asked about the execution plan: '{user_question}'\n"
                            f"\n"
                            f"Original request:\n{parsed_request}\n"
                            f"\n"
                            f"Current plan:\n{plan}\n"
                            f"\n"
                            f"Provide a clear, concise explanation that answers the user's specific question. "
                            f"Focus on helping them understand the plan without changing it. "
                            f"Be informative but brief."
                        )
                    }
                    return Prompt(p)

        class PlanConfirmation:

            class RequestMainContext:

                @staticmethod
                def stable() -> Prompt:
                    p = {
                        "title": "ConfirmationRequesterMainContext",
                        "description": "request user confirmation for main context",
                        "command": "",
                        "message": (
                            "You are a helpful assistant that communicates execution plans clearly and concisely."
                        )
                    }
                    return Prompt(p)
                
            class RequestGenerator:

                @staticmethod
                def _format_plan_for_display(plan: List[Dict]) -> str:
                    """Format plan steps into a readable string."""
                    formatted_steps = []
                    for i, step in enumerate(plan, 1):
                        agent = step.get("agent", "Unknown")
                        goal = step.get("goal", "No description")
                        formatted_steps.append(f"{i}. [{agent}] {goal}")
                    return "\n".join(formatted_steps)

                @staticmethod
                def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                    plan = state.get("plan", [])

                    plan_text = OrchestratorPrompts.Plan.PlanConfirmation.RequestGenerator._format_plan_for_display(plan)

                    p = {
                        "title": "ConfirmationRequesterGenerator",
                        "description": "request user confirmation for generator",
                        "command": "",
                        "message": (
                            f"Generate a clear, concise confirmation message for the user about the following execution plan.\n"
                            f"The message should be:\n"
                            f"- Schematic and organized (use bullet points or numbering)\n"
                            f"- Concise but complete\n"
                            f"- End with a clear question asking if they want to proceed\n"
                            f"\n"
                            f"Plan:\n{plan_text}\n"
                            f"\n"
                            f"Generate the confirmation message (be brief and well-formatted):"
                        )
                    }
                    return Prompt(p)
                
            class ResponseClassifier:

                PLAN_RESPONSE_LABELS = {
                    "accept": (
                        "User accepts the plan and wants to proceed immediately. "
                        "Examples: 'ok', 'yes', 'proceed', 'looks good', 'go ahead', 'do it', 'perfect'"
                    ),
                    "modify": (
                        "User wants changes to the plan but still intends to execute something. "
                        "Examples: 'change step 2', 'skip retriever', 'add more detail', 'swap order', "
                        "'do only step 1', 'remove the last step'"
                    ),
                    "clarify": (
                        "User needs more information before deciding (asking questions, not rejecting). "
                        "Examples: 'what does step 1 do?', 'explain retriever', 'why two steps?', "
                        "'what is DPC?', 'how long will this take?'"
                    ),
                    "reject": (
                        "User rejects the plan approach and wants a completely different strategy. "
                        "Examples: 'no that's wrong', 'different approach please', 'not what I meant', "
                        "'try another way', 'that won't work'"
                    ),
                    "abort": (
                        "User wants to cancel the entire operation without alternatives. "
                        "Examples: 'cancel', 'stop', 'nevermind', 'forget it', 'abort', 'no thanks'"
                    )
                }
                
                class ClassifierContext:

                    @staticmethod
                    def stable() -> Prompt:
                        p = {
                            "title": "ResponseClassifierContext",
                            "description": "context for classifying user responses",
                            "command": "",
                            "message": (
                                "You are a precise intent classifier. Return only the label name."
                            )
                        }
                        return Prompt(p)
                    
                class ZeroShotClassifier:
                    
                    @staticmethod
                    def stable(user_response: str) -> Prompt:
                        plan_response_labels = OrchestratorPrompts.Plan.PlanConfirmation.ResponseClassifier.PLAN_RESPONSE_LABELS
                        plan_response_labels_names = list(plan_response_labels.keys())

                        p = {
                            "title": "ZeroShotClassifier",
                            "description": "zero-shot classifier for user responses",
                            "command": "",
                            "message": (
                                "Classify the user's response into ONE of these categories:\n\n"
                                f"{json.dumps(plan_response_labels, indent=2)}\n\n"
                                f"User response: '{user_response}'\n\n"
                                f"Return ONLY the label name ({'/'.join(plan_response_labels_names)}) as a single word."
                            )
                        }
                        return Prompt(p)

        class StepCheckpoint:
            """Prompts for the mid-plan step-checkpoint interrupt in SupervisorRouter."""

            CHECKPOINT_RESPONSE_LABELS = {
                "continue": (
                    "User wants to proceed with the next step. "
                    "Examples: 'ok', 'yes', 'continue', 'proceed', 'go ahead', 'next', 'fine', 'do it'"
                ),
                "abort": (
                    "User wants to stop execution and cancel remaining steps. "
                    "Examples: 'stop', 'abort', 'cancel', 'halt', 'no', 'enough', 'nevermind', 'that's enough'"
                ),
            }

            class CheckpointContext:

                @staticmethod
                def stable() -> Prompt:
                    p = {
                        "title": "StepCheckpointContext",
                        "description": "context for step-checkpoint classifier",
                        "command": "",
                        "message": (
                            "You are a precise intent classifier. Return only the label name."
                        ),
                    }
                    return Prompt(p)

            class CheckpointClassifier:

                @staticmethod
                def stable(user_response: str) -> Prompt:
                    labels = OrchestratorPrompts.Plan.StepCheckpoint.CHECKPOINT_RESPONSE_LABELS
                    label_names = list(labels.keys())
                    p = {
                        "title": "StepCheckpointClassifier",
                        "description": "zero-shot classifier for step-checkpoint responses",
                        "command": "",
                        "message": (
                            "Classify the user's response into ONE of these categories:\n\n"
                            f"{json.dumps(labels, indent=2)}\n\n"
                            f"User response: '{user_response}'\n\n"
                            f"Return ONLY the label name ({'/'.join(label_names)}) as a single word."
                        ),
                    }
                    return Prompt(p)

            @staticmethod
            def stable(state: MABaseGraphState, completed_step: dict) -> Prompt:
                """Generate the step-checkpoint interrupt message shown to the user.

                Args:
                    state: current graph state.
                    completed_step: the plan step that just finished
                        (dict with keys ``agent`` and ``goal``).
                """
                plan = state.get("plan", [])
                current_step_idx = state.get("current_step", 0)
                tool_results = state.get("tool_results", {})

                completed_agent = completed_step.get("agent", "unknown agent")
                completed_goal = completed_step.get("goal", "no goal specified")

                # Summarise tool_results for the completed agent key
                agent_key = "retriever" if "retriever" in completed_agent else "models"
                agent_results = tool_results.get(agent_key, {})
                result_summary = str(agent_results) if agent_results else "No result detail available."

                # Remaining steps (from current_step_idx onward — already incremented by executor)
                remaining = plan[current_step_idx:] if plan else []
                if remaining:
                    remaining_lines = "\n".join(
                        f"  {i + 1}. [{s.get('agent', '?')}] {s.get('goal', '')}"
                        for i, s in enumerate(remaining)
                    )
                    remaining_text = f"Remaining steps ({len(remaining)}):\n{remaining_lines}"
                else:
                    remaining_text = "No further steps — this was the last step."

                p = {
                    "title": "StepCheckpoint",
                    "description": "mid-plan checkpoint message for the user",
                    "command": "",
                    "message": (
                        f"Step completed: [{completed_agent}] {completed_goal}\n"
                        f"\n"
                        f"Result summary:\n{result_summary}\n"
                        f"\n"
                        f"{remaining_text}\n"
                        f"\n"
                        f"Do you want to continue with the next step, or stop here?"
                    ),
                }
                return Prompt(p)