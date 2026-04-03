"""Final responder prompts for generating user-facing responses."""

from typing import Dict, Any

from langchain_core.messages import SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.context_builder import ContextBuilder

from . import Prompt
from .layers_agent_promps import LayersAgentPrompts
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
                    conversation_context = Prompt(dict(
                        header="[CONVERSATION HISTORY]",
                        message=ContextBuilder.conversation_history(state, max_messages=10)
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



class FinalResponderPrompts:
    """Prompts for the final response generation stage."""

    class Response:
        """System prompts for generating the final response.

        ``stable()`` inspects the execution narrative (when available) to select
        the most appropriate response style.
        """

        # -- Response‐style variants (§4D PLN‐013) ----------------------------------

        _BASE_RULES = (
            "Respond in the same language as the user's original request.\n"
            "Do not invent information; base your answer strictly on the available data.\n"
            "Be concise but helpful."
        )

        @staticmethod
        def _completed_plan() -> str:
            """Plan with completed steps — results + map guidance + suggestions."""
            return (
                "You are a geospatial AI assistant reporting the results of an execution plan.\n"
                "\n"
                "Instructions:\n"
                "- Summarize what was done and the key outcomes (layers created, data retrieved).\n"
                "- If new layers were created, tell the user they are visible on the map.\n"
                "- If errors occurred in some steps, mention them briefly and suggest how to fix them.\n"
                "- Suggest concrete next steps the user could take (e.g., run a simulation on the new DEM, "
                "retrieve rainfall data, change parameters).\n"
                "- " + FinalResponderPrompts.Response._BASE_RULES
            )

        @staticmethod
        def _info_response() -> str:
            """Empty plan — user asked a question, no actions executed."""
            return (
                "You are a geospatial AI assistant for the SaferPlaces platform.\n"
                "\n"
                "## Platform capabilities\n"
                "The platform can:\n"
                "1. **Flood simulation** (SaferRain): simulate flooding from constant rainfall (mm) "
                "or retrieved rainfall rasters. Requires a DEM.\n"
                "2. **Digital Twin creation** (DigitalTwinTool): generate DEM + buildings"
                "for any area from a bounding box.\n"
                "3. **DPC meteorological data** (Italian Civil Protection): retrieve radar rainfall, "
                "precipitation, temperature, lightning data for Italy. Past/recent data only.\n"
                "   Products: SRI, VMI, SRT1/3/6/12/24, TEMP, LTG, IR108, HRD.\n"
                "4. **Meteoblue weather forecasts**: retrieve global weather forecasts for "
                "precipitation, temperature, wind, humidity. Future data up to 14 days.\n"
                "\n"
                "## Available layers\n"
                "The user's project has layers visible in the context below. Describe them when asked.\n"
                "\n"
                "## Registered shapes\n"
                "The user may have drawn shapes (bounding boxes, polygons, etc.) on the map. "
                "They are listed in the context below with their shape_id and type. Describe them when asked.\n"
                "\n"
                "Instructions:\n"
                "- Answer questions about the platform precisely using the capabilities listed above.\n"
                "- When asked about layers, list the layers from the context with their properties.\n"
                "- When asked about shapes or drawn areas, list the shapes with shape_id, type, and label.\n"
                "- If the user could benefit from an action (simulation, data retrieval), suggest it as a follow-up.\n"
                "- " + FinalResponderPrompts.Response._BASE_RULES
            )

        @staticmethod
        def _aborted_plan() -> str:
            """Plan aborted by the user — closure with partial summary."""
            return (
                "You are a geospatial AI assistant. The user cancelled the execution plan.\n"
                "\n"
                "Instructions:\n"
                "- Acknowledge the cancellation politely.\n"
                "- If any steps were completed before the abort, briefly summarize what was accomplished.\n"
                "- Offer to help with a different approach or a new request.\n"
                "- " + FinalResponderPrompts.Response._BASE_RULES
            )

        @staticmethod
        def _error_response() -> str:
            """Errors during execution — report + recovery suggestions."""
            return (
                "You are a geospatial AI assistant reporting on an execution that encountered errors.\n"
                "\n"
                "Instructions:\n"
                "- Explain what went wrong in clear, non-technical terms.\n"
                "- If some steps succeeded, mention their results.\n"
                "- Provide concrete recovery suggestions (retry, change parameters, try a different approach).\n"
                "- " + FinalResponderPrompts.Response._BASE_RULES
            )

        @classmethod
        def _select_variant(cls, state: MABaseGraphState) -> str:
            """Pick the right system prompt variant based on execution narrative."""
            narrative = state.get("execution_narrative")
            plan = state.get("plan")
            plan_confirmation = state.get("plan_confirmation")

            # Case 1: user aborted
            if plan_confirmation == "aborted":
                return cls._aborted_plan()

            # Case 2: has narrative → inspect completion status
            if narrative:
                status = narrative.get_completion_status()
                if status == "failed":
                    return cls._error_response()
                if narrative.errors:
                    return cls._error_response()
                if status in ("completed", "partial"):
                    return cls._completed_plan()

            # Case 3: plan executed (no narrative but plan exists)
            if plan:
                return cls._completed_plan()

            # Case 4: no plan / empty plan → informational
            return cls._info_response()

        @staticmethod
        def stable(state: MABaseGraphState = None, **kwargs) -> Prompt:
            """Context-aware response prompt (§4D PLN-013).

            When *state* is provided the prompt variant is chosen automatically
            based on the execution narrative status.  Falls back to a generic
            prompt when state is ``None`` (backward compatibility).
            """
            if state is not None:
                message = FinalResponderPrompts.Response._select_variant(state)
            else:
                message = (
                    "You are an expert assistant responsible for generating the final response to the user.\n"
                    "\n"
                    "Instructions:\n"
                    "- Write a clear, concise, and helpful answer based on the provided context and tool results.\n"
                    "- If there are errors or issues, explain them clearly and suggest possible next steps.\n"
                    "- If the context involves geospatial data or map layers, ensure your answer is relevant and informative.\n"
                    "- Respond in the same language as the user's original request, unless otherwise specified.\n"
                    "- Do not invent information; base your answer strictly on the available data."
                )

            p = {
                "title": "FinalResponse",
                "description": "System prompt per generare la risposta finale all'utente",
                "command": "",
                "message": message,
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Minimal version for testing."""
            p = {
                "title": "FinalResponse",
                "description": "Minimal response system prompt",
                "command": "",
                "message": (
                    "Generate a clear and concise response based on the provided context.\n"
                    "Do not invent information."
                )
            }
            return Prompt(p)

    class Context:
        """Context prompts for providing state information to the final response."""

        class Formatted:
            """Formatted context in human-readable text."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Stable version: uses execution_narrative if available (§3 PLN-013), else formatted state."""
                # PHASE 1: Try to build context from ExecutionNarrative (§3)
                narrative = state.get('execution_narrative')
                
                if narrative:
                    # Use narrative-based context (more structured and useful)
                    context_msg = FinalResponderPrompts.Context.Formatted._build_from_narrative(narrative, state)
                else:
                    # Fallback: build from raw state (backward compatibility)
                    context_msg = FinalResponderPrompts.Context.Formatted._build_from_state(state)
                
                p = {
                    "title": "FormattedContext",
                    "description": "Formatta lo stato in testo leggibile",
                    "command": "",
                    "message": context_msg
                }
                return Prompt(p)
            
            @staticmethod
            def _build_from_narrative(narrative, state: MABaseGraphState) -> str:
                """Build context from execution narrative (§3 PLN-013)."""
                lines = []
                
                lines.append("=== EXECUTION SUMMARY ===")
                
                if narrative.request_summary:
                    lines.append(f"\n📋 Richiesta: {narrative.request_summary}")
                    if narrative.request_type:
                        lines.append(f"   Tipo: {narrative.request_type}")
                
                if narrative.plan_summary:
                    lines.append(f"\n📌 Piano: {narrative.plan_summary}")
                    lines.append(f"   Step totali: {narrative.total_steps}")
                
                # Completed steps
                if narrative.steps_executed:
                    lines.append(f"\n✅ Step completati: {len(narrative.steps_executed)}")
                    for step in narrative.steps_executed:
                        lines.append(f"   [{step.step_index+1}] {step.agent}: {step.output_summary or step.goal}")
                
                # Layers created
                if narrative.layers_created:
                    lines.append(f"\n📊 Layer creati: {len(narrative.layers_created)}")
                    for layer in narrative.layers_created:
                        lines.append(f"   • {layer.name} ({layer.layer_type})")
                        if layer.description:
                            lines.append(f"     {layer.description}")
                
                # Errors
                if narrative.errors:
                    lines.append(f"\n❌ Errori: {len(narrative.errors)}")
                    for error in narrative.errors:
                        lines.append(f"   [{error.tool_name}] {error.message}")
                        if error.recovery_suggestion:
                            lines.append(f"     Suggerimento: {error.recovery_suggestion}")
                
                # User interactions
                if narrative.user_interactions:
                    lines.append(f"\n🔄 Interazioni: {len(narrative.user_interactions)}")
                    for interaction in narrative.user_interactions:
                        lines.append(f"   • {interaction}")
                
                # Suggestions
                if narrative.suggestions:
                    lines.append(f"\n💡 Suggerimenti per i prossimi step:")
                    for suggestion in narrative.suggestions:
                        lines.append(f"   • {suggestion}")
                
                # Project layers (FIX-04: layer registry in FinalResponder context)
                layer_registry = state.get("layer_registry", [])
                if layer_registry:
                    lines.append(f"\n📂 Layer nel progetto: {len(layer_registry)}")
                    for l in layer_registry:
                        title = l.get("title", "untitled")
                        ltype = l.get("type", "unknown")
                        desc = l.get("description", "")
                        meta = l.get("metadata", {})
                        line = f"   • {title} ({ltype})"
                        if desc:
                            line += f" — {desc}"
                        if meta and meta.get("bbox"):
                            line += f" [bbox: {meta['bbox']}]"
                        lines.append(line)

                # Registered shapes
                shapes_registry = state.get("shapes_registry") or []
                if shapes_registry:
                    lines.append(f"\n🗺 Shape registrate: {len(shapes_registry)}")
                    for s in shapes_registry:
                        sid = s.get("shape_id", "?")
                        stype = s.get("shape_type", "unknown")
                        label = s.get("label", "")
                        entry = f"   • {sid} ({stype})"
                        if label:
                            entry += f" — {label}"
                        lines.append(entry)
                
                lines.append(f"\n📊 Status: {narrative.get_completion_status()}")
                lines.append("\n=== END SUMMARY ===\n")
                
                return "\n".join(lines)
            
            @staticmethod
            def _build_from_state(state: MABaseGraphState) -> str:
                """Build context from raw state (backward compatibility)."""
                parsed_request = state.get('parsed_request') or {}
                intent = parsed_request.get('intent', 'N/A')
                raw_entities = parsed_request.get('entities', [])
                # Entities are now dicts with 'name' key (Entity model), handle both old and new format
                entity_names = []
                for e in raw_entities:
                    if isinstance(e, dict):
                        entity_names.append(e.get('name', str(e)))
                    else:
                        entity_names.append(str(e))
                entities = ', '.join(entity_names) or 'N/A'
                plan = state.get('plan', 'N/A')
                tool_results = state.get('tool_results', 'N/A')
                error = state.get('error', 'None')
                raw_text = parsed_request.get('raw_text', 'N/A')
                
                context = (
                    "Context for your answer:\n"
                    f"- User intent: {intent}\n"
                    f"- Entities: {entities}\n"
                    f"- Plan: {plan}\n"
                    f"- Tool results: {tool_results}\n"
                    f"- Error: {error}\n"
                    f"- Original user input: {raw_text}\n"
                )
                
                # Add layer registry (FIX-04)
                layer_registry = state.get('layer_registry', [])
                if layer_registry:
                    context += f"\nProject layers ({len(layer_registry)}):\n"
                    for l in layer_registry:
                        title = l.get('title', 'untitled')
                        ltype = l.get('type', 'unknown')
                        desc = l.get('description', '')
                        line = f"  • {title} ({ltype})"
                        if desc:
                            line += f" — {desc}"
                        context += line + "\n"

                # Registered shapes
                shapes_registry = state.get('shapes_registry') or []
                if shapes_registry:
                    context += f"\nRegistered shapes ({len(shapes_registry)}):\n"
                    for s in shapes_registry:
                        sid = s.get('shape_id', '?')
                        stype = s.get('shape_type', 'unknown')
                        label = s.get('label', '')
                        entry = f"  • {sid} ({stype})"
                        if label:
                            entry += f" — {label}"
                        context += entry + "\n"
                
                return context

            @staticmethod
            def v001(state: MABaseGraphState, **kwargs) -> Prompt:
                """Minimal version: only key fields."""
                parsed_request = state.get('parsed_request') or {}
                intent = parsed_request.get('intent', 'N/A')
                plan = state.get('plan', 'N/A')
                error = state.get('error', 'None')
                
                p = {
                    "title": "FormattedContext",
                    "description": "Minimal formatted context",
                    "command": "",
                    "message": (
                        "Context for your answer:\n"
                        f"- User intent: {intent}\n"
                        f"- Plan: {plan}\n"
                        f"- Error: {error}\n"
                    )
                }
                return Prompt(p)
