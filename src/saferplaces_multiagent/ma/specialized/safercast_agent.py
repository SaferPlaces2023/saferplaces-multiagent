
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from .tools.dpc_retriever_tool import DPCRetrieverTool
from ..names import NodeNames, AgentNames


class Prompts:
    """Prompts for specialized data retrieval agent."""

    SPECIALIZED_TOOL_SELECTION = '\n'.join((
        "You are a specialized agent for data model retrieval.",
        "Choose the best tool to accomplish the goal.",
        "Only call tools that are provided.",
        "If needed info is missing, still propose the most likely tool call with best-effort args.",
    ))

    SPECIALIZED_REQUEST = staticmethod(lambda goal, parsed_request: '\n'.join((
        f"Goal: {goal}",
        f"Parsed: {parsed_request}"
    )))



class DataRetrieverAgent:
    """Specialized agent for data model retrieval and tool execution."""

    tools = dict(
        dpc_retriever_tool = DPCRetrieverTool
    )

    def __init__(self):
        self.name = AgentNames.DATA_RETRIEVER_AGENT
        self.tools = {tool_name: tool() for tool_name, tool in self.tools.items()}
        self.llm = _base_llm.bind_tools(list(self.tools.values()))

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        step = state["plan"][state["current_step"]]
        goal = step.get("goal") or ""
        parsed_request = state.get("parsed_request") or ""

        invoke_messages = [
            SystemMessage(content=Prompts.SPECIALIZED_TOOL_SELECTION),
            HumanMessage(content=Prompts.SPECIALIZED_REQUEST(goal, parsed_request))
        ]
        invocation = self.llm.invoke(invoke_messages)

        if not getattr(invocation, "tool_calls", None):
            state.setdefault("tool_results", {})
            state["tool_results"][f"step_{state['current_step']}"] = {
                "status": "no_tool_call",
                "text": getattr(invocation, "content", "")
            }
            return state

        tool_call = invocation.tool_calls[0]
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args", {}) or {}

        if tool_name not in self.tools:
            state.setdefault("tool_results", {})
            state["tool_results"][f"step_{state['current_step']}"] = {
                "status": "unknown_tool",
                "tool": tool_name,
                "args": tool_args
            }
            state["awaiting_user"] = True
            state["messages"] = [
                AIMessage(content=f"Tool '{tool_name}' not recognized. Please clarify your request.")
            ]
            return state

        err = False
        if err:
            state["awaiting_user"] = True
            state["messages"] = [
                AIMessage(content=f"Missing or invalid parameters for '{tool_name}'. Details: {err}\nPlease provide the required information.")
            ]
            return state

        result = self.tools[tool_name]._execute(**tool_args)

        state.setdefault("tool_results", {})
        state["tool_results"][f"step_{state['current_step']}"] = {
            "tool": tool_name,
            "args": tool_args,
            "result": result
        }

        tool_response = ToolMessage(
            content=f"""Layer generated:
- Title: DPC retrieved data layer.
- URI: 's3://example-bucket/dpc-out/dpc-temperature.tif'
- Parameters: {tool_args}""",
            tool_call_id=tool_call["id"]
        )

        ai_message_out = self.llm.invoke([invocation, tool_response])

        state["messages"] = [invocation, tool_response, ai_message_out]

        return state

