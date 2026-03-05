from dataclasses import dataclass, asdict
from typing import Optional, TypedDict, Union, List, Dict, Any, Literal

from langchain_core.messages import BaseMessage


@dataclass
class Prompt:
    title: str
    description: str
    command: str
    message: str

    def __init__(self, prompt: dict):
        self.title = prompt.get("title", "")
        self.description = prompt.get("description", "")
        self.command = prompt.get("command", "")
        self.message = prompt.get("message", "")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to(self, message_type: type[BaseMessage]) -> BaseMessage:
        return message_type(content=self.message)



from . import (
    supervisor_agent_prompts
)