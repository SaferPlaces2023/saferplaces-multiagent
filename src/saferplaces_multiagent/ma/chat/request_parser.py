import json

from typing import List, Optional
from pydantic import BaseModel, Field

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage


class ParsedRequest(BaseModel):
    intent: str = Field(description="Main high-level intent")
    entities: List[str] = Field(default_factory=list, description="List of relevant entities")
    raw_text: str = Field(description="Original user input text")


class Prompts:

    SYSTEM_REQUEST_PROMPT = '\n'.join((
        "You are an expert assistant that converts user requests into a structured execution request.",
        "",
        "Your tasks:",
        "- Extract the main high-level intent of the request (as a short phrase).",
        "- Extract a list of relevant entities explicitly mentioned in the request.",
        "- Extract explicit parameters only if they are clearly stated.",
        "- Copy the original user input as a field.",
        "- Do not invent or hallucinate information. If a field is not present, leave it empty or as an empty list.",
        "",
        "Be precise, concise, and execution-oriented."
    ))


class RequestParser:

    def __init__(self):
        self.name = NodeNames.REQUEST_PARSER
        self.llm = _base_llm.with_structured_output(ParsedRequest)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.REQUEST_PARSER}] → Parsing request...")

        # Initialize new cycle: clear previous request state
        StateManager.initialize_new_cycle(state)

        # if state.get("awaiting_user"):
        #     state['awaiting_user'] = False
        #     return state
        if len(state["messages"]) == 0:
            return state

        if not isinstance(state["messages"][-1], HumanMessage):
            return state
        
        input_prompt = state["messages"][-1].content
        invoke_messages = [
            *state["messages"][:-1],
            SystemMessage(content=Prompts.SYSTEM_REQUEST_PROMPT),
            HumanMessage(content=input_prompt)
        ]

        parsed: ParsedRequest = self.llm.invoke(invoke_messages)

        state['awaiting_user'] = False

        state["parsed_request"] = parsed.model_dump()
        print(f"[{NodeNames.REQUEST_PARSER}] ✓ Intent: {parsed.intent}")
        return state
