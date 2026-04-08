"""StateProcessor — handles non-conversational (state-update) graph invocations."""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from ...multiagent_node import MultiAgentNode
from ...common.states import MABaseGraphState
from ..names import NodeNames
from ..specialized.map_agent import MapAgent


class StateProcessor(MultiAgentNode):
    """
    Runs at the entry of every graph invocation, before the request parser.

    Routing signals set by this node (read by the conditional edge in the graph):

    - HumanMessage as last message  → route to REQUEST_PARSER (normal conversational flow)
    - New unregistered shapes found → register them inline via MapAgent, then route to END
    - Neither                       → route to END (no-op invocation, nothing to do)
    """

    def __init__(self, name: str = NodeNames.STATE_PROCESSOR, log_state: bool = True):
        super().__init__(name, log_state)
        self._map_agent = MapAgent()

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{self.name}] → Checking state...")

        new_shapes = self._find_shapes_to_register(state)
        if new_shapes:
            ids = [s["collection_id"] for s in new_shapes]
            print(f"[{self.name}]   ✦ Shapes to register/update: {ids}")
            state["map_request"] = (
                f"Register or update the following shapes into the shapes registry: {ids}. "
                f"Call register_shape once for each collection_id in the list."
            )
            # DOC: Execute inline — MapAgent is a support agent, no graph routing needed
            state = self._map_agent.run(state)
        else:
            state["map_request"] = None
            print(f"[{self.name}] ✓ No new shapes")

        return state

    @staticmethod
    def _find_shapes_to_register(state: MABaseGraphState) -> list:
        """Return shapes that need registration: new (absent from registry) or modified (geometry changed)."""
        user_drawn = state.get("user_drawn_shapes") or []
        shapes_registry = state.get("shapes_registry") or []
        registry_map = {s.get("shape_id"): s for s in shapes_registry}

        result = []
        for shape in user_drawn:
            cid = shape.get("collection_id")
            if cid not in registry_map:
                result.append(shape)
            else:
                current_geometry = (shape.get("features") or [{}])[0].get("geometry", {})
                registered_geometry = registry_map[cid].get("geometry", {})
                if current_geometry != registered_geometry:
                    result.append(shape)
        return result
