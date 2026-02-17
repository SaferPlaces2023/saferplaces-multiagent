import json
import os

from typing import List, Optional
from pydantic import BaseModel, Field

from ..common.states import MABaseGraphState
from ..common.utils import _base_llm

from langchain_core.messages import HumanMessage


# ---------------------------------------------------------------------------
#  Load external prompts from prompts.json (same directory as this file)
# ---------------------------------------------------------------------------
_PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "prompts.json")

def _load_prompts():
    with open(_PROMPTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class ParsedRequest(BaseModel):
    intent: str = Field(description="Main high-level intent")
    entities: List[str] = Field(default_factory=list, description="List of relevant entities")
    raw_text: str = Field(description="Original user input text")


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

        # Load prompts from external JSON (re-read each call so edits take effect)
        prompts = _load_prompts().get("chat_agent", {})
        sys_prompt = prompts.get("system")  # None when disabled

        try:
            llm_messages = []
            if sys_prompt:
                llm_messages.append({"role": "system", "content": sys_prompt})
            llm_messages.append({"role": "user", "content": user_input})
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
                    "response_format": ParsedRequest.model_json_schema(),
                    "input": {
                        "system": sys_prompt,
                        "user": user_input,
                    },
                    "output": {
                        "raw": raw_msg.content,
                        "parsed": parsed.model_dump(),
                    },
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
