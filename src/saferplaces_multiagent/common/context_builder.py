"""Context Enrichment Pipeline (§1 of PLN-013).

Builds a rich, structured PlanningContext for the Supervisor Agent.
Tres phases:
1. Resolve geospatial context (layers summary)
2. Tool results history (what was produced in previous steps)
3. Semantically filtered conversation (only user/final/feedback messages)
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
import json

from saferplaces_multiagent.common.states import MABaseGraphState

from .base_models import compute_geometry_metadata

from langchain_core.messages import (
    BaseMessage, HumanMessage, AIMessage, SystemMessage, 
    ToolMessage, get_buffer_string
)

from . import utils


@dataclass
class PlanningContext:
    """Structured context for Supervisor planning."""
    
    # Parsed request summary
    request_intent: Optional[str] = None
    request_type: Optional[str] = None
    
    # Geospatial context (layer summary)
    available_layers_summary: Optional[str] = None

    # User-registered shapes (shapes_registry summary)
    available_shapes_summary: Optional[str] = None
    
    # Tool results from previous steps in this cycle
    previous_results_summary: Optional[str] = None
    
    # Conversation history (semantically filtered)
    conversation_history: Optional[str] = None
    
    def to_dict(self) -> dict:
        return asdict(self)


class ContextBuilder:
    """Builds enriched PlanningContext for Supervisor."""

    @staticmethod
    def build(state: Dict[str, Any]) -> PlanningContext:
        """
        Build comprehensive PlanningContext from state.
        
        Combines:
        1. Parsed request summary (intent + type)
        2. Available layers summary (readable, not JSON dump)
        3. Previous results summary (what happened in prior steps)
        4. Filtered conversation history (user messages + final responses + feedback)
        """
        context = PlanningContext()
        
        # Phase 1: Request summary
        parsed_request = state.get("parsed_request", {})
        if isinstance(parsed_request, dict):
            context.request_intent = parsed_request.get("intent")
            context.request_type = parsed_request.get("request_type")
        
        # Phase 2: Geospatial context enrichment
        context.available_layers_summary = ContextBuilder._build_layers_summary(state)

        # Phase 2b: Shapes registry context
        context.available_shapes_summary = ContextBuilder._build_shapes_summary(state)
        
        # Phase 3: Tool results history (if this is a re-planning scenario)
        current_step = state.get("current_step")
        if current_step and current_step > 0:
            context.previous_results_summary = ContextBuilder._build_previous_results(state)
        
        # Phase 4: Filtered conversation history
        context.conversation_history = ContextBuilder._filter_conversation_history(state)
        
        return context

    @staticmethod
    def _build_shapes_summary(state: Dict[str, Any]) -> Optional[str]:
        """Build human-readable summary of registered user shapes."""
        shapes_registry = state.get("shapes_registry") or []
        if not shapes_registry:
            return None

        summary_lines = [f"Shape registrate dall'utente: {len(shapes_registry)}"]
        for shape in shapes_registry:
            if not isinstance(shape, dict):
                continue
            shape_id = shape.get("shape_id", "?")
            shape_type = shape.get("shape_type", "unknown")
            label = shape.get("label")
            created_at = shape.get("created_at", "")
            geom = shape.get("geometry", {})
            geom_type = geom.get("type", "") if isinstance(geom, dict) else ""

            line = f"\n  • {shape_id}"
            if label:
                line += f" — {label}"
            summary_lines.append(line)
            summary_lines.append(f"    Tipo: {shape_type} ({geom_type})")
            # Spatial metadata
            meta = shape.get("metadata") or (compute_geometry_metadata(geom) if isinstance(geom, dict) else {})
            if meta:
                if "lon" in meta and "lat" in meta:
                    summary_lines.append(f"    Coordinate: lon={meta['lon']}, lat={meta['lat']}")
                if "bbox" in meta:
                    b = meta["bbox"]
                    summary_lines.append(
                        f"    Bbox: west={b['west']}, south={b['south']}, east={b['east']}, north={b['north']}"
                    )
                if "area_km2" in meta:
                    summary_lines.append(f"    Area: ~{meta['area_km2']} km²")
                if "length_km" in meta:
                    summary_lines.append(f"    Lunghezza: ~{meta['length_km']} km")
                if "num_features" in meta:
                    summary_lines.append(f"    N. sub-geometrie: {meta['num_features']}")
            if created_at:
                summary_lines.append(f"    Creata: {created_at}")

        return "\n".join(summary_lines)

    @staticmethod
    def _build_layers_summary(state: Dict[str, Any]) -> str:
        """Build human-readable summary of available layers.

        Uses ``relevant_layers`` (processed by LayersAgent) when available,
        falls back to the raw ``layer_registry`` (always present after
        ``restore_state``) so that the first planning cycle isn't blind.
        """
        layer_registry = state.get("layer_registry", [])
        additional_context = state.get("additional_context", {})
        relevant_layers = additional_context.get("relevant_layers", {})

        # Priority: relevant_layers (processed) > layer_registry (raw)
        layers_to_summarize = (
            relevant_layers.get("layers", [])
            if isinstance(relevant_layers, dict)
            else []
        )
        if not layers_to_summarize:
            layers_to_summarize = layer_registry

        if not layers_to_summarize:
            return "Nessun layer disponibile nel registro."

        summary_lines = [f"Layer disponibili: {len(layers_to_summarize)}"]

        for layer in layers_to_summarize:
            if not isinstance(layer, dict):
                continue
            title = layer.get("title", layer.get("name", "Unknown"))
            layer_type = layer.get("type", "unknown")
            src = layer.get("src", "")
            desc = layer.get("description", "")
            meta = layer.get("metadata", {})

            summary_lines.append(f"\n  • {title}")
            summary_lines.append(f"    Tipo: {layer_type}")
            if desc:
                summary_lines.append(f"    Descrizione: {desc}")
            if src:
                summary_lines.append(f"    Sorgente: {src}")
            if meta:
                bbox = meta.get("bbox")
                if bbox:
                    summary_lines.append(f"    Bbox: {bbox}")
                band = meta.get("band")
                if band is not None:
                    summary_lines.append(f"    Band: {band}")
                res = meta.get("pixelsize") or meta.get("resolution")
                if res:
                    summary_lines.append(f"    Risoluzione: {res}m")

        return "\n".join(summary_lines)

    @staticmethod
    def _build_previous_results(state: Dict[str, Any]) -> str:
        """
        Build summary of tool results from previous steps in the cycle.
        
        Example output:
          Ciclo precedente (step 1/2):
          ✓ models_subgraph → SaferRain
            Output: Digital Twin creato (WD raster 30m, 150 KB)
            Layer ID: dt_roma_001
          
          Step attuale: 2/2 → retriever → DPC data fetch
        """
        plan = state.get("plan", [])
        current_step = state.get("current_step", 0)
        tool_results = state.get("tool_results", {})
        
        if not plan or current_step <= 0:
            return None
        
        lines = []
        
        # Summarize completed steps
        completed_count = current_step
        if completed_count > 0:
            lines.append(f"Step completati: {completed_count}/{len(plan)}")
            
            for i in range(min(completed_count, len(plan))):
                step = plan[i]
                agent = step.get("agent", "unknown")
                goal = step.get("goal", "no goal")
                
                lines.append(f"\n  [{i+1}] {agent}")
                lines.append(f"      Obiettivo: {goal}")
                
                # Try to get result summary from tool_results or execution_narrative
                step_key = f"step_{i}"
                if step_key in tool_results:
                    lines.append(f"      Esito: ✓ Completato")
        
        # Current step info
        if current_step < len(plan):
            lines.append(f"\nStep attuale: [{current_step+1}] {plan[current_step].get('agent', '?')}")
        
        return "\n".join(lines) if lines else None

    @staticmethod
    def _filter_conversation_history(state: Dict[str, Any]) -> str:
        """
        Filter conversation history to include only semantically relevant messages:
        - Include: HumanMessage, final AIMessages with content (no tool_calls)
        - Exclude: ToolMessage, AIMessage with tool_calls, SystemMessage
        - Summarize: If history > threshold, generate a summary
        """
        messages = state.get("messages", [])
        
        if not messages:
            return "Nessuna conversazione precedente."
        
        filtered = []
        
        for msg in messages:
            # Include user messages
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                filtered.append(f"Utente: {content}")
            
            # Include final AI responses (with content, not tool_calls)
            elif isinstance(msg, AIMessage):
                # Skip if this is a tool-calling message (has tool_calls)
                if msg.tool_calls:
                    continue
                
                if msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    filtered.append(f"Assistente: {content}")
            
            # Skip ToolMessage and SystemMessage
            elif isinstance(msg, (ToolMessage, SystemMessage)):
                continue
        
        if not filtered:
            return "Nessun messaggio rilevante nella conversazione."
        
        # If history is long, keep only last N messages
        MAX_MESSAGES = 10
        if len(filtered) > MAX_MESSAGES:
            filtered = filtered[-MAX_MESSAGES:]
            return "\n".join(["(... conversazione precedente ...)", *filtered])
        
        return "\n".join(filtered)


    @staticmethod
    def format_for_prompt(context: PlanningContext) -> str:
        """Format PlanningContext as a readable prompt section."""
        lines = []
        
        lines.append("=== CONTESTO DI PLANNING ===")
        
        if context.request_intent:
            lines.append(f"\nRichiesta: {context.request_intent} (tipo: {context.request_type})")
        
        if context.available_layers_summary:
            lines.append(f"\n{context.available_layers_summary}")

        if context.available_shapes_summary:
            lines.append(f"\n{context.available_shapes_summary}")
        
        if context.previous_results_summary:
            lines.append(f"\n{context.previous_results_summary}")
        
        if context.conversation_history:
            lines.append(f"\nStorico conversazione:\n{context.conversation_history}")
        
        lines.append("\n=== FINE CONTESTO ===\n")
        
        return "\n".join(lines)
    



    @staticmethod
    def conversation_history(state: MABaseGraphState, max_messages: int = 10) -> str:
        """
        Filter conversation history to include only semantically relevant messages:
        - Include: HumanMessage, final AIMessages with content (no tool_calls)
        - Exclude: ToolMessage, AIMessage with tool_calls, SystemMessage
        - Summarize: If history > threshold, generate a summary
        """
        messages = state.get("messages", [])
        
        if not messages:
            return "Nessuna conversazione precedente."
        
        filtered = []
        
        for msg in messages:
            
            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                filtered.append(f"- [human]: {content}")
            
            elif isinstance(msg, AIMessage):
                
                if msg.tool_calls:
                    continue
                
                if msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    filtered.append(f"- [ai]: {content}")
            
            elif isinstance(msg, (ToolMessage, SystemMessage)):
                continue
        
        if not filtered:
            return "No previous messages."
        
        if len(filtered) > max_messages:
            filtered = filtered[-max_messages:]
        
        return "\n".join(filtered)
