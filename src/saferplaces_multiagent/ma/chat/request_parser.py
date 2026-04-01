from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState, StateManager, build_nowtime_system_message
from ...common.base_models import Thought, ParsedRequest
from ...common.utils import _base_llm, random_id8
from ..names import NodeNames
from ..prompts import request_parser_prompts

from langchain_core.messages import HumanMessage, SystemMessage


class RequestParser(MultiAgentNode):

    # ParsedRequest JSON can be large — use a higher completion budget than the global default.
    _PARSE_MAX_TOKENS = 15000
    # Number of past messages to include as context (prevents unbounded prompt growth).
    _MAX_HISTORY_MESSAGES = 8

    def __init__(
        self,
        name: str = NodeNames.REQUEST_PARSER,
        log_state: bool = True,
        update_CoT: bool = True
    ):
        super().__init__(name, log_state, update_CoT)
        # self.llm = _base_llm.bind(max_completion_tokens=self._PARSE_MAX_TOKENS).with_structured_output(ParsedRequest)
        self.llm = _base_llm.with_structured_output(ParsedRequest)

    def _define_CoT(self, state) -> list[Thought]:
        cot = []
        if state['parsed_request']:
            cot.append(
                Thought(
                    # id_=random_id8(),
                    owner=self.name,
                    message=f"Pensando a [ {state['parsed_request']['intent']} ] ...",
                    payload=state['parsed_request']
                )
            )
        return cot


    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.REQUEST_PARSER}] → Parsing request...")

        if len(state["messages"]) > 0 and not isinstance(state["messages"][-1], HumanMessage):
            return state
        
        # Initialize new cycle: clear previous request state
        StateManager.initialize_new_cycle(state)
        
        if len(state["messages"]) == 0:          
            return state

        prompt_input = state["messages"][-1].content

        parsed = self._analyze_request(state, prompt_input)

        state["parsed_request"] = parsed.model_dump()
        print(f"[{NodeNames.REQUEST_PARSER}] ✓ Intent: {parsed.intent} | Type: {parsed.request_type}")
        return state

    def _analyze_request(self, state: MABaseGraphState, prompt_input: str) -> ParsedRequest:
        """Extract structured request with entities, parameters, and implicit requirements."""
        # Build context about existing layers for the analyzer
        # Priority: relevant_layers (processed by LayersAgent) > layer_registry (raw, always available)
        layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
        if not layers:
            layers = state.get("layer_registry", [])
        
        # Minipatch - don't know why but sometimes relevant_layers_list is encapsulated in a list
        if isinstance(layers, list) and len(layers) == 1 and isinstance(layers[0], list):
            layers = layers[0]
        
        layer_summary = self._summarize_layers(layers) if layers else "No layers available in the project."

        shapes_summary = self._summarize_shapes(state.get("shapes_registry") or [])

        prompt_context = request_parser_prompts.RequestParserPrompts.MainContext.stable(
            layer_summary=layer_summary,
            shapes_summary=shapes_summary,
        )
        # Limit history to avoid unbounded prompt growth over long conversations.
        history = state["messages"][:-1][-self._MAX_HISTORY_MESSAGES:]
        invoke_messages = [
            build_nowtime_system_message(),
            *history,
            SystemMessage(content=prompt_context.message),
            HumanMessage(content=prompt_input)
        ]

        print([m.content for m in invoke_messages])

        parsed: ParsedRequest = self.llm.invoke(invoke_messages)
        return parsed

    @staticmethod
    def _summarize_shapes(shapes: list) -> str:
        """Produce a concise text summary of registered shapes."""
        if not shapes:
            return "No shapes registered by the user."
        lines = []
        for s in shapes:
            shape_id = s.get("shape_id", "?")
            shape_type = s.get("shape_type", "unknown")
            label = s.get("label", "")
            entry = f"• {shape_id} ({shape_type})"
            if label:
                entry += f" — {label}"
            lines.append(entry)
        return "\n".join(lines)

    @staticmethod
    def _summarize_layers(layers: list) -> str:
        """Produce a concise text summary of available layers for the analyzer."""
        if not layers:
            return "No layers available."
        summaries = []
        for l in layers:
            print('[DEBUG] Layer:', l)
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
