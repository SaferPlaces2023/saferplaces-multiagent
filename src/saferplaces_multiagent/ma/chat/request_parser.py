import json

import pandas as pd

from typing import List, Optional
from pydantic import BaseModel, Field

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState, StateManager, build_nowtime_system_message
from ...common.base_models import ParsedRequest, Entity, RequestType
from ...common.utils import _base_llm
from ..names import NodeNames
from ..prompts import request_parser_prompts

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.types import interrupt


# Maximum clarification iterations before auto-proceeding
_MAX_CLARIFY_ITERATIONS = 2


class RequestParser(MultiAgentNode):

    def __init__(self, name: str = NodeNames.REQUEST_PARSER, log_state: bool = True):
        super().__init__(name, log_state)
        self.llm = _base_llm.with_structured_output(ParsedRequest)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.REQUEST_PARSER}] → Parsing request...")

        # Initialize new cycle: clear previous request state
        StateManager.initialize_new_cycle(state)

        if len(state["messages"]) == 0:
            return state

        if not isinstance(state["messages"][-1], HumanMessage):
            return state

        prompt_input = state["messages"][-1].content

        # --- Stage 1: Structured analysis with entity resolution ---
        parsed = self._analyze_request(state, prompt_input)

        # --- Stage 2: Clarification Gate ---
        parsed = self._clarification_gate(state, parsed)

        state["parsed_request"] = parsed.model_dump()
        print(f"[{NodeNames.REQUEST_PARSER}] ✓ Intent: {parsed.intent} | Type: {parsed.request_type}")
        return state

    def _analyze_request(self, state: MABaseGraphState, prompt_input: str) -> ParsedRequest:
        """Stage 1 — Extract structured request with entities, ambiguities, requirements."""
        # Build context about existing layers for the analyzer
        # Priority: relevant_layers (processed by LayersAgent) > layer_registry (raw, always available)
        layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
        if not layers:
            layers = state.get("layer_registry", [])
        
        # Minipatch - don't know why but sometimes relevant_layers_list is encapsulated in a list
        if isinstance(layers, list) and len(layers) == 1 and isinstance(layers[0], list):
            layers = layers[0]
        
        layer_summary = self._summarize_layers(layers) if layers else "No layers available in the project."

        prompt_context = request_parser_prompts.RequestParserPrompts.MainContext.stable(
            layer_summary=layer_summary
        )
        invoke_messages = [
            build_nowtime_system_message(),
            *state["messages"][:-1],
            SystemMessage(content=prompt_context.message),
            HumanMessage(content=prompt_input)
        ]

        parsed: ParsedRequest = self.llm.invoke(invoke_messages)
        return parsed

    def _clarification_gate(self, state: MABaseGraphState, parsed: ParsedRequest) -> ParsedRequest:
        """Stage 2 — If critical ambiguities exist for action requests, ask user for clarification."""
        if parsed.request_type != "action":
            return parsed
        if not parsed.ambiguities:
            return parsed

        # Respect interaction budget
        interaction_count = state.get("interaction_count", 0)
        interaction_budget = state.get("interaction_budget", 8)
        if interaction_count >= interaction_budget:
            return parsed

        # Build clarification message
        clarify_msg = self._build_clarification_message(parsed)

        # Interrupt to ask user
        interruption = interrupt({
            "content": clarify_msg,
            "interrupt_type": "request-clarification",
        })

        user_response = interruption.get("response", "") if isinstance(interruption, dict) else str(interruption)

        # Bump interaction counter
        state["interaction_count"] = interaction_count + 1

        # Re-analyze with the user's clarification folded in
        enriched_input = (
            f"{parsed.raw_text}\n\n"
            f"[User clarification]: {user_response}"
        )
        parsed = self._analyze_request(state, enriched_input)
        return parsed

    def _build_clarification_message(self, parsed: ParsedRequest) -> str:
        """Build a natural clarification message from detected ambiguities."""
        lines = []
        intent_summary = parsed.intent
        lines.append(f"Ho capito che vuoi: **{intent_summary}**")
        lines.append("Però ho bisogno di alcuni chiarimenti:\n")
        for i, amb in enumerate(parsed.ambiguities, 1):
            lines.append(f"  {i}. {amb}")
        lines.append("")
        lines.append('Rispondi ai punti che preferisci, oppure scrivi "procedi" per far decidere all\'agente.')
        return "\n".join(lines)

    @staticmethod
    def _summarize_layers(layers: list) -> str:
        """Produce a concise text summary of available layers for the analyzer."""
        if not layers:
            return "No layers available."
        summaries = []
        for l in layers:
            title = l.get("title", "untitled")
            ltype = l.get("type", "unknown")
            src = l.get("src", "")
            desc = l.get("description", "")
            meta = l.get("metadata", {})

            line = f"• {title} ({ltype})"
            if desc:
                line += f" — {desc}"

            details = []
            if meta:
                bbox = meta.get("bbox")
                if bbox:
                    details.append(f"bbox={bbox}")
                band = meta.get("band")
                if band:
                    details.append(f"band={band}")
                res = meta.get("pixelsize") or meta.get("resolution")
                if res:
                    details.append(f"res={res}m")
            if src:
                details.append(f"src={src}")

            if details:
                line += f"\n  [{', '.join(details)}]"
            summaries.append(line)
        return "\n".join(summaries)
