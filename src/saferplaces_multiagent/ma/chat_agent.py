import json
import os
from enum import Enum

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, create_model

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
    """Fallback schema (used when no intents are defined in prompts.json)."""
    intent: str = Field(description="Main high-level intent")
    entities: List[str] = Field(default_factory=list, description="List of relevant entities")
    raw_text: str = Field(description="Original user input text")


def _build_parsed_request_model(intents: list[str] | None = None):
    """Build a ParsedRequest model dynamically.
    
    If `intents` is provided, the `intent` field becomes an enum
    so the JSON Schema contains an explicit list of allowed values.
    """
    if not intents:
        return ParsedRequest

    IntentEnum = Enum("IntentEnum", {v: v for v in intents})

    return create_model(
        "ParsedRequest",
        intent=(IntentEnum, Field(description="Main high-level intent (pick from allowed values)")),
        entities=(List[str], Field(default_factory=list, description="List of relevant entities")),
        raw_text=(str, Field(description="Original user input text")),
    )


class ChatAgent:

    def __init__(self):
        self.name = "ChatAgent"

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        user_input = state["messages"][-1].content

        if not isinstance(state["messages"][-1], HumanMessage):
            return state

        # Load prompts from external JSON (re-read each call so edits take effect)
        prompts = _load_prompts().get("chat_agent", {})
        sys_prompt = prompts.get("system")  # None when disabled
        intents = prompts.get("intents")    # None or list of allowed intent strings

        # Build schema-constrained model and bind to LLM
        ResponseModel = _build_parsed_request_model(intents)
        llm = _base_llm.with_structured_output(ResponseModel, include_raw=True)

        try:
            llm_messages = []
            if sys_prompt:
                llm_messages.append({"role": "system", "content": sys_prompt})
            llm_messages.append({"role": "user", "content": user_input})
            result = llm.invoke(llm_messages)
            parsed = result["parsed"]
            raw_msg = result["raw"]  # AIMessage with response_metadata

            # Normalize enum values back to plain strings for serialization
            parsed_dict = parsed.model_dump()
            if hasattr(parsed_dict.get("intent"), "value"):
                parsed_dict["intent"] = parsed_dict["intent"].value
            elif isinstance(parsed_dict.get("intent"), str):
                pass  # already a string
            
            # Extract token usage from the raw AIMessage
            usage = getattr(raw_msg, 'usage_metadata', None) or {}
            state["llm_metadata"] = {
                "chat_agent": {
                    "model": raw_msg.response_metadata.get("model_name", "gpt-4o-mini"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "response_format": ResponseModel.model_json_schema(),
                    "input": {
                        "system": sys_prompt,
                        "user": user_input,
                    },
                    "output": {
                        "raw": raw_msg.content,
                        "parsed": parsed_dict,
                    },
                }
            }
        except Exception as e:
            print(f'Error parsing request: {e}')
            parsed_dict = {
                "intent": "unknown",
                "entities": [],
                "raw_text": user_input,
            }
            state["llm_metadata"] = { "chat_agent": { "error": str(e) } }

        state["parsed_request"] = parsed_dict
        return state
