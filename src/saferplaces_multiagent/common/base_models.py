from __future__ import annotations
from typing import Optional, TypedDict, Union, List, Dict, Any, Literal, Annotated

from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

import json
import math
import datetime

from . import utils


_URI_HINT = "HTTP(S) URL, S3 URI (s3://...)"


class Thought(BaseModel):
    id_: str = Field(default_factory=utils.random_id8)
    owner: str
    message: str
    payload: Optional[Any] = Field(default=None)


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
    id: Optional[str] = None # DOC: from btoa(src)
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    style: Optional[Dict[str, Any]] = None

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


# ---------------------------------------------------------------------------
# Map state models (PLN-014)
# ---------------------------------------------------------------------------

class MapView(BaseModel):
    """Current viewport state of the MapLibre frontend map."""
    center_lon: float = Field(description="Longitude of the map center")
    center_lat: float = Field(description="Latitude of the map center")
    zoom: float = Field(default=10.0, description="MapLibre zoom level")
    bbox: Optional[List[float]] = Field(
        default=None,
        description="Current viewport bounding box [west, south, east, north]"
    )


class MapCommand(BaseModel):
    """A command produced by the MapAgent to be consumed by the frontend."""
    command_session: Optional[str]
    type: str = Field(description="Command type: 'move_view' | 'set_layer_style'")
    payload: Dict[str, Any] = Field(description="Command-specific payload")
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat(),
        description="ISO timestamp of command creation (UTC)"
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def _flatten_coords(geom: dict) -> list:
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])
    if gtype == "Point":
        return [coords] if coords else []
    if gtype in ("LineString", "MultiPoint"):
        return list(coords)
    if gtype == "Polygon":
        return [pt for ring in coords for pt in ring]
    if gtype == "MultiLineString":
        return [pt for line in coords for pt in line]
    if gtype == "MultiPolygon":
        return [pt for poly in coords for ring in poly for pt in ring]
    return []


def _bbox_from_coords(pts: list) -> tuple | None:
    if not pts:
        return None
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _polygon_area_km2(ring: list) -> float:
    n = len(ring)
    if n < 3:
        return 0.0
    area_rad = 0.0
    for i in range(n):
        j = (i + 1) % n
        area_rad += math.radians(ring[i][0]) * math.radians(ring[j][1])
        area_rad -= math.radians(ring[j][0]) * math.radians(ring[i][1])
    lat_mid = sum(p[1] for p in ring) / n
    return abs(area_rad) / 2 * (6371.0 ** 2) * math.cos(math.radians(lat_mid))


def _linestring_length_km(coords: list) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += _haversine_km(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
    return total


def compute_geometry_metadata(geom: dict) -> dict:
    """
    Compute spatial metadata from a GeoJSON geometry dict.

    Returns a dict with:
      - crs: always "EPSG:4326"
      - Point       → lon, lat
      - Polygon     → bbox (dict), area_km2 (float)
      - LineString  → bbox (dict), length_km (float)
      - Multi*      → num_features (int), bbox (dict)
    """
    if not isinstance(geom, dict):
        return {"crs": "EPSG:4326"}

    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])
    meta: dict = {"crs": "EPSG:4326"}

    if gtype == "Point":
        if coords and len(coords) >= 2:
            meta["lon"] = round(coords[0], 6)
            meta["lat"] = round(coords[1], 6)

    elif gtype == "Polygon":
        outer_ring = coords[0] if coords else []
        bbox = _bbox_from_coords(outer_ring)
        if bbox:
            w, s, e, n = bbox
            meta["bbox"] = {"west": round(w, 5), "south": round(s, 5),
                            "east": round(e, 5), "north": round(n, 5)}
        meta["area_km2"] = round(_polygon_area_km2(outer_ring), 2)

    elif gtype == "LineString":
        bbox = _bbox_from_coords(coords)
        if bbox:
            w, s, e, n = bbox
            meta["bbox"] = {"west": round(w, 5), "south": round(s, 5),
                            "east": round(e, 5), "north": round(n, 5)}
        meta["length_km"] = round(_linestring_length_km(coords), 2)

    elif gtype in ("MultiPoint", "MultiLineString", "MultiPolygon"):
        meta["num_features"] = len(coords)
        all_pts = _flatten_coords(geom)
        bbox = _bbox_from_coords(all_pts)
        if bbox:
            w, s, e, n = bbox
            meta["bbox"] = {"west": round(w, 5), "south": round(s, 5),
                            "east": round(e, 5), "north": round(n, 5)}

    return meta


class DrawnShape(BaseModel):
    """A geometry drawn by the user on the map."""
    shape_id: str = Field(description="Unique identifier for the shape")
    shape_type: Literal["point", "bbox", "linestring", "polygon"] = Field(
        description="Geometry type of the drawn shape"
    )
    geometry: Dict[str, Any] = Field(description="GeoJSON geometry object")
    label: Optional[str] = Field(default=None, description="Optional user label")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Pre-computed spatial metadata (crs, bbox, area_km2, length_km, etc.)"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat(),
        description="ISO timestamp of shape creation (UTC)"
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()



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