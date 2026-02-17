

import json
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

FINAL_PROMPT = """You are the final response writer.
Write a concise, user-facing answer in Italian.
Use the tool results to answer. If there is an error, explain it and propose next steps.
Remember that this agent has the purpose to work with geospatial layers.
"""

class FinalChatAgent:
    def __init__(self):
        self.name = "FinalChatAgent"
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

        ass_msg = AIMessage(content=f"Context JSON:\n{json.dumps(payload, ensure_ascii=False)}")
        # print('------')
        # print(ass_msg.content)
        # print('------')

        resp = self.llm.invoke([
            SystemMessage(content=FINAL_PROMPT),
            ass_msg
        ])

        state["messages"] = [AIMessage(content=resp.content)]
        return state
