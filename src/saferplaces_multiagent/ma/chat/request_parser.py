from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState, StateManager, build_nowtime_system_message
from ...common.base_models import Thought, ParsedRequest
from ...common.utils import _base_llm, random_id8
from ..names import NodeNames
from ..prompts import request_parser_prompts
from ..prompts.request_parser_prompts import RequestParserInstructions

from langchain_core.messages import HumanMessage, SystemMessage, AnyMessage, BaseMessage


class RequestParser(MultiAgentNode):

    def __init__(
        self,
        name: str = NodeNames.REQUEST_PARSER,
        log_state: bool = True,
        update_CoT: bool = True
    ):
        super().__init__(name, log_state, update_CoT)
        self.llm = _base_llm.with_structured_output(ParsedRequest)


    def _define_CoT(self, state) -> list[Thought]:
        cot = []
        if state['parsed_request']:
            cot.append(
                Thought(
                    owner=self.name,
                    message=f"Pensando a [ {state['parsed_request']['intent']} ] ...",
                    payload=state['parsed_request']
                )
            )
        return cot


    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        # DOC: Initialize new cycle: clear previous request state
        StateManager.initialize_new_cycle(state)

        # DOC: Invocation
        invocation: list[BaseMessage] = RequestParserInstructions.Invocations.ParseOneShot.stable(state)
        parsed: ParsedRequest = self.llm.invoke(invocation)

        # DOC: Update state
        state["parsed_request"] = parsed.model_dump()
        state["supervisor_invocation_reason"] = "new_request"

        # DOC: Next node!
        print(f"[{NodeNames.REQUEST_PARSER}] ✓ Intent: {parsed.intent} | Type: {parsed.request_type}")
        return state
