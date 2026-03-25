"""Models Agent prompts for environmental simulations and model orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json

from ...common.states import MABaseGraphState

from . import Prompt

from ...common.utils import get_conversation_context as _get_conversation_context


# State key constants (referenced for clarity, imported from models_agent.py at runtime)
STATE_MODELS_INVOCATION = "models_invocation"
STATE_MODELS_CONFIRMATION = "models_invocation_confirmation"
STATE_MODELS_REINVOCATION_REQUEST = "models_reinvocation_request"


class ModelsPrompts:
    """Prompts for specialized models/simulations agent.
    
    Follows the F009 pattern with hierarchical organization and static method versioning.
    """

    class MainContext:
        """System-level contextualization for simulation tool selection."""

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "SimulationToolSelectionContext",
                "description": "system role for environmental models and simulations with tool-specific guides",
                "command": "",
                "message": (
                    "You are a specialized simulation agent for a geospatial AI platform.\n"
                    "\n"
                    "## Your task\n"
                    "1. Analyze the simulation/model goal provided by the orchestrator.\n"
                    "2. Select the correct tool and provide accurate arguments.\n"
                    "3. If a tool requires a layer input, select it from the Relevant layers in context (use the layer's `src` value).\n"
                    "4. If no suitable layer exists, do NOT invent one — describe what is missing.\n"
                    "\n"
                    "## Tool: digital_twin\n"
                    "Creates geospatial base layers for an Area of Interest.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `bbox` (required): bounding box in EPSG:4326 {west, south, east, north}\n"
                    "  → If the user provides a location name, infer the approximate bbox.\n"
                    "- `layers` (required): flat list of layer names to generate.\n"
                    "  → DEFAULT for generic requests (new project, digital twin, DEM): ['dem']\n"
                    "  → Only add more names if the user explicitly requests specific layers.\n"
                    "  Extended example: ['dem', 'slope', 'hand', 'buildings', 'landuse', 'manning']\n"
                    "  All available names: dem, valleydepth, tri, tpi, slope, dem_filled, flow_dir, flow_accum,\n"
                    "  streams, hand, twi, river_network, river_distance, buildings, dem_buildings,\n"
                    "  dem_filled_buildings, roads, landuse, manning, ndvi, ndwi, ndbi, sea_mask, sand, clay\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `dem_dataset`: DEM source identifier (default: auto-selected by region).\n"
                    "  Leave as None unless the user requests a specific dataset.\n"
                    "- `pixelsize`: resolution in meters (default: None = native resolution).\n"
                    "  Prefer None unless the user explicitly requests a resolution.\n"
                    "\n"
                    "Output: the requested layers as raster/vector files.\n"
                    "\n"
                    "## Tool: safer_rain\n"
                    "Runs flood propagation simulation on a DEM using rainfall input.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `dem` (required): DEM/DTM raster. Use the `src` value from the layer registry.\n"
                    "  → This tool does NOT create DEMs. If no DEM is available, the orchestrator should have\n"
                    "    scheduled a digital_twin step first.\n"
                    "- `rain` (required): rainfall input — either:\n"
                    "  • A numeric value (mm) for uniform rainfall (e.g. 50.0 for 50mm)\n"
                    "  • A raster URL/URI for spatially variable rainfall (use `src` from layer registry)\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `band` / `to_band`: for multiband rainfall rasters, select band range (1-based).\n"
                    "  Use only if the goal mentions time-series or specific bands.\n"
                    "- `mode`: 'lambda' (fast, default) or 'batch' (large areas). Keep default unless specified.\n"
                    "- `t_srs`: target CRS (e.g. 'EPSG:32633'). Leave None to use DEM's CRS.\n"
                    "\n"
                    "Output: water depth raster (GeoTIFF) in meters.\n"
                    "\n"
                    "## Common mistakes to avoid\n"
                    "- Do NOT set `dem` to a location name — always use a layer `src` URI\n"
                    "- Do NOT set `rain` to a product name — use the numeric value or raster URI\n"
                    "- Do NOT set `pixelsize` to a value unless the user explicitly asks for a specific resolution\n"
                    "- Do NOT propose safer_rain if no DEM layer exists in context\n"
                    "\n"
                    "## Rules\n"
                    "- Use only tools from the provided list.\n"
                    "- Do NOT execute commands directly; only propose tool calls.\n"
                    "- Use only layers that explicitly exist in the provided context."
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Previous stable version — preserved for test override compatibility."""
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
