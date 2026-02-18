from typing import Any, Optional, List, Dict
import os

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langchain_core.tools import BaseTool

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

    TOOL_SELECTION = '\n'.join((
        "You are a specialized simulations agent.",
        "Choose the best model/tool to accomplish the goal.",
        "Only call provided tools and propose reasonable args if missing."
    ))

    REQUEST = staticmethod(lambda state: '\n'.join((
        f"Goal: {state['plan'][state['current_step']].get('goal', 'N/A')}",
        f"Parsed: {state.get('parsed_request', '')}"
    )))


class ModelsAgent:
    """Agent that executes environmental models using tools backed by APIs."""

    tools = dict(
        safer_rain_tool = SaferRainTool
    )

    def __init__(self):
        self.name = NodeNames.MODELS_AGENT
        self.tools = {k: v() for k, v in self.tools.items()}
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
                invalid_reason = rule(**tool_args)
                if invalid_reason is not None:
                    invalid_args[arg] = invalid_reason

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
        state["tool_results"][f"step_{state.get('current_step', 0)}"] = {
            "tool": tool_name,
            "args": tool_args,
            "result": result
        }
        state["current_step"] = state.get("current_step", 0) + 1

        tool_response = ToolMessage(
            content=f"Model run completed: {tool_name} → {result.get('tool_output', {}).get('uri')}",
            tool_call_id=tool_call.get("id")
        )

        return tool_response

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[ModelsAgent] → Executing model tool...")

        invoke_messages = [
            SystemMessage(content=Prompts.TOOL_SELECTION),
            HumanMessage(content=Prompts.REQUEST(state))
        ]
        invocation = self.llm.invoke(invoke_messages)

        invocation_state = self.tool_call_invocation(invocation, state)
        if invocation_state is not None:
            return invocation_state

        tool_call = invocation.tool_calls[0]
        print(f"[ModelsAgent] → Tool: {tool_call['name']}")

        validation_state = self.tool_call_validation(tool_call, state)
        if validation_state is not None:
            print(f"[ModelsAgent] ⚠ Validation failed")
            return validation_state

        tool_response = self.tool_call_response(tool_call, state)

        ai_message_out = self.llm.invoke([invocation, tool_response])

        state["messages"] = [invocation, tool_response, ai_message_out]
        print(f"[ModelsAgent] ✓ Done")

        return state
