
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from .tools.dpc_retriever_tool import DPCRetrieverTool
from .tools.meteoblue_retriever_tool import MeteoblueRetrieverTool
from ..names import NodeNames, AgentNames


class Prompts:
    """Prompts for specialized data retrieval agent."""

    SPECIALIZED_TOOL_SELECTION = '\n'.join((
        "You are a specialized agent for data model retrieval.",
        "Choose the best tool to accomplish the goal.",
        "Only call tools that are provided.",
        "If needed info is missing, still propose the most likely tool call with best-effort args.",
    ))

    SPECIALIZED_REQUEST = staticmethod(lambda state: '\n'.join((
        f"Goal: {state['plan'][state['current_step']].get('goal', 'N/A')}",
        f"Parsed: {state.get('parsed_request', '')}"
    )))



class DataRetrieverAgent:
    """Specialized agent for data model retrieval and tool execution."""

    tools = dict(
        dpc_retriever_tool = DPCRetrieverTool,
        meteoblue_retriever_tool = MeteoblueRetrieverTool
    )

    def __init__(self):
        self.name = AgentNames.DATA_RETRIEVER_AGENT
        self.tools = {tool_name: tool() for tool_name, tool in self.tools.items()}
        self.llm = _base_llm.bind_tools(list(self.tools.values()))

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    

    def tool_call_invocation(self, invocation: AIMessage, state: MABaseGraphState) -> MABaseGraphState | None:
        if getattr(invocation, "tool_calls", None) is None:
            state['messages'] = invocation
            return state

        return None

    def tool_call_validation(self, tool_call: ToolCall, state: MABaseGraphState) -> MABaseGraphState | None:
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or dict()
        tool = self.tools[tool_name]

        invalid_args = dict()
        args_validation_rules = tool._set_args_validation_rules()

        for arg in tool.args_schema.model_fields.keys():
            for rule in args_validation_rules.get(arg, []):
                invalid_reason = None
                invalid_reason = rule(**tool_args)
                if invalid_reason is not None:
                    invalid_args[arg] = invalid_reason 
                    continue

        if len(invalid_args) > 0:
            state["awaiting_user"] = True
            state["messages"] = [
                AIMessage(content=f"Some parameters for '{tool_name}' are invalid.\nDetails: {invalid_args}\nPlease provide the required information.")
            ]
            return state
        
        return None

    def tool_call_response(self, tool_call: ToolCall, state: MABaseGraphState) -> MABaseGraphState | None:
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or dict()
        tool = self.tools[tool_name]

        result = tool._execute(**tool_args)

        state.setdefault("tool_results", {})
        state["tool_results"][f"step_{state['current_step']}"] = {
            "tool": tool_name,
            "args": tool_args,
            "result": result
        }

        tool_response = ToolMessage(
            content=f"""Layer generated:
- Title: {tool_name.replace('_', ' ').title()} retrieved data layer.
- URI: 's3://example-bucket/{tool_name}-out/{tool_args.get('variable', 'data')}.tif'
- Parameters: {tool_args}""",
            tool_call_id=tool_call["id"]
        )

        return tool_response


    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{AgentNames.DATA_RETRIEVER_AGENT}] → Executing tool...")

        invoke_messages = [
            SystemMessage(content=Prompts.SPECIALIZED_TOOL_SELECTION),
            HumanMessage(content=Prompts.SPECIALIZED_REQUEST(state))
        ]
        invocation = self.llm.invoke(invoke_messages)

        invocation_state = self.tool_call_invocation(invocation, state)
        if invocation_state is not None:
            return invocation_state

        # TODO: We should loop through them
        tool_call = invocation.tool_calls[0]
        print(f"[{AgentNames.DATA_RETRIEVER_AGENT}] → Tool: {tool_call['name']}")
        
        validation_state = self.tool_call_validation(tool_call, state)
        if validation_state is not None:
            print(f"[{AgentNames.DATA_RETRIEVER_AGENT}] ⚠ Validation failed")
            return validation_state
        
    
        tool_response = self.tool_call_response(tool_call, state)

        ai_message_out = self.llm.invoke([invocation, tool_response])

        state["messages"] = [invocation, tool_response, ai_message_out]
        print(f"[{AgentNames.DATA_RETRIEVER_AGENT}] ✓ Done")

        return state

