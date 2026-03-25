from __future__ import annotations
from typing import Optional, TypedDict, Union, List, Dict, Any, Literal, Annotated

from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

import json
import datetime

from . import utils


_URI_HINT = "HTTP(S) URL, S3 URI (s3://...)"

ConfirmationState = Literal["accepted", "rejected", "pending"]

RequestType = Literal["action", "info", "analysis", "clarification"]

# ---------------------------------------------------------------------------
# Resolved metadata — discriminated union per entity_type
# ---------------------------------------------------------------------------

class ResolvedMetadata(BaseModel):
    model_config = {"extra": "forbid"}
    
    # location
    country: Optional[str] = None
    approx_bbox: Optional[List[float]] = None
    admin_level: Optional[str] = None
    # date
    iso_date: Optional[str] = None
    date_range: Optional[List[str]] = None
    # layer
    layer_id: Optional[str] = None
    source: Optional[str] = None
    # model
    model_id: Optional[str] = None
    version: Optional[str] = None
    # product
    product_id: Optional[str] = None
    format: Optional[str] = None
    # parameter
    value: Optional[str] = None
    unit: Optional[str] = None

# ---------------------------------------------------------------------------
# Entity & ParsedRequest
# ---------------------------------------------------------------------------

class Entity(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(description="Entity name as mentioned by the user")
    entity_type: str = Field(
        description="Entity type: location, layer, model, product, date, parameter"
    )
    resolved: Optional[ResolvedMetadata] = Field(
        default=None,
        description="Resolved metadata, discriminated by entity_type. "
                    "Omit if entity_type is not one of the known types."
    )

class ParsedRequest(BaseModel):
    model_config = {"extra": "forbid"}
    intent: str = Field(description="Main high-level intent of the request")
    request_type: RequestType = Field(
        description="Classification: action, info, analysis, or clarification"
    )
    entities: List[Entity] = Field(
        default_factory=list,
        description="Typed entities with optional resolution"
    )
    parameters_json: str = Field(
        default="{}",
        description=(
            "JSON string of explicit parameters extracted from the request. "
            "Examples: {\"bbox\": [...], \"rainfall_mm\": 50, \"duration_days\": 3}"
        )
    )
    implicit_requirements: List[str] = Field(
        default_factory=list,
        description="Requirements inferred from context (e.g. 'needs DEM', 'needs bbox')"
    )
    raw_text: str = Field(description="Original user input text verbatim")

    @property
    def parameters(self) -> dict:
        return json.loads(self.parameters_json) if self.parameters_json else {}


# Semantic enum for plan lifecycle — replaces the old plan_confirmation + plan_aborted pattern
PlanConfirmationStatus = Literal["pending", "accepted", "modify", "rejected", "aborted"]

@dataclass
class Layer:
    title: str
    type: Literal["raster", "vector"]
    src: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.type not in ("raster", "vector"):
            raise ValueError("Layer.type must be 'raster' or 'vector'")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
class RelevantLayers(TypedDict):
    layers: List[Layer]
    is_dirty: bool

class AdditionalContext(TypedDict):
    relevant_layers: RelevantLayers



class BBox(BaseModel):
    """
    Bounding box in EPSG:4326 (WGS84).
    - `west` = min longitude
    - `south` = min latitude
    - `east` = max longitude
    - `north` = max latitude
    """
    west: float = Field(..., description="Minimum longitude (degrees), e.g., 10.0")
    south: float = Field(..., description="Minimum latitude (degrees), e.g., 44.0")
    east: float = Field(..., description="Maximum longitude (degrees), e.g., 12.0")
    north: float = Field(..., description="Maximum latitude (degrees), e.g., 46.0")

    def __str__(self):
        return f"{{\"west\": {self.west}, \"south\": {self.south}, \"east\": {self.east}, \"north\": {self.north}}}"
    
    def to_list(self) -> List[float]:
        """
        Convert the bounding box to a list [west, south, east, north].
        """
        return [self.west, self.south, self.east, self.north]
    
    @property
    def lat_range(self) -> List[float]:
        """
        Get the latitude range as [south, north].
        """
        return [self.south, self.north]
    
    @property
    def long_range(self) -> List[float]:
        """
        Get the longitude range as [west, east].
        """
        return [self.west, self.east]
    
    def draw_feature_collection(
            self,
            collection_id: str | None = None,
            description: str | None = None
        ) -> Dict[str, Any]:
        """
        Convert the bounding box to a GeoJSON-like dictionary for drawing.
        """
        collection_id = collection_id or utils.random_id8()
        name = f"draw-bbox-src-{collection_id}"
        description = description or None
        return {
            "collection_id": collection_id,
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [self.west, self.south],
                            [self.east, self.south],
                            [self.east, self.north],
                            [self.west, self.north],
                            [self.west, self.south]
                        ]]
                    },
                    "properties": {
                        "ts": datetime.datetime.now(tz=datetime.timezone.utc).timestamp() * 1000  # Convert to milliseconds
                    }
                }
            ],
            "metadata": {
                "bounds": {
                    "minx": self.west,
                    "miny": self.south,
                    "maxx": self.east,
                    "maxy": self.north
                },
                "feature_type": "bbox",
                "name": name,
                "description": description
            },
        }