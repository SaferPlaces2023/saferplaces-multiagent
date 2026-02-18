
import json

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames
from langchain_core.messages import AIMessage, SystemMessage



class Prompts:

    FINAL_RESPONSE_PROMPT = (
        "You are an expert assistant responsible for generating the final response to the user.\n"
        "\n"
        "Instructions:\n"
        "- Write a clear, concise, and helpful answer based on the provided context and tool results.\n"
        "- If there are errors or issues, explain them clearly and suggest possible next steps.\n"
        "- If the context involves geospatial data or map layers, ensure your answer is relevant and informative.\n"
        "- Respond in the same language as the user's original request, unless otherwise specified.\n"
        "- Do not invent information; base your answer strictly on the available data."
    )

    STRUCTURED_FINAL_CONTEXT = staticmethod(lambda state: (
        f"Context JSON:\n"
        f"""{json.dumps({
            'parsed_request': state.get('parsed_request'),
            'plan': state.get('plan'),
            'tool_results': state.get('tool_results'),
            'error': state.get('error')
        }, ensure_ascii=False, indent=2)}"""
    ))

    FORMAT_FINAL_CONTEXT = staticmethod(lambda state: (
        "Context for your answer:\n"
        f"- User intent: {state.get('parsed_request', {}).get('intent', 'N/A')}\n"
        f"- Entities: {', '.join(state.get('parsed_request', {}).get('entities', [])) or 'N/A'}\n"
        f"- Plan: {state.get('plan', 'N/A')}\n"
        f"- Tool results: {state.get('tool_results', 'N/A')}\n"
        f"- Error: {state.get('error', 'None')}\n"
        f"- Original user input: {state.get('parsed_request', {}).get('raw_text', 'N/A')}\n"
    ))



class FinalResponder:
    """Agent that generates the final user-facing response."""

    def __init__(self):
        self.name = NodeNames.FINAL_RESPONDER
        self.llm = _base_llm

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{NodeNames.FINAL_RESPONDER}] → Generating response...")

        invoke_messages = [
            *state["messages"],
            SystemMessage(content=Prompts.FINAL_RESPONSE_PROMPT),
            
            # AIMessage(content=Prompts.STRUCTURED_FINAL_CONTEXT(state)),
            AIMessage(content=Prompts.FORMAT_FINAL_CONTEXT(state))
        ]

        response = self.llm.invoke(invoke_messages)

        state["messages"] = [AIMessage(content=response.content)]

        print(f"[{NodeNames.FINAL_RESPONDER}] ✓ Response ready")
        return state
