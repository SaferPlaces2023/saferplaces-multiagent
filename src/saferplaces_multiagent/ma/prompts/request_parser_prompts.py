"""Request parser prompts for the Request Analyzer (§2 PLN-013)."""

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder


class RequestParserInstructions:

    class Prompts:

        class _RoleAndScope:

            def stable(state: MABaseGraphState, *args, **kwds) -> Prompt:

                message = (
                    "You are a request analysis specialist for SaferPlaces, an AI-assisted geospatial analysis platform for climate data.\n"
                    "Your only job is to extract structured intent and entities from the user's natural language input.\n"
                    "You do not plan actions, execute tools, or generate responses to the user."
                )

                return Prompt(dict(
                    header = "[ROLE and SCOPE]",
                    message = message
                ))
            
        class _GlobalContext:

            def stable(state: MABaseGraphState) -> Prompt:

                layer_context = LayersAgentPrompts.BasicLayerSummary.stable(state)

                # map_context = MapAgentPrompts.MapContext

                conversation_context = Prompt(dict(
                    header = "[CONVERSATION HISTORY]",
                    message = ContextBuilder.conversation_history(state, max_messages=5)
                ))

                message = (
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

            def stable(state: MABaseGraphState) -> Prompt:

                raw_text = state['messages'][-1].content

                message = (
                    f"Parse the following user message and return a ParsedRequest JSON.\n"
                    f"User message: \"{raw_text}\""
                )
        
                return Prompt(dict(
                    header = "[TASK INSTRUCTION]",
                    message = message
                ))
            
            def only_instruction(state: MABaseGraphState) -> Prompt:

                message = (
                    f"Parse the following user message and return a ParsedRequest JSON.\n"
                )
        
                return Prompt(dict(
                    header = "[TASK INSTRUCTION]",
                    message = message
                ))
            
        class _ParsedRequest:

            def stable(state: MABaseGraphState) -> Prompt:

                parsed_request = state.get("parsed_request", {})
                
                if not parsed_request:
                    message = "No parsed request available."
                else:
                    message = (
                        f"[intent]: {parsed_request.get('intent', 'N/A')}\n"
                        f"[request_type]: {parsed_request.get('request_type', 'N/A')}\n"
                    )

                return Prompt(dict(
                    header = "[PARSED REQUEST]",
                    message = message
                ))

                  
    class Invocations:
        
        class ParseOneShot:

            def stable(state: MABaseGraphState) -> Prompt:

                role_and_scope = RequestParserInstructions.Prompts._RoleAndScope.stable(state)
                global_context = RequestParserInstructions.Prompts._GlobalContext.stable(state)
                task_instruction = RequestParserInstructions.Prompts._TaskInstruction.stable(state)

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
            
        class ParseMultiPrompt:

            def stable(state: MABaseGraphState) -> Prompt:

                role_and_scope = RequestParserInstructions.Prompts._RoleAndScope.stable(state)
                global_context = RequestParserInstructions.Prompts._GlobalContext.stable(state)
                task_instruction = RequestParserInstructions.Prompts._TaskInstruction.stable(state)

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





    class MainContext:

        @staticmethod
        def stable(layer_summary: str = "No layers available.", shapes_summary: str = "No shapes registered.", **kwargs) -> Prompt:
            p = {
                "title": "RequestAnalyzerContext",
                "description": "System prompt for 2-stage structured request analysis",
                "command": "",
                "message": (
                    "You are a request analyzer for a geospatial AI platform that manages flood simulations, "
                    "meteorological data retrieval, and digital twin generation.\n"
                    "\n"
                    "## Your task\n"
                    "Analyze the user's message and produce a structured JSON output with these fields:\n"
                    "\n"
                    "### Fields\n"
                    "- **intent** (string): Short phrase describing the main goal.\n"
                    "- **request_type** (string): One of:\n"
                    '  - `"action"` — user wants something executed (simulation, data retrieval, layer creation)\n'
                    '  - `"info"` — user asks a question or wants information\n'
                    '  - `"analysis"` — user wants analysis of existing data\n'
                    '  - `"clarification"` — user is responding to a previous question\n'
                    "- **entities** (list of objects): Each entity has:\n"
                    '  - `name`: as mentioned by user (e.g. "Roma", "50mm", "SRI")\n'
                    '  - `entity_type`: one of `location`, `layer`, `model`, `product`, `date`, `parameter`\n'
                    '  - `resolved`: optional dict with inferred metadata. For locations: `{"country": "IT", "approx_bbox": [west, south, east, north]}`. '
                    'For products: `{"code": "SRI", "source": "DPC"}`. Leave null if uncertain.\n'
                    "- **parameters** (dict): Explicit parameters extracted from the request:\n"
                    '  - `bbox`: bounding box if specified `{"west": ..., "south": ..., "east": ..., "north": ...}`\n'
                    "  - `rainfall_mm`: rainfall amount in mm if specified\n"
                    "  - `product`: product code if specified (SRI, VMI, SRT24, PRECIPITATION, etc.)\n"
                    "  - `duration_hours`: duration if specified\n"
                    "  - `time_start`, `time_end`: ISO8601 timestamps if specified\n"
                    "  - `pixelsize`: resolution in meters if specified\n"
                    "  - Any other explicit parameter mentioned\n"
                    "- **implicit_requirements** (list of strings): Requirements that can be inferred. Examples:\n"
                    '  - "needs DEM for the target area" (for flood simulations when no DEM exists)\n'
                    '  - "needs bbox" (when location is named but no coordinates given)\n'
                    '  - "needs rainfall data" (when simulation requires rainfall raster not in context)\n'
                    "- **raw_text** (string): Verbatim copy of the user's message.\n"
                    "\n"
                    "## Context resolution rules\n"
                    "1. For **locations**: resolve well-known cities/regions to approximate bounding boxes.\n"
                    '   - Roma → `{"country": "IT", "approx_bbox": [12.35, 41.80, 12.60, 41.99]}`\n'
                    '   - Milano → `{"country": "IT", "approx_bbox": [9.05, 45.40, 9.28, 45.53]}`\n'
                    '   - Nord Italia → `{"country": "IT", "approx_bbox": [6.6, 44.0, 14.0, 47.1]}`\n'
                    "2. For **parameters**: extract only what is explicitly stated. Do NOT invent values.\n"
                    "3. If the request mentions a location and layers already exist for that area, "
                    "note it in implicit_requirements (e.g. 'DEM already available for Roma').\n"
                    "4. If the user specifies a location name but no bbox, resolve the approx_bbox in the entity — "
                    "the system can use the resolved bbox.\n"
                    "5. When the user references an existing layer by name (or describes a layer), resolve it to the "
                    "matching layer from the Available Layers list. Include the layer's title and src in the resolved entity.\n"
                    "6. When the user says 'in the area of layer X' or 'use the bbox of layer X', resolve the bbox from "
                    "the layer's metadata and include it in the parameters.\n"
                    "\n"
                    "## Available layers in current project\n"
                    f"{layer_summary}\n"
                    "\n"
                    "## Shapes registered by the user\n"
                    "These are geometric areas drawn by the user on the map. "
                    "They can be used as spatial input (e.g. bounding box for simulations or analysis). "
                    "When the user refers to 'the area I drew', 'my bbox', 'the selected zone', match it to one of these.\n"
                    f"{shapes_summary}\n"
                    "\n"
                    "## Platform capabilities (for implicit_requirements detection)\n"
                    "- **Flood simulation** (SaferRain): requires DEM + rainfall (constant mm or raster)\n"
                    "- **Digital Twin** (DigitalTwinTool): creates DEM + buildings + land-use from bbox\n"
                    "- **DPC data retrieval**: Italian radar data (SRI, VMI, SRT*, etc.) — Italy only, past data\n"
                    "- **Meteoblue retrieval**: global weather forecasts (PRECIPITATION, TEMPERATURE, etc.) — future data\n"
                    "\n"
                    "## Important\n"
                    "- Be precise and concise.\n"
                    "- Do NOT hallucinate entities or parameters not present in the user's message.\n"
                    "- If the user's message is a simple greeting or general question, set request_type to `info` "
                    "and leave entities, parameters, and implicit_requirements empty."
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Previous stable version — preserved for test override compatibility."""
            p = {
                "title": "RequestParserContext",
                "description": "System prompt per il parsing strutturato delle richieste utente",
                "command": "",
                "message": (
                    "You are an expert assistant that converts user requests into a structured execution request.\n"
                    "\n"
                    "Your tasks:\n"
                    "- Extract the main high-level intent of the request (as a short phrase).\n"
                    "- Extract a list of relevant entities explicitly mentioned in the request.\n"
                    "- Extract explicit parameters only if they are clearly stated.\n"
                    "- Copy the original user input as a field.\n"
                    "- Do not invent or hallucinate information. If a field is not present, leave it empty or as an empty list.\n"
                    "\n"
                    "Be precise, concise, and execution-oriented."
                )
            }
            return Prompt(p)
