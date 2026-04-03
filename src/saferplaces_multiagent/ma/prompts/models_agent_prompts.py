"""Models Agent prompts for environmental simulations and model orchestration.

Organizes prompts according to the F009 (Prompt Organization Architecture) pattern.
Prompts are structured hierarchically with `stable()` and version variants for A/B testing.
"""

import json

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from .request_parser_prompts import RequestParserInstructions

from ...common.utils import get_conversation_context as _get_conversation_context



class ModelsInstructions:

    class InvokeTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a flood simulation specialist for SaferPlaces.\n"
                        "You operate the SaferRain hydraulic model.\n"
                        "Your task is to propose tool calls that configure and run a flood simulation\n"
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

                    # map_context = MapAgentPrompts.MapContext

                    conversation_context = Prompt(dict(
                        header = "[CONVERSATION HISTORY]",
                        message = ContextBuilder.conversation_history(state, max_messages=5)
                    ))

                    goal_context = Prompt(dict(
                        header = "[GOAL]",
                        message = state['plan'][state['current_step']]['goal']
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
                    "## Tool: saferbuildings_tool\n"
                    "Detects flooded buildings from a water depth raster.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `water` (required): water depth raster from a prior flood simulation.\n"
                    "  Use the `src` value from the layer registry.\n"
                    "  → This tool does NOT run simulations. If no water depth layer exists,\n"
                    "    the orchestrator should have scheduled a safer_rain step first.\n"
                    "\n"
                    "Building source — choose ONE, they are mutually exclusive:\n"
                    "- `buildings` (optional): URL, S3 URI, or layer reference to a buildings dataset.\n"
                    "  Use the `src` value from the layer registry if a buildings layer is available.\n"
                    "- `provider` (optional): provider to fetch buildings automatically.\n"
                    "  Allowed values: OVERTURE (global, default), RER-REST/* (Emilia-Romagna, Italy),\n"
                    "  VENEZIA-WFS/* (Venice), VENEZIA-WFS-CRITICAL-SITES.\n"
                    "  → Use OVERTURE when no buildings layer is available in context.\n"
                    "  → NEVER set both `buildings` and `provider` at the same time.\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `bbox`: geographic extent in EPSG:4326 {west, south, east, north}.\n"
                    "  If omitted, the water raster bounds are used.\n"
                    "- `wd_thresh`: flood depth threshold in meters (default: 0.5).\n"
                    "  Use only if the user specifies a different threshold.\n"
                    "- `flood_mode`: 'BUFFER' (default, for OVERTURE/RER-REST), 'IN-AREA' (for VENEZIA-WFS), 'ALL'.\n"
                    "- `stats`: True to compute per-building water depth statistics (wd_min, wd_mean, wd_max).\n"
                    "  Use only if the user explicitly requests per-building statistics.\n"
                    "- `summary`: True to compute an aggregated summary grouped by building type.\n"
                    "  Use only if the user explicitly requests a summary.\n"
                    "\n"
                    "Output: vector layer with all buildings and `is_flooded` flag per feature.\n"
                    "\n"
                    "## Tool: safer_fire_tool\n"
                    "Simulates wildland fire propagation over terrain.\n"
                    "\n"
                    "Required parameters:\n"
                    "- `dem` (required): DEM/DTM raster for slope and terrain computation.\n"
                    "  Use the `src` value from the layer registry.\n"
                    "  → If no DEM is available, the orchestrator should have scheduled a digital_twin step first.\n"
                    "- `ignitions` (required): vector file (GeoJSON/GPKG) or raster defining fire ignition sources.\n"
                    "  Use the `src` value from the layer registry if an ignitions layer is available.\n"
                    "- `wind_speed` (required): constant wind speed in m/s (e.g. 8.0).\n"
                    "- `wind_direction` (required): constant wind direction in meteorological degrees\n"
                    "  (0=N, 90=E, 180=S, 270=W). The wind blows FROM this direction.\n"
                    "\n"
                    "Land use (optional) — choose ONE, mutually exclusive:\n"
                    "- `landuse` (optional): URL, S3 URI, or layer reference to a land use raster.\n"
                    "  Use the `src` value from the layer registry if a land use layer is available.\n"
                    "- `landuse_provider` (optional): provider to auto-fetch land use.\n"
                    "  Allowed: ESA/LANDUSE/V100 (global, default), RER/LANDUSE (Emilia-Romagna),\n"
                    "  CUSTOM/LANDUSE/FBVI, CUSTOM/LANDUSE/RER/AIB.\n"
                    "  → Use ESA/LANDUSE/V100 when no land use layer is available in context.\n"
                    "  → NEVER set both `landuse` and `landuse_provider` at the same time.\n"
                    "\n"
                    "Optional parameters:\n"
                    "- `bbox`: geographic extent in EPSG:4326 {west, south, east, north}.\n"
                    "  Restricts simulation to the area of interest. If omitted, full DEM extent is used.\n"
                    "- `start_datetime`: ISO 8601 start time (e.g. '2025-10-01T00:00:00Z').\n"
                    "  If omitted, current system time is used.\n"
                    "- `time_max`: maximum simulation duration in seconds (default: 3600 = 1 hour).\n"
                    "  Use larger values for longer simulations (e.g., 7200 = 2h, 21600 = 6h).\n"
                    "- `time_step_interval`: output snapshot interval in seconds (default: 300 = 5 min).\n"
                    "- `moisture`: constant fuel moisture content as fraction [0, 1] (default: 0.15).\n"
                    "  Lower values → faster fire spread (dry fuel). Range: 0.05 (very dry) to 0.40 (wet).\n"
                    "\n"
                    "Output: fire spread rasters (burned area, fire arrival time) at multiple time steps.\n"
                    "\n"
                    "## Common mistakes to avoid\n"
                    "- Do NOT set `dem` to a location name — always use a layer `src` URI\n"
                    "- Do NOT set `rain` to a product name — use the numeric value or raster URI\n"
                    "- Do NOT set `pixelsize` to a value unless the user explicitly asks for a specific resolution\n"
                    "- Do NOT propose safer_rain if no DEM layer exists in context\n"
                    "- Do NOT set both `buildings` and `provider` for saferbuildings_tool — they are mutually exclusive\n"
                    "- Do NOT propose saferbuildings_tool if no water depth layer exists in context\n"
                    "- Do NOT propose safer_fire_tool if no DEM layer exists in context\n"
                    "- Do NOT set both `landuse` and `landuse_provider` for safer_fire_tool — they are mutually exclusive\n"
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
