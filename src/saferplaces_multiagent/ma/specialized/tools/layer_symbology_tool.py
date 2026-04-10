"""LayerSymbologyTool — generates MapLibre GL JS style objects via LLM."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, HumanMessage

from ....common.base_models import MapCommand
from ....common.utils import _base_llm
from ...prompts.map_agent_prompts import MapAgentPrompts


class LayerSymbologyInput(BaseModel):
    layer_id: str = Field(
        description=(
            "The exact title/id of the layer in the layer_registry to restyle. "
            "Must match a layer present in the registry."
        )
    )
    user_request: str = Field(
        description=(
            "Natural language styling request, e.g. "
            "'color DEM from blue to red', 'make flood layer semi-transparent orange'."
        )
    )


def _extract_layer_style_inputs(layer: dict) -> tuple[str, str, dict]:
    """Extract (layer_type, geometry_subtype, layer_metadata) from a layer dict."""
    layer_type: str = layer.get("type", "raster")  # "raster" | "vector"
    metadata: dict = layer.get("metadata") or {}

    if layer_type == "vector":
        geometry_type: str | list = metadata.get("geometry_type", "Polygon")
        if isinstance(geometry_type, list):
            geometry_type = geometry_type[0] if geometry_type else "Polygon"
        geo_map = {
            "Polygon": "fill",
            "MultiPolygon": "fill",
            "LineString": "line",
            "MultiLineString": "line",
            "Point": "circle",
            "MultiPoint": "circle",
        }
        geometry_subtype = geo_map.get(geometry_type, "fill")
    else:
        geometry_subtype = "raster"

    return layer_type, geometry_subtype, metadata


def _parse_style_json(raw: str) -> dict | None:
    """Try to extract a JSON object from the LLM response."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        # remove first and last fence lines
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = inner.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class LayerSymbologyTool(BaseTool):
    name: str = "set_layer_style"
    description: str = (
        "Generate a MapLibre GL JS style object for a layer based on a natural language request. "
        "Updates the layer's style in the registry and produces a 'set_layer_style' MapCommand "
        "for the frontend."
    )
    args_schema: type[BaseModel] = LayerSymbologyInput

    # Injected by MapAgent at call time
    state: dict = {}

    def _run(self, layer_id: str, user_request: str) -> str:
        # 1. Find layer in registry
        registry: list = self.state.get("layer_registry") or []
        layer = next((l for l in registry if l.get("title") == layer_id), None)
        if layer is None:
            return (
                f"Error: layer '{layer_id}' not found in layer_registry. "
                f"Available layers: {[l.get('title') for l in registry]}"
            )

        # 2. Extract style inputs
        layer_type, geometry_subtype, layer_metadata = _extract_layer_style_inputs(layer)

        # 3. Build LLM request
        human_payload = json.dumps({
            "layer_type": layer_type,
            "geometry_subtype": geometry_subtype,
            "layer_metadata": layer_metadata,
            "user_request": user_request,
        }, ensure_ascii=False)

        llm = _base_llm.bind(temperature=0)
        messages = [
            MapAgentPrompts.GenerateMaplibreStylePrompt.stable().to(SystemMessage),
            HumanMessage(content=human_payload),
        ]

        # 4. Invoke LLM
        response = llm.invoke(messages)
        raw_content: str = response.content if hasattr(response, "content") else str(response)

        # 5. Parse JSON — retry once with correction prompt if parsing fails
        style = _parse_style_json(raw_content)
        if style is None:
            correction_msg = HumanMessage(
                content=(
                    "Your previous response was not valid JSON. "
                    "Reply ONLY with a valid JSON object (no markdown, no explanation). "
                    f"Original response:\n{raw_content}"
                )
            )
            retry_response = llm.invoke(messages + [response, correction_msg])
            raw_retry = retry_response.content if hasattr(retry_response, "content") else str(retry_response)
            style = _parse_style_json(raw_retry)
            if style is None:
                return (
                    f"Error: could not generate a valid MapLibre style for layer '{layer_id}'. "
                    "The LLM did not return valid JSON."
                )

        # 6. Update layer style in registry
        for lyr in registry:
            if lyr.get("title") == layer_id:
                lyr["style"] = style
                break
        self.state["layer_registry"] = registry

        # 7. Produce MapCommand
        command = MapCommand(
            command_session=self.state.get("map_commands_session"),
            type="set_layer_style",
            payload={"layer_id": layer['src'], "style": style},
        )
        existing_commands: list = list(self.state.get("map_commands") or [])
        existing_commands.append(command.to_dict())
        self.state["map_commands"] = existing_commands

        return f"Style applied to layer '{layer_id}': {json.dumps(style, ensure_ascii=False)}"
