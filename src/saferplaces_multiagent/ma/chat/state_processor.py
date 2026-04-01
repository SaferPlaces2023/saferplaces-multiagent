"""StateProcessor — handles non-conversational (state-update) graph invocations."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState
from ..names import NodeNames


class StateProcessor(MultiAgentNode):
    """
    Runs at the entry of every graph invocation, before the request parser.

    Routing signals set by this node (read by the conditional edge in the graph):

    - HumanMessage as last message  → route to REQUEST_PARSER (normal conversational flow)
    - New unregistered shapes found → set map_request, route to MAP_AGENT for registration
    - Neither                       → route to END (no-op invocation, nothing to do)
    """

    def __init__(self, name: str = NodeNames.STATE_PROCESSOR, log_state: bool = True):
        super().__init__(name, log_state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{self.name}] → Checking state...")

        new_shapes = self._find_unregistered_shapes(state)
        if new_shapes:
            ids = [s["collection_id"] for s in new_shapes]
            print(f"[{self.name}]   ✦ New unregistered shapes: {ids}")
            state["map_request"] = (
                f"Register the following newly drawn shapes into the shapes registry: {ids}. "
                f"Call register_shape once for each collection_id in the list."
            )
        else:
            state["map_request"] = None
            print(f"[{self.name}] ✓ No new shapes")

        return state

    @staticmethod
    def _find_unregistered_shapes(state: MABaseGraphState) -> list:
        """Return shapes present in user_drawn_shapes but absent from shapes_registry."""
        user_drawn = state.get("user_drawn_shapes") or []
        shapes_registry = state.get("shapes_registry") or []
        registered_ids = {s.get("shape_id") for s in shapes_registry}
        return [s for s in user_drawn if s.get("collection_id") not in registered_ids]

    @staticmethod
    def has_human_message(state: MABaseGraphState) -> bool:
        messages = state.get("messages") or []
        return bool(messages) and isinstance(messages[-1], HumanMessage)
