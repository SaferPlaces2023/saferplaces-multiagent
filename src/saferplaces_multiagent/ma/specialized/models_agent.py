from typing import Any, Optional, List, Dict
import os

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langchain_core.tools import BaseTool
from langgraph.types import Command, interrupt

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames
from ...nodes.base import base_models
from .tools.safer_rain_tool import SaferRainTool


# Registry-friendly description for the Models agent.
# Use this to populate the supervisor agent registry.
MODELS_AGENT_DESCRIPTION = {
    "name": NodeNames.MODELS_AGENT,
    "description": (
        "Specialized agent that executes environmental models via APIs: flood (rain or storm-surge), "
        "fire propagation, structural impact analyses and similar scenarios. "
        "It exposes tools that run models and returns generated layers or reports for downstream processing."
    ),
    "examples": [
        "Run flood propagation for a heavy-rain scenario on a bbox",
        "Simulate fire spread given ignition points and wind conditions",
        "Estimate compromised structures after a flood event"
    ]
}


class Prompts:
    """Prompts for the Models agent."""

    SPECIALIZED_TOOL_SELECTION = '\n'.join((
        "You are a specialized simulations agent.",
        "Choose the best model/tool to accomplish the goal.",
        "Only call provided tools and propose reasonable args if missing."
    ))

    SPECIALIZED_REQUEST = staticmethod(lambda state: '\n'.join((
        f"Goal: {state['plan'][state['current_step']].get('goal', 'N/A')}",
        f"Parsed: {state.get('parsed_request', '')}"
    )))

    SPECIALIZED_RE_REQUEST = staticmethod(lambda state: '\n'.join((
        f"Goal: {state['plan'][state['current_step']].get('goal', 'N/A')}",
        "Some tools needs to be reviewed or corrected.",
        "Here is the current invocation:",
        '\n'.join([tc['name'] + ': ' + str(tc['args']) for tc in state['models_invocation'].tool_calls]),
        f"User response: {state['models_reinvocation_request'].content}",
        "Produce a new sequence of tool calls based on the user's feedback. You can modify arguments, order, adding or deleting tool calls."
    )))


class Tools:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Tools, cls).__new__(cls)
            active_tools = [
                tool() for tool in [
                    SaferRainTool,
                ]
            ]
            cls._instance._tools = {tool.name: tool for tool in active_tools}
        return cls._instance

    @property
    def tools(self):
        return self._tools
    
    def get(self, tool_name):
        return self._tools[tool_name]


class ModelsAgent:
    """Agent that executes environmental models using tools backed by APIs."""

    def __init__(self):
        self.name = NodeNames.MODELS_AGENT
        self.tools = Tools().tools
        self.llm = _base_llm.bind_tools(list(self.tools.values()))

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def tool_calls_invocation(self, invocation: AIMessage, state: MABaseGraphState) -> MABaseGraphState | None:
        if len(getattr(invocation, "tool_calls") or []) == 0:
            print("NO TOOL CALLS")
            state["current_step"] += 1
            state['models_invocation'] = invocation
            state["models_invocation_confirmation"] = None
            state['messages'] = invocation
            return state

        return None

    def run(self, state: MABaseGraphState) -> MABaseGraphState:

        print(f"[{NodeNames.MODELS_AGENT}] → Invoking tools...")

        if state.get("models_invocation_confirmation") != 'rejected':
            invoke_messages = [
                SystemMessage(content=Prompts.SPECIALIZED_TOOL_SELECTION),
                HumanMessage(content=Prompts.SPECIALIZED_REQUEST(state))
            ]
        else:
            invoke_messages = [
                SystemMessage(content=Prompts.SPECIALIZED_TOOL_SELECTION),
                HumanMessage(content=Prompts.SPECIALIZED_RE_REQUEST(state))
            ]

        print('>>>', [m.content for m in invoke_messages])
        invocation = self.llm.invoke(invoke_messages)

        invocation_state = self.tool_calls_invocation(invocation, state)
        if invocation_state is not None:
            return invocation_state
        
        print(f"[{NodeNames.MODELS_AGENT}] → Tool calls: [{len(invocation.tool_calls)}]: {[call['name'] for call in invocation.tool_calls]}")
        
        state['models_invocation'] = invocation
        state["models_current_step"] = 0
        state["models_invocation_confirmation"] = 'pending'
        state['models_reinvocation_request'] = None

        return state


class ModelsInvocationConfirm:

    def __init__(self):
        self.name = NodeNames.MODELS_INVOCATION_CONFIRM
        self.enabled = False

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    
    def tool_call_validation(self, tool_call: ToolCall, state: MABaseGraphState) -> MABaseGraphState | None:
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or dict()
        tool = Tools().get(tool_name)

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
            invalid_message = AIMessage(content=f"Some parameters for '{tool_name}' are invalid.\nDetails: {invalid_args}\nPlease provide the required information.")
            return invalid_message
        
        return None
    
    def validate(self, state: MABaseGraphState) -> MABaseGraphState | None:
        invocation = state["models_invocation"]
        invocation_step = state["models_current_step"]

        invalid_invocation_messages = []
        for tool_call in invocation.tool_calls[invocation_step:]:
            invalid_reason = self.tool_call_validation(tool_call, state)
            if invalid_reason is not None:
                invalid_invocation_messages.append(invalid_reason)

        if len(invalid_invocation_messages) == 0:
            return None
        
        print(f"[{self.name}] ⚠ Validation failed")
        print(f"Invalid tool calls: {[m.content for m in invalid_invocation_messages]}?",)
        interruption = interrupt({
            "content": f"Some tool calls needs to be reviewed or corrected: {[m.content for m in invalid_invocation_messages]}?",
            "interrupt_type": "invocation-validation"
        })
        print('solve interruption', interruption)
        response = interruption.get('response', 'User did not provide any response.')
        state["models_current_step"] = 0
        state["models_invocation_confirmation"] = 'rejected'
        state['models_reinvocation_request'] = HumanMessage(content=response)
        return state
    
    def confirm(self, state: MABaseGraphState) -> MABaseGraphState:
        invocation = state.get('models_invocation')
        invocation_confirmed = state.get('models_invocation_confirmation')
        if invocation is not None and len(invocation.tool_calls) > 0 and invocation_confirmed == 'pending':
            print(f"Do you want to proceed with the tool calls: {invocation.tool_calls}?",)
            interruption = interrupt({
                "content": f"Do you want to proceed with the tool calls: {invocation.tool_calls}?",
                "interrupt_type": "invocation-confirmation"
            })
            print('solve interruption', interruption)
            response = interruption.get('response', 'User did not provide any response.')
            if response == 'ok':
                state["models_invocation_confirmation"] = 'accepted'
                state["models_reinvocation_request"] = None
            else:
                state["models_current_step"] = 0
                state["models_invocation_confirmation"] = 'rejected'                
                state['models_reinvocation_request'] = HumanMessage(content=response)
        
        return state
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        validation_state = self.validate(state)
        if validation_state is not None:
            return validation_state
        if self.enabled:
            return self.confirm(state)
        state["models_invocation_confirmation"] = 'accepted'
        state['models_reinvocation_request'] = None
        return state
    

class ModelsExecutor:

    def __init__(self):
        self.name = NodeNames.MODELS_EXECUTOR

    def tool_call_response(self, tool_call: ToolCall, state: MABaseGraphState) -> MABaseGraphState | None:
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or dict()
        tool = Tools().get(tool_name)

        result = tool._execute(**tool_args)

        state.setdefault("tool_results", {})
        state["tool_results"][f"step_{state['current_step']}"] = state["tool_results"].get(f"step_{state['current_step']}") or []
        state["tool_results"][f"step_{state['current_step']}"].append({
            "tool": tool_name,
            "args": tool_args,
            "result": result
        })
        state["models_current_step"] += 1 # Assume no errors (then fix this only if no errors)

        tool_response = ToolMessage(
            content=f"""Layer generated:
- Title: {tool_name.replace('_', ' ').title()} models simulation layer.
- URI: 's3://example-bucket/{tool_name}-out/{tool_args.get('variable', 'data')}.tif'
- Parameters: {tool_args}""",
            tool_call_id=tool_call["id"]
        )

        return tool_response


    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        invocation = state["models_invocation"]
        invocation_step = state["models_current_step"]
        
        tool_response_seq = []
        
        for tool_call in invocation.tool_calls[invocation_step:]:
            print(f"[{self.name}] → Tool: {tool_call['name']}")
            
            tool_response = self.tool_call_response(tool_call, state)
            
            tool_response_seq.append(tool_response)
            
            print(f"[{self.name}] → Tool response: {tool_response}")

        state["current_step"] += 1
        state["messages"] = [invocation, *tool_response_seq]
        
        print(f"[{self.name}] ✓ Done")

        return state