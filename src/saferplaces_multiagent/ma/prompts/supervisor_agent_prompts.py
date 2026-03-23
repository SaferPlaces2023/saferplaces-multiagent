"""Supervisor agent prompts for orchestration."""

from typing import List, Dict

import json

from ...common.states import MABaseGraphState
from ..names import NodeNames

from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION

from . import Prompt

from ...common.utils import get_conversation_context as _get_conversation_context


class OrchestratorPrompts:

    class MainContext:

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "OrchestrationContext",
                "description": "planning skill — domain-aware multi-step workflow with operational rules",
                "command": "",
                "message": (
                    "You are an expert multi-step planning agent for a geospatial AI platform "
                    "specialized in flood simulations, meteorological data retrieval, and digital twin generation.\n"
                    "\n"
                    "## Operational Rules\n"
                    "\n"
                    "### 1. FLOOD SIMULATION (SaferRain)\n"
                    "Always requires:\n"
                    "- A DEM raster for the target area. If no DEM is available in context → add a Digital Twin step FIRST.\n"
                    "- Rainfall input: a constant value in mm (e.g. 50mm) OR a rainfall raster from context/retrieval.\n"
                    "- If the user mentions rainfall from radar/forecast and no such raster exists → add a retriever_subgraph step BEFORE.\n"
                    "Output: water depth raster (WD).\n"
                    "\n"
                    "### 2. DIGITAL TWIN (DigitalTwinTool)\n"
                    "Creates: DEM + buildings + land-use for an area.\n"
                    "Requires: only a bounding box (bbox).\n"
                    "Use as FIRST step when no DEM exists for the target area.\n"
                    "Output: DEM raster + building footprints + land-use layer.\n"
                    "\n"
                    "### 3. DPC DATA RETRIEVAL\n"
                    "Retrieves radar/meteorological data from Italian Civil Protection.\n"
                    "Coverage: Italy ONLY. Data: past/recent (not forecasts).\n"
                    "Products: SRI (rainfall intensity mm/h), VMI (max reflectivity), SRT1/3/6/12/24 (cumulative precipitation), \n"
                    "TEMP (temperature), LTG (lightning), IR108 (cloud cover), HRD (heavy rain detection).\n"
                    "If the area is outside Italy → DO NOT use DPC, use Meteoblue instead.\n"
                    "\n"
                    "### 4. METEOBLUE FORECAST RETRIEVAL\n"
                    "Retrieves weather forecast data from Meteoblue.\n"
                    "Coverage: global. Data: FUTURE forecasts up to 14 days.\n"
                    "Variables: PRECIPITATION, TEMPERATURE, WINDSPEED, WINDDIRECTION, RELATIVEHUMIDITY, etc.\n"
                    "Use when the user asks for forecasts or future weather data.\n"
                    "\n"
                    "## Reasoning Process\n"
                    "1. Identify the final outcome the user wants.\n"
                    "2. Check the parsed request for resolved entities, parameters, and implicit requirements.\n"
                    "3. For each needed capability, check if prerequisites are satisfied by available layers.\n"
                    "   - If a prerequisite is present → skip the producing step.\n"
                    "   - If a prerequisite is missing → add the producing agent as an earlier step.\n"
                    "4. Order steps so every agent receives what it needs from the previous step.\n"
                    "5. Keep steps to the minimum necessary.\n"
                    "\n"
                    "## Available Agents\n"
                    "\n"
                    "### models_subgraph — Simulations & Geospatial Models\n"
                    "**Tools**: DigitalTwinTool, SaferRainTool\n"
                    "**Use when**: creating DEM/buildings/land-use for new areas, or running flood simulations.\n"
                    "**Prerequisites**: DigitalTwinTool needs only a bbox. SaferRainTool needs a DEM (create via DigitalTwin if missing).\n"
                    "**Outputs**: DEM raster, building footprints, land-use layer (DigitalTwin); water depth raster (SaferRain).\n"
                    "\n"
                    "### retriever_subgraph — Meteorological Data Retrieval\n"
                    "**Tools**: DPCRetrieverTool (Italy, past data), MeteoblueRetrieverTool (global, forecasts)\n"
                    "**Use when**: retrieving rainfall radar data, precipitation measurements, weather forecasts.\n"
                    "**Prerequisites**: none (only needs product/variable, area, and time range).\n"
                    "**Outputs**: meteorological raster layer (precipitation, temperature, radar data).\n"
                    "\n"
                    "## Rules\n"
                    "- Use ONLY agents listed above (models_subgraph or retriever_subgraph).\n"
                    "- Do NOT execute tools — only plan.\n"
                    "- Do NOT ask the user questions.\n"
                    "- Return an empty plan (steps: []) for informational queries that need no actions.\n"
                    "- Include resolved entity information (bbox, parameters) in the goal description when available.\n"
                    "\n"
                    "## Common mistakes to avoid\n"
                    "- Do NOT schedule SaferRain without a DEM. Always check available layers first; "
                    "if no DEM → add a Digital Twin step before SaferRain.\n"
                    "- Do NOT use retriever_subgraph to create DEMs or run simulations — it only retrieves data.\n"
                    "- Do NOT use models_subgraph to retrieve meteorological data — it only creates DEMs and runs simulations.\n"
                    "- Do NOT use DPC (retriever_subgraph) for areas outside Italy — use Meteoblue instead.\n"
                    "- Do NOT use DPC for future forecasts — DPC provides only past/recent data.\n"
                    "- Do NOT use Meteoblue for past/historical data — Meteoblue provides only future forecasts.\n"
                    "- Do NOT create a Digital Twin if a DEM for the target area already exists in available layers.\n"
                    "- Do NOT include unnecessary steps — keep the plan minimal.\n"
                    "- Do NOT duplicate steps for the same goal.\n"
                    "- Do NOT omit the bbox or key parameters from the goal description — "
                    "the specialized agent needs them to select tool arguments.\n"
                    "\n"
                    "## Output format\n"
                    "- steps: ordered list of execution steps\n"
                    "  - steps[].agent: agent name (models_subgraph or retriever_subgraph)\n"
                    "  - steps[].goal: DETAILED description that includes:\n"
                    "    - WHAT the tool will do\n"
                    "    - WHY it's needed (e.g., 'no DEM available for this area')\n"
                    "    - KEY PARAMETERS that will be used (bbox, rainfall_mm, product, etc.)\n"
                    "    - EXPECTED OUTPUT (e.g., 'produces a DEM raster at 30m resolution')\n"
                    "\n"
                    "## Examples\n"
                    "User: 'simulate flood for Rome with 50mm' — NO DEM in context:\n"
                    '  steps: [{"agent": "models_subgraph", "goal": "Create Digital Twin (DEM + buildings) for Rome (bbox ~[12.35, 41.80, 12.60, 41.99])"}, '
                    '{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation with 50mm constant rainfall on the Rome DEM"}]\n'
                    "\n"
                    "User: 'simulate flood for Rome with 50mm' — DEM already exists:\n"
                    '  steps: [{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation with 50mm rainfall using the existing DEM layer"}]\n'
                    "\n"
                    "User: 'simulate flood using real radar rainfall on existing DEM' — no rainfall raster:\n"
                    '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve current SRI rainfall data from DPC for the DEM area"}, '
                    '{"agent": "models_subgraph", "goal": "Run SaferRain flood simulation using the retrieved rainfall raster and existing DEM"}]\n'
                    "\n"
                    "User: 'what is the current rainfall in northern Italy':\n"
                    '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve current SRI rainfall intensity for northern Italy from DPC"}]\n'
                    "\n"
                    "User: 'get precipitation forecast for London':\n"
                    '  steps: [{"agent": "retriever_subgraph", "goal": "Retrieve Meteoblue PRECIPITATION forecast for London area"}]\n'
                    "\n"
                    "User: general question / greeting:\n"
                    "  steps: []"
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Previous stable version — preserved for test override compatibility."""
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

        # Legacy AGENT_REGISTRY kept for backward compatibility (used by replanning prompts)
        AGENT_REGISTRY = [
            {
                "name": NodeNames.MODELS_SUBGRAPH,
                "description": MODELS_AGENT_DESCRIPTION["description"],
                "examples": MODELS_AGENT_DESCRIPTION["examples"],
                "outputs": MODELS_AGENT_DESCRIPTION["outputs"],
                "prerequisites": MODELS_AGENT_DESCRIPTION["prerequisites"],
                "implicit_step_rules": MODELS_AGENT_DESCRIPTION["implicit_step_rules"],
            },
            {
                "name": NodeNames.RETRIEVER_SUBGRAPH,
                "description": SAFERCAST_AGENT_DESCRIPTION["description"],
                "examples": SAFERCAST_AGENT_DESCRIPTION["examples"],
                "outputs": SAFERCAST_AGENT_DESCRIPTION["outputs"],
                "prerequisites": SAFERCAST_AGENT_DESCRIPTION["prerequisites"],
                "implicit_step_rules": SAFERCAST_AGENT_DESCRIPTION["implicit_step_rules"],
            },
        ]

        @staticmethod
        def _format_layers_summary(layers: list) -> str:
            """Build a human-readable summary of available layers for the planner."""
            if not layers:
                return "No layers available in the current project."
            lines = []
            for l in layers:
                title = l.get("title", "untitled")
                ltype = l.get("type", "unknown")
                desc = l.get("description", "")
                src = l.get("src", "")
                meta = l.get("metadata", {})
                line = f"  • {title} ({ltype})"
                if desc:
                    line += f" — {desc}"
                details = []
                if meta:
                    bbox = meta.get("bbox")
                    if bbox:
                        details.append(f"bbox={bbox}")
                    band = meta.get("band")
                    if band is not None:
                        details.append(f"band={band}")
                    res = meta.get("pixelsize") or meta.get("resolution")
                    if res:
                        details.append(f"res={res}m")
                if src:
                    details.append(f"src={src}")
                if details:
                    line += f"\n    [{', '.join(details)}]"
                lines.append(line)
            return "\n".join(lines)

        @staticmethod
        def _format_parsed_request(parsed_request: dict) -> str:
            """Format the enriched ParsedRequest for the planner prompt."""
            if not parsed_request:
                return "No parsed request available."
            lines = []
            lines.append(f"Intent: {parsed_request.get('intent', 'N/A')}")
            lines.append(f"Request type: {parsed_request.get('request_type', 'N/A')}")

            entities = parsed_request.get("entities", [])
            if entities:
                lines.append("Entities:")
                for e in entities:
                    resolved = e.get("resolved")
                    res_str = f" → {resolved}" if resolved else ""
                    lines.append(f"  • {e.get('name', '?')} ({e.get('entity_type', '?')}){res_str}")

            params = parsed_request.get("parameters", {})
            if params:
                lines.append("Parameters:")
                for k, v in params.items():
                    lines.append(f"  • {k}: {v}")

            implicit = parsed_request.get("implicit_requirements", [])
            if implicit:
                lines.append("Implicit requirements:")
                for r in implicit:
                    lines.append(f"  • {r}")

            lines.append(f"Raw text: {parsed_request.get('raw_text', '')}")
            return "\n".join(lines)

        @staticmethod
        def _format_plan_readable(plan: list) -> str:
            """Format a plan (list of step dicts) into human-readable text."""
            if not plan:
                return "No plan generated."
            _AGENT_LABELS = {
                "models_subgraph": "Simulazione/Modelli",
                "retriever_subgraph": "Recupero Dati",
            }
            lines = []
            for i, step in enumerate(plan, 1):
                agent = step.get("agent", "unknown")
                goal = step.get("goal", "no goal")
                label = _AGENT_LABELS.get(agent, agent)
                lines.append(f"  Step {i}: [{label}] {goal}")
            return "\n".join(lines)

        class CreatePlan:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                parsed_request = state.get("parsed_request", {})
                # Priority: relevant_layers (processed) > layer_registry (raw)
                layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
                if not layers:
                    layers = state.get("layer_registry", [])
                conversation_context = _get_conversation_context(state)

                # Use human-readable formatting instead of JSON dumps
                request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
                layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

                message = (
                    f"## Parsed Request\n{request_text}\n"
                    f"\n"
                    f"## Available Layers\n{layers_text}\n"
                )
                if conversation_context:
                    message = (
                        f"## Conversation Context\n{conversation_context}\n\n"
                    ) + message

                p = {
                    "title": "PlanCreation",
                    "description": "structured plan creation with enriched request",
                    "command": "",
                    "message": message
                }
                return Prompt(p)
                
        class IncrementalReplanning:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Generate prompt for incremental modifications (modify label)."""
                parsed_request = state.get("parsed_request", {})
                current_plan = state.get("plan", [])
                replan_request = state.get("replan_request")
                user_feedback = replan_request.content if replan_request else "No feedback"
                conversation_context = _get_conversation_context(state)

                request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
                plan_text = OrchestratorPrompts.Plan._format_plan_readable(current_plan)

                # Include available layers for reference
                layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
                if not layers:
                    layers = state.get("layer_registry", [])
                layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

                message = (
                    f"User requested modifications to the existing plan.\n"
                    f"\n"
                    f"## Original Request\n{request_text}\n"
                    f"\n"
                    f"## Current Plan\n{plan_text}\n"
                    f"\n"
                    f"## Available Layers\n{layers_text}\n"
                    f"\n"
                    f"## User Feedback\n{user_feedback}\n"
                    f"\n"
                    f"Adjust the plan incrementally based on user feedback:\n"
                    f"- Keep steps not mentioned by the user\n"
                    f"- Modify only what's explicitly requested\n"
                    f"- If the user refers to a step by number, map it to the correct step above\n"
                    f"- If the user mentions using an existing layer, check Available Layers\n"
                    f"- Minimize disruption to the overall approach"
                )
                if conversation_context:
                    message = (
                        f"## Conversation Context\n{conversation_context}\n\n"
                    ) + message

                p = {
                    "title": "IncrementalReplanning",
                    "description": "incremental plan modification",
                    "command": "",
                    "message": message
                }
                return Prompt(p)
            
        class TotalReplanning:

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Generate prompt for total replanning (reject label)."""
                parsed_request = state.get("parsed_request", {})
                previous_plan = state.get("plan", [])
                replan_request = state.get("replan_request")
                user_feedback = replan_request.content if replan_request else "No feedback"
                conversation_context = _get_conversation_context(state)

                request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
                plan_text = OrchestratorPrompts.Plan._format_plan_readable(previous_plan)

                # Include available layers for the new plan
                layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
                if not layers:
                    layers = state.get("layer_registry", [])
                layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

                message = (
                    f"User rejected the entire plan approach and wants a different strategy.\n"
                    f"\n"
                    f"## Original Request\n{request_text}\n"
                    f"\n"
                    f"## Previous Plan (REJECTED)\n{plan_text}\n"
                    f"\n"
                    f"## Available Layers\n{layers_text}\n"
                    f"\n"
                    f"## User Feedback\n{user_feedback}\n"
                    f"\n"
                    f"Create a completely new plan from scratch. "
                    f"Take a fundamentally different approach based on user requirements. "
                    f"Do not repeat the rejected strategy."
                )
                if conversation_context:
                    message = (
                        f"## Conversation Context\n{conversation_context}\n\n"
                    ) + message

                p = {
                    "title": "TotalReplanning",
                    "description": "total plan modification",
                    "command": "",
                    "message": message
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