import json

from typing import List, Optional
from pydantic import BaseModel, Field

from ..common.states import MABaseGraphState
from ..common.utils import _base_llm

from langchain_core.messages import HumanMessage


class ParsedRequest(BaseModel):
    intent: str = Field(description="Main high-level intent")
    entities: List[str] = Field(default_factory=list, description="List of relevant entities")
    raw_text: str = Field(description="Original user input text")


class Prompts:

    # sys_standardize = '\n'.join((
    #     "You convert user requests into a structured execution request. ",
    #     "",
    #     "Your job: ",
    #     "- Extract the high-level intent. ",
    #     "- Extract relevant entities. ",
    #     "- Extract explicit parameters. ",
    #     "- Determine if the request likely requires multiple execution steps. ",
    #     "",
    #     "Be precise and execution-oriented. ",
    #     "Do not hallucinate parameters.",
    # ))
    pass


class ChatAgent:

    def __init__(self):
        self.name = "ChatAgent"
        self.llm = _base_llm.with_structured_output(ParsedRequest)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        user_input = state["messages"][-1].content

        if not isinstance(state["messages"][-1], HumanMessage):
            return state

        try:
            parsed: ParsedRequest = self.llm.invoke([
                # {"role": "system", "content": Prompts.sys_standardize},
                {"role": "user", "content": user_input}
            ])
        except Exception as e:
            print(f'Error parsing request: {e}')
            parsed = ParsedRequest(
                intent="unknown",
                raw_text=user_input
            )

        state["parsed_request"] = parsed.model_dump()
        return state


import json
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

FINAL_PROMPT = """You are the final response writer.
Write a concise, user-facing answer in Italian.
Use the tool results to answer. If there is an error, explain it and propose next steps.
"""

class FinalChatAgent:
    def __init__(self):
        self.name = "ChatAgent"
        self.llm = _base_llm

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:   
        payload = {
            "parsed_request": state.get("parsed_request"),
            "plan": state.get("plan"),
            "tool_results": state.get("tool_results"),
            "error": state.get("error"),
        }

        resp = self.llm.invoke([
            SystemMessage(content=FINAL_PROMPT),
            HumanMessage(content=f"Context JSON:\n{json.dumps(payload, ensure_ascii=False)}")
        ])

        state["messages"] = [AIMessage(content=resp.content)]
        return state
