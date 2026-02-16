# Minimal example of an initial ChatAgent in Python

import json

from ..common.states import MABaseGraphState
from ..common.utils import _base_llm


_PROMPTS = dict(

    sys_standardize = (
        "You are an assistant that takes free text and returns a JSON with: \n"
        "- intent: main user intent \n"
        "- entities: list of relevant entities \n"
        "- raw_text: original user text \n"
        "Reply ONLY with valid JSON, no explanations. "
    )

)

class InitialChatAgent:
    
    def __init__(self):
        self.name = 'InitialChatAgent'
    
    @staticmethod
    def __call__(state: MABaseGraphState) -> MABaseGraphState:
        return InitialChatAgent.run(state)
    
    @staticmethod
    def run(state: MABaseGraphState) -> MABaseGraphState:
        user_input = state["messages"][-1].content
        system_prompt = _PROMPTS["sys_standardize"]

        response = _base_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ])

        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError:
            parsed = {
                "intent": "unknown",
                "entities": [],
                "raw_text": user_input
            }
            
        state["parsed_request"] = parsed
        return state
