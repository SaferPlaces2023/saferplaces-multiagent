import json

import pandas as pd

from typing import List, Optional
from pydantic import BaseModel, Field

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames
from ..prompts import request_parser_prompts

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage


class ParsedRequest(BaseModel):
    intent: str = Field(description="Main high-level intent")
    entities: List[str] = Field(default_factory=list, description="List of relevant entities")
    raw_text: str = Field(description="Original user input text")


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
        prompt_context = request_parser_prompts.RequestParserPrompts.MainContext.stable()
        invoke_messages = [
            *state["messages"][:-1],
            SystemMessage(content=prompt_context.message),
            HumanMessage(content=prompt_input)
        ]

        parsed: ParsedRequest = self.llm.invoke(invoke_messages)

        state['awaiting_user'] = False

        state["parsed_request"] = parsed.model_dump()
        print(f"[{NodeNames.REQUEST_PARSER}] ✓ Intent: {parsed.intent}")
        return state
