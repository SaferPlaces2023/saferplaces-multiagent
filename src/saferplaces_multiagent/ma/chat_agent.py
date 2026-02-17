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
        self.llm = _base_llm.with_structured_output(ParsedRequest, include_raw=True)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        user_input = state["messages"][-1].content

        if not isinstance(state["messages"][-1], HumanMessage):
            return state

        try:
            llm_messages = [
                # {"role": "system", "content": Prompts.sys_standardize},
                {"role": "user", "content": user_input}
            ]
            result = self.llm.invoke(llm_messages)
            parsed: ParsedRequest = result["parsed"]
            raw_msg = result["raw"]  # AIMessage with response_metadata
            
            # Extract token usage from the raw AIMessage
            usage = getattr(raw_msg, 'usage_metadata', None) or {}
            state["llm_metadata"] = {
                "chat_agent": {
                    "model": raw_msg.response_metadata.get("model_name", "gpt-4o-mini"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "messages": {
                        "system": None,  # system prompt disabled
                        "user": user_input,
                        "assistant": raw_msg.content,
                    },
                    "parsed_output": parsed.model_dump(),
                }
            }
        except Exception as e:
            print(f'Error parsing request: {e}')
            parsed = ParsedRequest(
                intent="unknown",
                raw_text=user_input
            )
            state["llm_metadata"] = { "chat_agent": { "error": str(e) } }

        state["parsed_request"] = parsed.model_dump()
        return state
