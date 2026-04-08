import os
import json
import requests

from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, AliasChoices, PrivateAttr

from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from saferplaces_multiagent.common.states import MABaseGraphState

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models


# ============================================================================
# Types
# ============================================================================

ProviderCode = Literal[
    "OVERTURE",
    "RER-REST/*",
    "VENEZIA-WFS/*",
    "VENEZIA-WFS-CRITICAL-SITES",
]

FloodMode = Literal["BUFFER", "IN-AREA", "ALL"]


# ============================================================================
# Constants
# ============================================================================

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

# Default parameter values
DEFAULT_WD_THRESH = 0.5
DEFAULT_FLOOD_MODE = "BUFFER"
DEFAULT_PROVIDER = "OVERTURE"
DEFAULT_T_SRS = "EPSG:4326"
DEFAULT_SUMMARY = True
DEFAULT_SUMMARY_ON = "subtype"
DEFAULT_STATS = True

# Allowed values for validation
FLOOD_MODES = ["BUFFER", "IN-AREA", "ALL"]
PROVIDER_CODES = ["OVERTURE", "RER-REST/*", "VENEZIA-WFS/*", "VENEZIA-WFS-CRITICAL-SITES"]


# ============================================================================
# Schema
# ============================================================================

class SaferBuildingsInputSchema(BaseModel):
    """
    Schema for detecting flooded buildings from a water depth raster.

    This tool identifies flooded buildings by combining:
    - A water depth raster (GeoTIFF) from a prior flood simulation
    - Building geometries (either from a local file/layer or fetched from a provider)

    Building sources:
      • Direct URL: https://example.com/buildings/rimini.geojson
      • S3 URI: s3://bucket/project/buildings.geojson
      • Layer Registry reference: "Buildings Rimini" (uses layer's src)
      • Provider fetch: OVERTURE, RER-REST/*, VENEZIA-WFS/*, VENEZIA-WFS-CRITICAL-SITES

    Note: `buildings` and `provider` are mutually exclusive.
    """

    # ============================================================================
    # Required Inputs
    # ============================================================================

    water: str = Field(
        ...,
        title="Water Depth Raster",
        description=(
            "Water depth raster (GeoTIFF) from a prior flood simulation.\n\n"
            "Sources:\n"
            "• Direct URL: https://example.com/floods/rimini-wd.tif\n"
            "• S3 URI: s3://bucket/project/rimini-wd.tif\n"
            "• Layer reference: 'Water Depth Rimini' (from Layer Registry)"
        ),
        examples=[
            "https://example.com/data/floods/rimini-wd.tif",
            "s3://bucket/project/rimini-wd.tif",
            "Water Depth Rimini",
        ],
        validation_alias=AliasChoices("water", "water_depth", "wd", "flood_raster"),
    )

    # ============================================================================
    # Optional Building Sources (mutually exclusive)
    # ============================================================================

    buildings: Optional[str] = Field(
        default=None,
        title="Buildings Vector (mutually exclusive with `provider`)",
        description=(
            "URL or S3 URI pointing to a buildings dataset (GeoJSON, GPKG).\n\n"
            "**Mutual exclusivity rule:** do NOT set `provider` if you provide `buildings`.\n\n"
            "Sources:\n"
            "• Direct URL: https://example.com/buildings/rimini.geojson\n"
            "• S3 URI: s3://bucket/project/buildings.geojson\n"
            "• Layer reference: 'Buildings Rimini' (from Layer Registry)"
        ),
        examples=[
            "https://example.com/data/buildings/rimini.geojson",
            "s3://bucket/project/buildings.geojson",
            "Buildings Rimini",
        ],
        validation_alias=AliasChoices("buildings", "buildings_path", "building_layer"),
    )

    provider: Optional[ProviderCode] = Field(
        default=None,
        title="Buildings Provider (mutually exclusive with `buildings`)",
        description=(
            "Provider to automatically fetch building geometries when no `buildings` file is given.\n\n"
            "**Mutual exclusivity rule:** do NOT set `buildings` if you set `provider`.\n\n"
            "Allowed values:\n"
            "• `OVERTURE` — global coverage (default when no buildings are available)\n"
            "• `RER-REST/*` — Emilia-Romagna region, Italy\n"
            "• `VENEZIA-WFS/*` — Venice area\n"
            "• `VENEZIA-WFS-CRITICAL-SITES` — Venice critical sites only"
        ),
        examples=["OVERTURE", "RER-REST/*", "VENEZIA-WFS/*"],
        validation_alias=AliasChoices("provider", "buildings_provider", "data_provider"),
    )

    # ============================================================================
    # Spatial Scope
    # ============================================================================

    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description=(
            "Geographic extent in EPSG:4326 using named keys west, south, east, north.\n"
            "If omitted, the water raster total bounds are used."
        ),
        examples=[{"west": 12.52, "south": 44.01, "east": 12.60, "north": 44.08}],
        validation_alias=AliasChoices("bbox", "aoi", "extent", "bounds", "bounding_box"),
    )

    t_srs: Optional[str] = Field(
        default=None,
        title="Target CRS (EPSG)",
        description=(
            "Target spatial reference system for the output (e.g., 'EPSG:4326').\n"
            "If None, CRS of buildings is used when provided, otherwise CRS of the water raster."
        ),
        examples=["EPSG:4326", "EPSG:3857"],
        validation_alias=AliasChoices("t_srs", "target_srs", "crs", "out_crs", "srs"),
    )

    # ============================================================================
    # Flood Logic & Thresholds
    # ============================================================================

    wd_thresh: float = Field(
        default=DEFAULT_WD_THRESH,
        title="Water Depth Threshold (m)",
        description="Buildings are considered flooded when water depth ≥ this threshold (meters).",
        examples=[0.5, 0.3, 1.0],
        validation_alias=AliasChoices("wd_thresh", "threshold", "flood_threshold", "water_depth_threshold"),
    )

    flood_mode: FloodMode = Field(
        default=DEFAULT_FLOOD_MODE,
        title="Flood Search Mode",
        description=(
            "Where to search for flood relative to buildings:\n"
            "• `BUFFER` — around building geometry (default; recommended for OVERTURE and RER-REST)\n"
            "• `IN-AREA` — inside geometry (recommended for VENEZIA-WFS sources)\n"
            "• `ALL` — both approaches"
        ),
        examples=["BUFFER", "IN-AREA"],
        validation_alias=AliasChoices("flood_mode", "search_mode", "flood_search_mode"),
    )

    # ============================================================================
    # Output Controls
    # ============================================================================

    only_flood: bool = Field(
        default=False,
        title="Return Only Flooded Buildings",
        description="If True, exclude non-flooded buildings from the output.",
        examples=[True],
        validation_alias=AliasChoices("only_flood", "flooded_only", "filter_flooded"),
    )

    stats: bool = Field(
        default=False,
        title="Compute Per-Building Water Depth Statistics",
        description=(
            "If True, compute water depth statistics per flooded building (wd_min, wd_mean, wd_max). "
            "This is an expensive operation — use only when explicitly requested."
        ),
        examples=[True],
        validation_alias=AliasChoices("stats", "compute_stats", "per_building_stats"),
    )

    summary: bool = Field(
        default=False,
        title="Compute Aggregated Summary",
        description=(
            "If True, compute an aggregated summary of flooded buildings grouped by `summary_on`.\n"
            "Default grouping depends on provider: OVERTURE → subtype, "
            "RER-REST → service_class, VENEZIA-WFS → service_id."
        ),
        examples=[True],
        validation_alias=AliasChoices("summary", "aggregate", "aggregate_summary"),
    )

    summary_on: Optional[List[str]] = Field(
        default=None,
        title="Summary Grouping Columns",
        description=(
            "List of attribute columns to group the summary by.\n"
            "If omitted and summary=True, defaults depend on provider "
            "(OVERTURE → subtype, RER-REST → service_class, VENEZIA-WFS → service_id)."
        ),
        examples=[["subtype"], ["service_class"], ["building_type", "class"]],
        validation_alias=AliasChoices("summary_on", "group_by", "summary_columns"),
    )

    out: Optional[str] = Field(
        default=None,
        title="Output Vector Path",
        description=(
            "Destination URL or S3 URI for the output vector file (GeoJSON/GPKG).\n"
            "If omitted, an S3 path is auto-generated.\n\n"
            "Output contains all buildings with `is_flooded` flag; "
            "per-building stats (wd_min, wd_mean, wd_max) are included if stats=True."
        ),
        examples=[
            "https://example.com/results/flooded_buildings.geojson",
            "s3://bucket/project/output/flooded_buildings.geojson",
        ],
        validation_alias=AliasChoices("out", "output", "output_path", "out_path"),
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging for troubleshooting.",
        examples=[True],
    )


# ============================================================================
# SaferBuildings Tool
# ============================================================================

class SaferBuildingsTool(BaseTool):
    """
    Tool for detecting flooded buildings from a water depth raster.

    Features:
      • Identify flooded buildings from a water depth raster (GeoTIFF)
      • Two modes for building geometries: direct file or provider-based fetch
      • Configurable flood depth threshold and search mode
      • Optional per-building water depth statistics (wd_min, wd_mean, wd_max)
      • Optional aggregated summary grouped by building attributes
      • Provider support: OVERTURE (global), RER-REST (Emilia-Romagna), VENEZIA-WFS (Venice)

    Example use cases:
      • "Identify flooded buildings from the latest flood simulation"
      • "Show me which buildings in Rimini are flooded with a 50 cm threshold"
      • "Compute per-building water depth stats for flooded buildings in Venice"
    """

    short_description: ClassVar[str] = (
        "Detects flooded buildings from a water depth raster and returns a vector layer with "
        "per-building flood status (`is_flooded`). "
        "Key params: water (required, water depth raster or layer reference), "
        "buildings (optional, buildings file or layer reference, mutually exclusive with provider), "
        "provider (optional, OVERTURE/RER-REST/VENEZIA-WFS; use when no buildings file is available), "
        "bbox (geographic extent), wd_thresh (flood depth threshold in meters, default 0.5m), "
        "stats (per-building water depth statistics: wd_min, wd_mean, wd_max), "
        "summary (aggregated statistics grouped by building type). "
        "Requires a prior flood simulation output (water depth raster) as input."
    )

    _graph_state: Optional[MABaseGraphState] = PrivateAttr(default=None)

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the SaferBuildings Tool."""
        super().__init__(
            name=N.SAFERBUILDINGS_TOOL,
            description=(
                "Detect flooded buildings from a water depth raster (GeoTIFF).\n\n"
                "Building geometries can be provided as:\n"
                "• A direct file (`buildings`) — URL, S3 URI, or Layer Registry reference\n"
                "• A provider fetch (`provider`) — OVERTURE (global), RER-REST/*, VENEZIA-WFS/*\n"
                "Note: `buildings` and `provider` are mutually exclusive.\n\n"
                "Output: Vector file with all buildings and `is_flooded` flag. "
                "Optional per-building stats (wd_min, wd_mean, wd_max) when stats=True."
            ),
            args_schema=SaferBuildingsInputSchema,
            **kwargs
        )

    def _set_graph_state(self, graph_state: MABaseGraphState) -> None:
        """Set the graph state for the tool."""
        self._graph_state = graph_state

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        return {
            'buildings': [
                self._validate_buildings_provider_exclusivity,
            ],
            'flood_mode': [
                self._validate_flood_mode,
            ],
            'wd_thresh': [
                self._validate_wd_thresh,
            ],
        }

    @staticmethod
    def _validate_buildings_provider_exclusivity(
        buildings: Optional[str] = None,
        provider: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """Validate that buildings and provider are not both set."""
        if buildings is not None and provider is not None:
            return (
                "buildings and provider are mutually exclusive. "
                "Provide either a buildings file/layer or a provider name, not both."
            )
        return None

    @staticmethod
    def _validate_flood_mode(flood_mode: Optional[str] = None, **kwargs) -> Optional[str]:
        """Validate flood mode is one of the allowed values."""
        if flood_mode and flood_mode not in FLOOD_MODES:
            return f"Invalid flood_mode '{flood_mode}'. Must be one of: {FLOOD_MODES}"
        return None

    @staticmethod
    def _validate_wd_thresh(wd_thresh: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate water depth threshold is positive."""
        if wd_thresh is not None and wd_thresh < 0:
            return f"wd_thresh must be >= 0, got {wd_thresh}"
        return None

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""

        def infer_provider(**kwargs: Any) -> str:
            """Infer default provider for building geometries."""
            return DEFAULT_PROVIDER

        def infer_t_srs(**kwargs: Any) -> str:
            """Infer default target spatial reference system."""
            return DEFAULT_T_SRS

        def infer_out(**kwargs: Any) -> str:
            """Infer default S3 output path for the flooded buildings vector."""
            state = kwargs.pop('_graph_state', None)
            filename = f"flooded-buildings-{utils.random_id8()}.geojson"
            return f"{s3_utils._STATE_BUCKET_(state)}/saferbuildings-out/{filename}"
        
        def infer_summary(**kwargs: Any) -> bool:
            """Infer default summary flag."""
            return DEFAULT_SUMMARY
        
        def infer_summary_on(**kwargs: Any) -> bool:
            """Infer default summary_on flag."""
            return DEFAULT_SUMMARY_ON

        def infer_stats(**kwargs: Any) -> bool:
            """Infer default stats flag."""
            return DEFAULT_STATS


        return {
            'provider': infer_provider,
            't_srs': infer_t_srs,
            'out': infer_out,
            'summary': infer_summary,
            'summary_on': infer_summary_on,
            'stats': infer_stats,
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the SaferBuildings tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        # Build API payload
        payload = self._build_api_payload(kwargs)

        # Call SaferBuildings API
        api_response = self._call_saferbuildings_api(payload)

        # Process response
        return self._process_api_response(payload, api_response)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""
        bbox_data = kwargs.get('bbox')
        if bbox_data is not None:
            if hasattr(bbox_data, 'to_list'):
                bbox_list = bbox_data.to_list()
            elif isinstance(bbox_data, dict):
                bbox_list = [bbox_data['west'], bbox_data['south'], bbox_data['east'], bbox_data['north']]
            else:
                bbox_list = bbox_data
        else:
            bbox_list = None

        tool_args = {
            'water': kwargs['water'],
            'buildings': kwargs.get('buildings'),
            'provider': kwargs.get('provider'),
            'bbox': bbox_list,
            't_srs': kwargs.get('t_srs'),
            'wd_thresh': kwargs.get('wd_thresh', DEFAULT_WD_THRESH),
            'flood_mode': kwargs.get('flood_mode', DEFAULT_FLOOD_MODE),
            'only_flood': kwargs.get('only_flood', False),
            'stats': kwargs.get('stats', False),
            'summary': kwargs.get('summary', False),
            'summary_on': kwargs.get('summary_on'),
            'out': kwargs.get('out'),
        }

        credentials = {
            'user': os.getenv("SAFERPLACES_API_USER"),
            'token': os.getenv("SAFERPLACES_API_TOKEN"),
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

    def _call_saferbuildings_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the SaferBuildings execution API.

        Args:
            payload: Request payload

        Returns:
            API response object
        """
        api_url = self._get_api_url()

        print(f"Calling SaferBuildings API at {api_url} with payload:\n", json.dumps(payload, indent=2))

        return requests.post(api_url, json=payload)

    @staticmethod
    def _get_api_url() -> str:
        """Get SaferBuildings API URL from environment."""
        api_root = os.getenv('SAFERPLACES_API_ROOT', 'http://localhost:5000')
        return f"{api_root}/processes/safer-buildings-process/execution"

    def _process_api_response(
        self,
        req_payload: Dict[str, Any],
        api_response: Any,
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            req_payload: Original request payload
            api_response: Response from SaferBuildings API

        Returns:
            Formatted tool response
        """
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"SaferBuildings API request failed: {api_response.status_code} - {api_response.text}",
            }

        response_data = api_response.json()

        # Validate response structure
        expected_id = "saferplacesapi.SaferBuildingsProcessor"
        if response_data.get('id') != expected_id or not response_data.get('files'):
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected SaferBuildings API response: {response_data}",
            }

        tool_output = {
            'status': STATUS_SUCCESS,
            'tool_output': self._format_tool_output(req_payload, response_data),
        }

        print('SaferBuildings tool output:\n', tool_output)
        return tool_output

    def _format_tool_output(
        self,
        req_payload: Dict[str, Any],
        response_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the tool output."""
        source = response_data['message']['body']['result']['s3_uri']

        output_description = (
            "Building flood analysis completed. "
            "Here is the generated flooded buildings vector layer with its S3 URI and metadata."
        )

        metadata: Dict[str, Any] = {
            'features_type': 'flooded-buildings',
            **utils.vector_specs(source),
        }

        # Include summary data if present in response
        summary_data = response_data.get('message', {}).get('body', {}).get('result', {}).get('summary')
        if summary_data:
            metadata['summary'] = summary_data

        return {
            'description': output_description,
            'data': {
                'variable': 'flooded-buildings',
                'source': source,
                'metadata': metadata,
            },
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
