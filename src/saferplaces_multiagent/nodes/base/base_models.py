from typing import Optional, Union, List, Dict, Any, Literal
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

import datetime

from ...common import utils


_URI_HINT = "HTTP(S) URL, S3 URI (s3://...)"

PlanConfirmation = Literal["accepted", "rejected", "pending"]

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
    
    def lat_range(self) -> List[float]:
        """
        Get the latitude range as [south, north].
        """
        return [self.south, self.north]
    
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