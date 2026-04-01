"""RegisterShapeTool — registers a user-drawn shape into shapes_registry."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

from ....common.base_models import DrawnShape, compute_geometry_metadata, MapCommand


_FEATURE_TYPE_MAP: dict = {
    "bbox": "bbox",
    "polygon": "polygon",
    "linestring": "linestring",
    "point": "point",
}


class RegisterShapeInput(BaseModel):
    collection_id: str = Field(
        description="The collection_id of the shape in user_drawn_shapes to register."
    )
    label: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable label for the shape. "
            "Defaults to the shape's name from metadata if omitted."
        ),
    )


class RegisterShapeTool(BaseTool):
    name: str = "register_shape"
    description: str = (
        "Register a user-drawn shape from user_drawn_shapes into the shapes_registry. "
        "Use this for every new shape that must be persisted and made available to downstream agents."
    )
    args_schema: type[BaseModel] = RegisterShapeInput

    # Injected by MapAgent at call time
    state: dict = {}

    def _run(self, collection_id: str, label: Optional[str] = None) -> str:
        user_drawn = self.state.get("user_drawn_shapes") or []
        shape_data = next(
            (s for s in user_drawn if s.get("collection_id") == collection_id), None
        )
        if shape_data is None:
            return (
                f"Error: no shape with collection_id '{collection_id}' "
                "found in user_drawn_shapes."
            )

        features = shape_data.get("features") or []
        if not features:
            return f"Error: shape '{collection_id}' contains no features."

        geometry = features[0].get("geometry", {})
        metadata = shape_data.get("metadata", {})
        feature_type = metadata.get("feature_type", "polygon")
        shape_type = _FEATURE_TYPE_MAP.get(feature_type, "polygon")

        registry = list(self.state.get("shapes_registry") or [])

        drawn = DrawnShape(
            shape_id=collection_id,
            shape_type=shape_type,
            geometry=geometry,
            label=label or metadata.get("name"),
            metadata=compute_geometry_metadata(geometry),
        )

        existing_idx = next(
            (i for i, s in enumerate(registry) if s.get("shape_id") == collection_id), None
        )
        if existing_idx is not None:
            registry[existing_idx] = drawn.to_dict()
            action = "updated"
        else:
            registry.append(drawn.to_dict())
            action = "registered"

        self.state["shapes_registry"] = registry

        # Remove from user_drawn_shapes — shape is now persisted in shapes_registry
        user_drawn = list(self.state.get("user_drawn_shapes") or [])
        self.state["user_drawn_shapes"] = [
            s for s in user_drawn if s.get("collection_id") != collection_id
        ]

        # Emit sync_shapes MapCommand so the frontend can display the registered shape (BUG-3 fix — PLN-015)
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

        print(f"[RegisterShapeTool] ✓ Shape '{collection_id}' {action} as {shape_type}")
        return f"Shape '{collection_id}' {action} as {shape_type}."
