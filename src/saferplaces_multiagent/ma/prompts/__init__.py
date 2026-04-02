from dataclasses import dataclass, asdict
from typing import Optional, TypedDict, Union, List, Dict, Any, Literal

from langchain_core.messages import BaseMessage


@dataclass
class Prompt:
    title: str
    description: str
    command: str
    message: str
    header : str

    def __init__(self, prompt: dict):
        self.title = prompt.get("title", "")
        self.description = prompt.get("description", "")
        self.command = prompt.get("command", "")
        self.header = prompt.get("header", "")
        self.message = prompt.get("message", "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to(self, message_type: type[BaseMessage]) -> BaseMessage:
        return message_type(content=self.message)

from . import (
    supervisor_agent_prompts,
    request_parser_prompts,
    final_responder_prompts,
    safercast_agent_prompts,
    models_agent_prompts,
    map_agent_prompts,
)