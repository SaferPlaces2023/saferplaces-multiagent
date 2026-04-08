"""MapAgent — specialized agent for frontend map interactions."""
from __future__ import annotations

from typing import List

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames
from ..prompts.map_agent_prompts import MapAgentInstructions
from .tools.create_shape_tool import CreateShapeTool
from .tools.layer_symbology_tool import LayerSymbologyTool
from .tools.move_map_view_tool import MoveMapViewTool
from .tools.register_shape_tool import RegisterShapeTool



class MapAgent(MultiAgentNode):
    """Fast agent for map interactions — executes tools immediately without a human-in-the-loop
    interrupt.

    Unlike retriever/models agents that follow the Agent → InvocationConfirm → Executor
    pattern, MapAgent is a single flat node that runs its tool loop synchronously. This is
    intentional: map viewport and style operations are expected to be fast and do not require
    user confirmation. As a consequence it increments both the agent-level and the global plan
    step counters at the end of ``run()``.
    """

    def __init__(self, name: str = NodeNames.MAP_AGENT, log_state: bool = True):
        super().__init__(name, log_state)
        self._tools: List[BaseTool] = [
            LayerSymbologyTool(),
            RegisterShapeTool(),
            CreateShapeTool(),
            MoveMapViewTool(),
        ]
        self.llm = _base_llm.bind_tools(self._tools)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_tool_calls(
        self,
        initial_messages: list,
        initial_invocation,
    ):
        """Run the agentic tool loop until no more tool calls are requested.

        Returns:
            Tuple of (final_messages, final_invocation) after all tools have run.
        """
        current_messages = list(initial_messages)
        current_invocation = initial_invocation

        while getattr(current_invocation, "tool_calls", None):
            tool_calls = current_invocation.tool_calls
            print(
                f"[{self.name}] → Executing {len(tool_calls)} tool(s): "
                f"{[tc['name'] for tc in tool_calls]}"
            )

            tool_responses = []
            for tool_call in tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args", {})

                tool_obj = next((t for t in self._tools if t.name == tool_name), None)
                if tool_obj:
                    print(f"[{self.name}]   → {tool_name}({tool_args})")
                    result = tool_obj._run(**tool_args)
                else:
                    result = f"Tool '{tool_name}' not found."

                tool_responses.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )

            current_messages = current_messages + [current_invocation] + tool_responses
            current_invocation = self.llm.invoke(current_messages)

        return current_messages, current_invocation

    # ------------------------------------------------------------------
    # Node entry point
    # ------------------------------------------------------------------

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute all map operations for the current goal."""
        print(f"[{self.name}] → Processing map operations...")

        # DOC: Inject current state into each tool (tools read/write state dict directly)
        for tool in self._tools:
            tool.state = state

        invoke_messages = MapAgentInstructions.InvokeTools.Invocation.InvokeOneShot.stable(state)
        invocation = self.llm.invoke(invoke_messages)

        if not getattr(invocation, "tool_calls", None):
            print(f"[{self.name}] ✓ No tool calls")
            state["map_invocation"] = None
            state["map_request"] = None
            state["supervisor_invocation_reason"] = "step_no_tools"
            return state

        _, final_invocation = self._execute_tool_calls(invoke_messages, invocation)
        state["map_invocation"] = final_invocation
        state["map_request"] = None
        state["supervisor_invocation_reason"] = "step_done"

        print(f"[{self.name}] ✓ Done")
        return state
