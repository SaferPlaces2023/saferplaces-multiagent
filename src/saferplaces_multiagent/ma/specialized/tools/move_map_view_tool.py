"""MoveMapViewTool — moves the map viewport based on a natural language request via LLM."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, HumanMessage

from ....common.base_models import MapCommand
from ....common.utils import _base_llm
from ...prompts.map_agent_prompts import MapAgentPrompts


def _parse_viewport_json(raw: str) -> dict | None:
    """Extract a viewport JSON object from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = inner.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # Must contain either center or bbox
    if "center" in parsed or "bbox" in parsed:
        return parsed
    return None


class MoveMapViewInput(BaseModel):
    user_request: str = Field(
        description=(
            "Natural language description of where to move the map, e.g. "
            "'go to Rome', 'zoom to the Po river delta', 'fit the whole of Italy', "
            "'center on coordinates 44.5, 11.3 at zoom 12'."
        )
    )


class MoveMapViewTool(BaseTool):
    name: str = "move_map_view"
    description: str = (
        "Move or zoom the map viewport based on a natural language request. "
        "Produces a 'move_view' MapCommand with a center+zoom or bbox payload."
    )
    args_schema: type[BaseModel] = MoveMapViewInput

    # Injected by MapAgent at call time
    state: dict = {}

    def _run(self, user_request: str) -> str:
        # Build current viewport context string
        map_viewport = self.state.get("map_viewport")
        map_zoom = self.state.get("map_zoom")
        if map_viewport:
            viewport_str = (
                f"Current map view: bounds=[west={map_viewport[0]}, south={map_viewport[1]}, "
                f"east={map_viewport[2]}, north={map_viewport[3]}], zoom={map_zoom}"
            )
        else:
            viewport_str = "Current map view: unknown"

        llm = _base_llm.bind(temperature=0)
        messages = [
            MapAgentPrompts.GenerateMoveViewPrompt.stable().to(SystemMessage),
            HumanMessage(content=f"{viewport_str}\n\nUser request: {user_request}"),
        ]
        response = llm.invoke(messages)
        raw_content: str = response.content if hasattr(response, "content") else str(response)

        viewport = _parse_viewport_json(raw_content)
        if viewport is None:
            correction = HumanMessage(
                content=(
                    "Your previous response was not valid JSON. "
                    "Reply ONLY with a valid JSON object containing 'center' or 'bbox'. "
                    f"Original response:\n{raw_content}"
                )
            )
            messages.append(HumanMessage(content=raw_content))
            messages.append(correction)
            retry = llm.invoke(messages)
            raw_content = retry.content if hasattr(retry, "content") else str(retry)
            viewport = _parse_viewport_json(raw_content)

        if viewport is None:
            return f"Error: LLM did not return a valid viewport for request: {user_request!r}"

        cmd = MapCommand(command_session=self.state.get("map_commands_session"), type="move_view", payload=viewport)
        commands = list(self.state.get("map_commands") or [])
        commands.append(cmd.to_dict())
        self.state["map_commands"] = commands

        print(f"[MoveMapViewTool] ✓ move_view → {viewport}")
        return f"Map view updated: {viewport}"
