"""MapAgent — specialized agent for frontend map interactions."""
from __future__ import annotations

from typing import List

from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames
from ..prompts.map_agent_prompts import MapAgentPrompts
from .tools.layer_symbology_tool import LayerSymbologyTool
from .tools.register_shape_tool import RegisterShapeTool


# Registry-friendly description for the Map agent.
MAP_AGENT_DESCRIPTION = {
    "name": NodeNames.MAP_AGENT,
    "description": (
        "Agent that manages map frontend interactions: moves the viewport, changes "
        "the visual style (symbology) of geospatial layers, and creates/registers "
        "vector shapes (points, polygons, lines) derived from natural language requests.\n"
        "Use this agent when the user wants to navigate the map, zoom to a location, "
        "change a layer appearance, or generate/register a geometric shape.\n"
        "Do NOT use for flood simulations or external data retrieval."
    ),
    "examples": [
        "Sposta la mappa su Roma",
        "Zoom sulla zona alluvionata",
        "Centra la mappa su Firenze",
        "Colora il layer DEM con una palette da blu ad arancione",
        "Rendi il layer flood semi-trasparente",
        "Crea una bbox attorno ad una regione o città",
        "Metti un punto nel centro della shape che ho disegnato",
    ],
    "outputs": (
        "Updated map_view state; MapCommand(s) for the frontend "
        "(move_view | set_layer_style | sync_shapes); "
        "new entries in shapes_registry (create_shape | register_shape)."
    ),
    "prerequisites": "For set_layer_style: the target layer must exist in the layer_registry.",
    "implicit_step_rules": (
        "Use map_agent for visual/navigation requests only. "
        "Do NOT combine with simulations in the same step."
    ),
}


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
            RegisterShapeTool()
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
        """Execute all map operations for the current goal and advance the plan step."""
        print(f"[{self.name}] → Processing map operations...")

        # Inject current state into each tool (tools read/write the state dict directly)
        for tool in self._tools:
            tool.state = state

        system_message = MapAgentPrompts.ContextPrompt.stable().to(SystemMessage)
        human_message = MapAgentPrompts.ExecutionContext.stable(state).to(HumanMessage)
        human_message.content += f"\nGoal: {state.get('map_request')}"

        invoke_messages = [system_message, human_message]
        invocation = self.llm.invoke(invoke_messages)

        if not getattr(invocation, "tool_calls", None):
            print(f"[{self.name}] ✓ No tool calls")
            state["map_invocation"] = invocation
            return state

        _, final_invocation = self._execute_tool_calls(invoke_messages, invocation)
        state["map_invocation"] = final_invocation

        # Prevent re-routing to this agent on subsequent supervisor cycles
        state["map_request"] = None

        # MapAgent has no Executor node, so it increments both step counters here:
        # the agent-level counter (map_current_step) and the global plan step counter.
        if state.get("parsed_request") is not None:
            StateManager.mark_agent_step_complete(state, "map")
            if state.get("current_step") is not None:
                state["current_step"] += 1

        print(f"[{self.name}] ✓ Done")
        return state
