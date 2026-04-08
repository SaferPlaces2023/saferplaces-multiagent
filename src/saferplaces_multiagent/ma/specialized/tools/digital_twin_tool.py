import os
import json
import requests

from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, AliasChoices, PrivateAttr

from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models
from ....common.states import MABaseGraphState


# ============================================================================
# Constants
# ============================================================================

# Default parameters
DEFAULT_PIXELSIZE = 10.0
DEFAULT_OUT_FORMAT = "COG"
DEFAULT_BUILDING_EXTRUDE_HEIGHT = 10.0
DEFAULT_T_SRS = "EPSG:3857"

# Available layer categories and their layers
LAYER_CATEGORIES = {
    "elevation": ["dem", "valleydepth", "tri", "tpi"],
    "hydrology": ["slope", "dem_filled", "flow_dir", "flow_accum", "streams", "hand", "twi", "river_network", "river_distance"],
    "constructions": ["buildings", "dem_buildings", "dem_filled_buildings", "roads"],
    "landcover": ["landuse", "manning", "ndvi", "ndwi", "ndbi", "sea_mask"],
    "soil": ["sand", "clay"],
}

ALL_LAYER_NAMES = [layer for layers in LAYER_CATEGORIES.values() for layer in layers]

# Typed layer name (used in schema for LLM-facing tool args)
TerraTwinLayerName = Literal[
    "dem", "valleydepth", "tri", "tpi",
    "slope", "dem_filled", "flow_dir", "flow_accum", "streams", "hand", "twi", "river_network", "river_distance",
    "buildings", "dem_buildings", "dem_filled_buildings", "roads",
    "landuse", "manning", "ndvi", "ndwi", "ndbi", "sea_mask",
    "sand", "clay",
]

# Output format options
OutFormat = Literal["GTiff", "COG"]

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

TERRATWIN_LAYER_NAME_TO_SURFACE_TYPE = {
    "dem": "dem",
    # TODO: complete with definition of the others
}



# ============================================================================
# Schema
# ============================================================================

class DigitalTwinInputSchema(BaseModel):
    """
    Schema for generating a comprehensive geospatial Digital Twin using Terra-Twin.

    A Digital Twin is a complete set of geospatial base layers for a given Area of Interest (AOI),
    covering elevation, hydrology, constructions, land cover, and soil properties.
    It produces 25 spatially-aligned layers from global open data sources.

    The basic Digital Twin consists of a single layer: the **DEM (Digital Elevation Model)**.
    Select only ['dem'] for a minimal setup, or add more layer names as needed.

    This tool is typically the **first step** in any geospatial workflow — it provides
    the foundational layers (DEM, buildings, land-use, hydrology, etc.) needed before
    running downstream analyses like flood simulations or risk assessments.
    """

    # ============================================================================
    # Required: Spatial Scope and Layer Selection
    # ============================================================================

    dem_reference: Optional[str] = Field(
        default=None,
        title="DEM Reference",
        description=(
            "Reference for the Digital Elevation Model (DEM) layer with which to align other layers.\n"
            "When provided, then bbox does not need to be specified."
        ),
        examples=[
            "s3://bucket.com/path/to/dem.tif"
        ],
        validation_alias=AliasChoices("dem_reference", "dem_template", "dem")
    )

    bbox: base_models.BBox = Field(
        default=None,
        title="Area of Interest (bbox)",
        description=(
            "Geographic extent in EPSG:4326 (WGS84) defining the Area of Interest.\n"
            "Uses named keys: west (min longitude), south (min latitude), "
            "east (max longitude), north (max latitude).\n\n"
            "Example: {'west': 12.31, 'south': 45.42, 'east': 12.36, 'north': 45.45}\n\n"
            "The bbox determines the spatial extent of ALL generated layers. "
            "All outputs will be clipped and aligned to this bounding box."
        ),
        examples=[
            {"west": 12.31, "south": 45.42, "east": 12.36, "north": 45.45},
            {"west": 9.05, "south": 45.42, "east": 9.25, "north": 45.55},
        ],
        validation_alias=AliasChoices("bbox", "aoi", "extent", "bounds", "bounding_box"),
    )

    layers: List[TerraTwinLayerName] = Field(
        ...,
        title="Layer Selection",
        description=(
            "Flat list of layer names to generate. "
            "Each name must be one of the known layer names across all categories:\n"
            "• elevation: dem, valleydepth, tri, tpi\n"
            "• hydrology: slope, dem_filled, flow_dir, flow_accum, streams, hand, twi, river_network, river_distance\n"
            "• constructions: buildings, dem_buildings, dem_filled_buildings, roads\n"
            "• landcover: landuse, manning, ndvi, ndwi, ndbi, sea_mask\n"
            "• soil: sand, clay\n\n"
            "DEFAULT RULE: for generic requests (new project, 'create a digital twin', 'I need a DEM', "
            "flood simulation setup with no specific layer requirements) → use only ['dem'].\n\n"
            "Examples:\n"
            "  ['dem']  → DEFAULT — use for any generic/unspecified request (new project, DEM only, digital twin)\n"
            "  ['dem', 'slope', 'hand']  → DEM + slope + HAND (only if explicitly requested)\n"
            "  ['dem', 'buildings', 'landuse', 'manning']  → DEM + constructions + land cover (only if explicitly requested)\n"
        ),
        examples=[
            ["dem"],
            ["dem", "slope", "hand", "twi"],
            ["dem", "buildings", "roads", "landuse", "manning"],
        ],
        validation_alias=AliasChoices("layers", "layer_selection", "selected_layers"),
    )

    # ============================================================================
    # Optional: Resolution & Format
    # ============================================================================

    pixelsize: Optional[float] = Field(
        default=None,
        title="Output Resolution (meters)",
        description=(
            "Target ground resolution in meters for all raster outputs. "
            "Controls the spatial detail of the generated layers.\n\n"
            "Guidelines:\n"
            "• 5m → high detail, suitable for small urban areas\n"
            "• 10m → balanced detail/performance (default)\n"
            "• 30m → lower detail, suitable for large regions\n\n"
            "If None, defaults to 10m. Values are clamped between 5m and 30m."
        ),
        examples=[5.0, 10.0, 30.0],
        validation_alias=AliasChoices("pixelsize", "pixel_size", "resolution", "res", "gsd"),
    )

    out_format: Optional[OutFormat] = Field(
        default=DEFAULT_OUT_FORMAT,
        title="Output Raster Format",
        description=(
            "Format for output raster files:\n"
            "• GTiff → standard GeoTIFF (default)\n"
            "• COG → Cloud Optimized GeoTIFF with pyramid overviews and tiled layout, "
            "optimized for efficient cloud-based access and streaming"
        ),
        examples=["GTiff", "COG"],
        validation_alias=AliasChoices("out_format", "format", "output_format"),
    )

    # ============================================================================
    # Optional: Region Name
    # ============================================================================

    region_name: Optional[str] = Field(
        default=None,
        title="Region Name",
        description=(
            "Human-readable identifier for the generated Digital Twin (e.g., 'Venice', 'Milan North'). "
            "Used for organizing output files and as a label in the Layer Registry. "
            "If omitted, a unique identifier is auto-generated."
        ),
        examples=["Venice", "Milan North", "Rome Center"],
        validation_alias=AliasChoices("region_name", "name", "region", "label"),
    )

    # ============================================================================
    # Optional: Clipping Geometry
    # ============================================================================

    clip_geometry: Optional[str] = Field(
        default=None,
        title="Clip Geometry",
        description=(
            "Optional geometry to clip all output layers to a specific boundary "
            "(e.g., administrative area, watershed, custom polygon).\n\n"
            "Accepts:\n"
            "• GDAL URI: 's3://bucket/boundaries.gpkg|layer=cities|where=name=\"Venice\"'\n"
            "• GeoJSON FeatureCollection string\n\n"
            "When provided, the bounding box is recalculated as the intersection "
            "between the original bbox and the clipping geometry."
        ),
        examples=[None],
        validation_alias=AliasChoices("clip_geometry", "clip", "mask", "boundary"),
    )

    # ============================================================================
    # Optional: Advanced Parameters
    # ============================================================================

    t_srs: Optional[str] = Field(
        default=None,
        title="Target Spatial Reference System",
        description=(
            "Target CRS/SRS for all output rasters (e.g., 'EPSG:32633'). "
            "If None, outputs use EPSG:4326 (WGS84). "
            "Use a projected CRS (e.g., UTM zone) for area/distance calculations."
        ),
        examples=["EPSG:4326", "EPSG:32633", "EPSG:3857"],
        validation_alias=AliasChoices("t_srs", "target_srs", "crs", "out_crs", "srs"),
    )

    building_extrude_height: Optional[float] = Field(
        default=DEFAULT_BUILDING_EXTRUDE_HEIGHT,
        title="Building Extrusion Height (meters)",
        description=(
            "Height in meters used to extrude building footprints onto the DEM, "
            "creating the dem_buildings layer. Default: 10.0m. "
            "Only relevant when 'constructions' category is included."
        ),
        examples=[10.0, 15.0],
        validation_alias=AliasChoices("building_extrude_height", "extrude_height", "building_height"),
    )

    dem_dataset: Optional[str] = Field(
        default=None,
        title="DEM Dataset Override",
        description=(
            "Specific DEM dataset to use instead of auto-selection. "
            "Leave as None to let the tool automatically select the best available DEM "
            "based on the AOI location.\n\n"
            "Auto-selection priorities by region:\n"
            "• Italy → GECOSISTEMA/ITALY\n"
            "• Netherlands → AHN/NETHERLANDS/05M\n"
            "• Belgium → GECOSISTEMA/BELGIUM/1M\n"
            "• France → IGN/RGE_ALTI/1M\n"
            "• Spain → IGN/ES/2M\n"
            "• UK → UK/LIDAR\n"
            "• USA → USGS/3DEP/1M\n"
            "• Europe fallback → COPERNICUS/EUDEM\n"
            "• Global fallback → NASA/NASADEM_HGT/001"
        ),
        examples=[None, "COPERNICUS/EUDEM", "USGS/3DEP/1M"],
        validation_alias=AliasChoices("dem_dataset", "dem_source", "dtm_dataset"),
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging for troubleshooting.",
        examples=[True],
    )


# ============================================================================
# Digital Twin Tool
# ============================================================================

class DigitalTwinTool(BaseTool):
    """
    Tool for generating a comprehensive geospatial Digital Twin using Terra-Twin.

    Features:
      • Generate 25 spatially-aligned geospatial layers from global open data
      • Elevation analysis: DEM, valley depth, terrain ruggedness (TRI), topographic position (TPI)
      • Hydrological modeling: slope, flow direction/accumulation, streams, HAND, TWI, river distance
      • Construction integration: building/road footprints, DEM with building extrusion
      • Land cover: ESA WorldCover, Manning roughness, NDVI, NDWI, NDBI, sea mask
      • Soil properties: sand and clay content from OpenLandMap
      • Selective layer generation: choose only the categories/layers needed
      • Configurable resolution, output format (GeoTIFF/COG), and CRS
      • Optional clipping to a specific geometry (admin boundary, watershed)
      • Global coverage with region-aware DEM auto-selection

    Example use cases:
      • "Create a Digital Twin for Venice to assess flood risk"
      • "Generate DEM, slope, and HAND layers for urban planning in Rome"
      • "Prepare all base layers for a new project area in northern Italy"
      • "Build elevation and hydrology layers at 5m resolution for a small catchment"
    """

    short_description: ClassVar[str] = (
        "Generates a comprehensive geospatial Digital Twin (up to 25 layers) for any Area of Interest. "
        "Produces spatially-aligned raster and vector layers across 5 categories: "
        "elevation (DEM, valley depth, TRI, TPI), "
        "hydrology (slope, flow dir/accum, streams, HAND, TWI, river distance), "
        "constructions (buildings, roads, DEM with buildings), "
        "landcover (landuse, Manning roughness, NDVI, NDWI, NDBI, sea mask), "
        "soil (sand, clay). "
        "Key params: bbox (required, EPSG:4326), layers (required flat list of layer names, "
        "e.g. ['dem'] for minimal setup or ['dem', 'slope', 'hand'] for more), "
        "pixelsize (resolution in meters, default 10m), out_format (GTiff or COG). "
        "This is typically the FIRST step in any geospatial workflow — it provides the foundational "
        "base layers needed before running simulations, risk assessments, or spatial analyses. "
        "Use it when the user needs a DEM, terrain data, buildings, land-use, hydrology, or any "
        "combination of geospatial base layers for a given area. Global coverage."
    )

    _graph_state: Optional[MABaseGraphState] = PrivateAttr(default=None)

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the DigitalTwin Tool."""
        super().__init__(
            name=N.DIGITAL_TWIN_TOOL,
            description=(
                "Generate a comprehensive geospatial Digital Twin for any Area of Interest (AOI) "
                "using Terra-Twin.\n\n"
                "### Purpose\n"
                "This tool is typically the **first step** in a geospatial workflow. It produces "
                "up to 25 spatially-aligned layers from global open data sources, providing the "
                "foundational base layers needed before running flood simulations, risk assessments, "
                "or spatial analyses.\n\n"
                "### What it creates (5 categories, 25 layers)\n"
                "- **Elevation** (4 layers): DEM, valley depth, TRI (terrain ruggedness), TPI (topographic position)\n"
                "- **Hydrology** (9 layers): slope, filled DEM, flow direction, flow accumulation, "
                "streams, HAND (Height Above Nearest Drainage), TWI (Topographic Wetness Index), "
                "river network (vector), river distance\n"
                "- **Constructions** (4 layers): building footprints (vector), roads (vector), "
                "DEM with buildings, filled DEM with buildings\n"
                "- **Land Cover** (6 layers): land-use classification, Manning roughness, "
                "NDVI, NDWI, NDBI, sea mask\n"
                "- **Soil** (2 layers): sand content, clay content\n\n"
                "### Inputs\n"
                "- `dem_reference` (optional): reference DEM for the AOI\n"
                "- `bbox` (required if no dem_reference is provided): AOI as EPSG:4326 bounding box (west, south, east, north)\n"
                "- `layers` (required): flat list of layer names to generate\n"
                "  - minimal DEM-only: ['dem']\n"
                "  - example: ['dem', 'slope', 'hand', 'buildings', 'landuse', 'manning']\n"
                "  - all names: dem, valleydepth, tri, tpi, slope, dem_filled, flow_dir, flow_accum, "
                "streams, hand, twi, river_network, river_distance, buildings, dem_buildings, "
                "dem_filled_buildings, roads, landuse, manning, ndvi, ndwi, ndbi, sea_mask, sand, clay\n"
                "- `pixelsize` (optional): resolution in meters (default: 10m)\n"
                "- `out_format` (optional): GTiff (default) or COG\n"
                "- `region_name` (optional): human-readable label\n"
                "- `clip_geometry` (optional): clip outputs to a specific boundary\n\n"
                "### Output\n"
                "S3 URIs for each generated layer, organized by category."
            ),
            args_schema=DigitalTwinInputSchema,
            **kwargs
        )

    def _set_graph_state(self, graph_state: MABaseGraphState) -> None:
        """Set the graph state for the tool."""
        self._graph_state = graph_state

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        return {
            'pixelsize': [
                self._validate_pixelsize,
            ],
            'layers': [
                self._validate_layers,
            ],
        }

    @staticmethod
    def _validate_pixelsize(pixelsize: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate pixel size is positive if provided."""
        if pixelsize is not None and pixelsize <= 0:
            return f"pixelsize must be > 0, got {pixelsize}"
        return None

    @staticmethod
    def _validate_layers(layers: Optional[List[TerraTwinLayerName]] = None, **kwargs) -> Optional[str]: # type: ignore
        """Validate that all layer names are known."""
        if layers is None:
            return None
        for layer in layers:
            if layer not in ALL_LAYER_NAMES:
                return (
                    f"Unknown layer '{layer}'. "
                    f"Valid layer names: {', '.join(ALL_LAYER_NAMES)}"
                )
        return None

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""

        def infer_pixelsize(**kwargs: Any) -> Optional[float]:
            """Clamp pixel size between 5 and 30 meters, default to 10m."""
            pixelsize = kwargs.get('pixelsize')
            if pixelsize is None:
                return DEFAULT_PIXELSIZE
            return max(5, min(30, pixelsize))

        def infer_region_name(**kwargs: Any) -> str:
            """Generate a unique region name if not provided."""
            region_name = kwargs.get('region_name')
            if region_name:
                return region_name
            return f"dt-{utils.random_id8()}"

        def infer_out_format(**kwargs: Any) -> str:
            """Default to COG."""
            return DEFAULT_OUT_FORMAT   # DOC: force to COG (ready for maplibre)
        
        def infer_t_srs(**kwargs: Any) -> str:
            """Default to EPSG:3857."""
            return kwargs.get('t_srs', DEFAULT_T_SRS)   # DOC: Force to Mercator (ready for maplibre)

        return {
            'pixelsize': infer_pixelsize,
            'region_name': infer_region_name,
            'out_format': infer_out_format,
            't_srs': infer_t_srs,
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the Digital Twin generation tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        # Build API payload
        payload = self._build_api_payload(kwargs)

        # Call Terra-Twin API
        api_response = self._call_terra_twin_api(payload)

        # Process response
        return self._process_api_response(api_response, kwargs)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""

        if kwargs.get('bbox'):
            bbox = kwargs['bbox']
            if hasattr(bbox, 'to_list'):
                bbox = bbox.to_list()
            elif isinstance(bbox, dict):
                bbox = [bbox['west'], bbox['south'], bbox['east'], bbox['north']]
        else:
            bbox = None

        state = self._graph_state
        output_bucket = s3_utils._STATE_BUCKET_(state) if state else None

        tool_args = {
            'dem_template': kwargs.get('dem_reference'),
            'bbox': bbox,
            'output_bucket': output_bucket,
            'region_name': kwargs.get('region_name'),
            'pixelsize': kwargs.get('pixelsize', DEFAULT_PIXELSIZE),
            't_srs': kwargs.get('t_srs', 'EPSG:4326'),
            'building_extrude_height': kwargs.get('building_extrude_height', DEFAULT_BUILDING_EXTRUDE_HEIGHT),
            'dem_database': kwargs.get('dem_dataset'),
            'out_format': kwargs.get('out_format', DEFAULT_OUT_FORMAT),
        }

        if kwargs.get('layers') is not None:
            tool_args['layers'] = self._layers_list_to_dict(kwargs['layers'])

        if kwargs.get('clip_geometry') is not None:
            tool_args['clip_geometry'] = kwargs['clip_geometry']

        credentials = {
            'token': os.getenv("TERRATWIN_API_TOKEN"),
        }

        debug_config = {
            'debug': kwargs.get('debug', True),
        }

        return {
            'inputs': {
                **tool_args,
                **credentials,
                **debug_config,
            }
        }

    @staticmethod
    def _layers_list_to_dict(layers: List[str]) -> Dict[str, List[str]]:
        """Convert a flat list of layer names to the nested category dict expected by the API."""
        result: Dict[str, List[str]] = {}
        for layer in layers:
            for category, category_layers in LAYER_CATEGORIES.items():
                if layer in category_layers:
                    result.setdefault(category, []).append(layer)
                    break
        return result

    def _call_terra_twin_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the Terra-Twin execution API.

        Args:
            payload: Request payload

        Returns:
            API response object
        """
        api_url = self._get_api_url()

        print(f"Calling Terra-Twin API at {api_url} with payload:\n", json.dumps(payload, indent=2))

        return requests.post(api_url, json=payload)

    @staticmethod
    def _get_api_url() -> str:
        """Get Terra-Twin API URL from environment."""
        api_root = os.getenv('TERRATWIN_API_ROOT', 'http://localhost:5002')
        return f"{api_root}/processes/terra-twin-process/execution"

    def _process_api_response(
        self,
        api_response: Any,
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            api_response: Response from Terra-Twin API
            kwargs: Original tool arguments

        Returns:
            Formatted tool response
        """
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"Terra-Twin API request failed with status {api_response.status_code}: {api_response.text}",
            }

        response_data = api_response.json()
        print(f"Terra-Twin API response:\n", json.dumps(response_data, indent=2))

        return {
            'status': STATUS_SUCCESS,
            'tool_output': self._format_tool_output(response_data, kwargs),
        }
    
    def _surface_type_from_layer_name(self, layer_name: str) -> str:
        """
        Map a layer name to a well-known standard surface type.
        """
        return TERRATWIN_LAYER_NAME_TO_SURFACE_TYPE.get(layer_name, layer_name)

    def _format_tool_output(
        self,
        response_data: Dict[str, Any],
        kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Format API response into structured tool output.

        Args:
            response_data: Parsed JSON from Terra-Twin API
            kwargs: Original tool arguments

        Returns:
            Structured output with description and layer data
        """
        output_description = (
            "Digital Twin generated successfully. "
            "Here are the generated geospatial layers organized by category, "
            "with their S3 URIs and metadata:"
        )

        formatted_layers = dict()

        for category in LAYER_CATEGORIES:
            category_data = response_data.get(category)
            if not (category_data and isinstance(category_data, dict)):
                continue
            for layer_name, layer_uri in category_data.items():
                layer_type = "vector" if layer_name in ("buildings", "roads", "river_network") else "raster"
                metadata = dict()
                if layer_type == 'raster':
                    metadata = {
                        'surface_type': self._surface_type_from_layer_name(layer_name),
                        ** utils.raster_specs(layer_uri),
                    }
                elif layer_type == 'vector':
                    metadata = {
                        ** utils.vector_specs(layer_uri),
                    }
                formatted_layers[layer_name] = {
                    'variable': layer_name,
                    'category': category,
                    'source': layer_uri,
                    'metadata': metadata
                }

        return {
            'description': output_description,
            'data': formatted_layers,
        }

    def _run(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Run the tool (LangChain BaseTool interface).

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool execution result
        """
        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop("run_manager", None)
        return super()._run(tool_args=kwargs, run_manager=run_manager)
