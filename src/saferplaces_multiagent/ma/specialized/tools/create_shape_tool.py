"""CreateShapeTool — generates a GeoJSON geometry from a natural language request via LLM."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, HumanMessage

from ....common.base_models import DrawnShape, compute_geometry_metadata, MapCommand
from ....common.utils import _base_llm, random_id8
from ...prompts.map_agent_prompts import MapAgentPrompts


_GEOMETRY_TYPE_MAP: dict = {
    "Point": "point",
    "MultiPoint": "point",
    "LineString": "linestring",
    "MultiLineString": "linestring",
    "Polygon": "polygon",
    "MultiPolygon": "polygon",
}


def _parse_geometry_json(raw: str) -> dict | None:
    """Extract a GeoJSON geometry object from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = inner.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # Accept either a bare geometry or a Feature/FeatureCollection wrapper
    if parsed.get("type") == "Feature":
        return parsed.get("geometry")
    if parsed.get("type") == "FeatureCollection":
        features = parsed.get("features") or []
        return features[0].get("geometry") if features else None
    # Bare geometry
    if "coordinates" in parsed:
        return parsed
    return None


class CreateShapeInput(BaseModel):
    user_request: str = Field(
        description=(
            "Natural language description of the shape to create, e.g. "
            "'a polygon around downtown Rome', 'a point at the Colosseum', "
            "'a bounding box covering the Po river delta'."
        )
    )


class CreateShapeTool(BaseTool):
    name: str = "create_shape"
    description: str = (
        "Generate a new GeoJSON geometry (point, line, or polygon) from a natural language "
        "request using the LLM. The generated shape is AUTOMATICALLY added to shapes_registry "
        "and sent to the frontend via a 'sync_shapes' MapCommand. "
        "Do NOT call register_shape after create_shape — registration is already handled internally."
    )
    args_schema: type[BaseModel] = CreateShapeInput

    # Injected by MapAgent at call time
    state: dict = {}

    def _run(self, user_request: str) -> str:
        # Build viewport context string
        map_viewport = self.state.get("map_viewport")
        map_zoom = self.state.get("map_zoom")
        if map_viewport:
            viewport_str = (
                f"Current map view: bounds=[west={map_viewport[0]}, south={map_viewport[1]}, "
                f"east={map_viewport[2]}, north={map_viewport[3]}]\n"
                f"Center point: lat={map_viewport[1] + (map_viewport[3] - map_viewport[1]) / 2}, "
                f"lng={map_viewport[0] + (map_viewport[2] - map_viewport[0]) / 2}.\n"
                f"Consider 1 degree of latitude/longitude as approximately 111 km. A 1km distance is approximately 0.009 degrees."
            )
        else:
            viewport_str = "Current map view: unknown"

        # Invoke LLM to generate a GeoJSON geometry
        llm = _base_llm.bind(temperature=0)
        messages = [
            MapAgentPrompts.GenerateShapePrompt.stable().to(SystemMessage),
            HumanMessage(content=f"{viewport_str}\n\nUser request: {user_request}"),
        ]
        response = llm.invoke(messages)
        raw_content: str = response.content if hasattr(response, "content") else str(response)

        geometry = _parse_geometry_json(raw_content)
        if geometry is None:
            # Retry once with a correction nudge
            correction = HumanMessage(
                content=(
                    "Your previous response was not a valid GeoJSON geometry. "
                    "Reply ONLY with a valid GeoJSON geometry object (no markdown, no explanation). "
                    f"Original response:\n{raw_content}"
                )
            )
            messages.append(HumanMessage(content=raw_content))
            messages.append(correction)
            retry = llm.invoke(messages)
            raw_content = retry.content if hasattr(retry, "content") else str(retry)
            geometry = _parse_geometry_json(raw_content)

        if geometry is None:
            return f"Error: LLM did not return a valid GeoJSON geometry for request: {user_request!r}"

        # Determine shape_type from geometry type
        geom_type: str = geometry.get("type", "Polygon")
        shape_type = _GEOMETRY_TYPE_MAP.get(geom_type, "polygon")

        shape_id = f"created-{random_id8()}"
        drawn = DrawnShape(
            shape_id=shape_id,
            shape_type=shape_type,
            geometry=geometry,
            label=user_request[:64],
            metadata=compute_geometry_metadata(geometry),
        )

        registry = list(self.state.get("shapes_registry") or [])
        registry.append(drawn.to_dict())
        self.state["shapes_registry"] = registry

        cmd = MapCommand(
            type="sync_shapes",
            payload={
                "shape_id": drawn.shape_id,
                "shape_type": drawn.shape_type,
                "geometry": drawn.geometry,
                "label": drawn.label,
                "metadata": drawn.metadata,
            },
        )
        commands = list(self.state.get("map_commands") or [])
        commands.append(cmd.to_dict())
        self.state["map_commands"] = commands

        print(f"[CreateShapeTool] ✓ Shape '{shape_id}' created ({geom_type})")
        return f"Shape '{shape_id}' created ({geom_type}) and added to shapes_registry."
