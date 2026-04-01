import os
import json
import requests
import datetime

from typing import Any, ClassVar, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, AliasChoices, PrivateAttr

from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from saferplaces_multiagent.common.states import MABaseGraphState

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models


# ============================================================================
# Constants
# ============================================================================

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

# Land use providers available
LANDUSE_PROVIDERS = [
    "RER/LANDUSE",
    "ESA/LANDUSE/V100",
    "CUSTOM/LANDUSE/FBVI",
    "CUSTOM/LANDUSE/RER/AIB",
]

# Wind direction range (meteorological degrees)
WIND_DIRECTION_MIN = 0.0
WIND_DIRECTION_MAX = 360.0

# Moisture range [0, 1]
MOISTURE_MIN = 0.0
MOISTURE_MAX = 1.0

# Default parameter values
DEFAULT_T_SRS = "EPSG:3857"
DEFAULT_TIME_MAX = 7200          # 2 hours in seconds
DEFAULT_TIME_STEP_INTERVAL = 900  # 15 minutes in seconds
DEFAULT_MOISTURE = 0.15           # 15% fuel moisture
DEFAULT_LANDUSE_PROVIDER = "ESA/LANDUSE/V100"


# ============================================================================
# Schema
# ============================================================================

class SaferFireInputSchema(BaseModel):
    """
    Schema for running wildland fire propagation simulations using safer-fire.

    This tool executes fire spread simulations using:
    - A Digital Elevation Model (DEM/DTM) as terrain elevation and slope input
    - Ignition sources (points, lines, or polygon geometries)
    - Wind conditions (direction and speed as constant scalars)
    - Optional land use for fuel mapping

    Input Sources:
      • Direct URL: https://example.com/dem.tif
      • S3 URI: s3://bucket/project/dem.tif
      • Layer Registry reference: "Venice DEM" (uses layer's src)
    """

    # ============================================================================
    # Required Inputs
    # ============================================================================

    dem: str = Field(
        ...,
        title="Digital Elevation Model (DEM/DTM)",
        description=(
            "Terrain elevation raster (GeoTIFF) used for slope computation and fire propagation.\n\n"
            "Sources:\n"
            "• Direct URL: https://example.com/dem_10m.tif\n"
            "• S3 URI: s3://bucket/project/dem.tif\n"
            "• Layer reference: 'Venice DEM' (from Layer Registry)"
        ),
        examples=[
            "https://example.com/dem_10m.tif",
            "s3://bucket/project/dem.tif",
            "Venice DEM",
        ],
        validation_alias=AliasChoices("dem", "dtm", "elevation", "dem_path"),
    )

    ignitions: str = Field(
        ...,
        title="Ignition Sources",
        description=(
            "Vector file (GeoJSON, GPKG, Shapefile) or raster defining the fire ignition points/areas.\n\n"
            "Sources:\n"
            "• Direct URL: https://example.com/ignitions.geojson\n"
            "• S3 URI: s3://bucket/project/ignitions.geojson\n"
            "• Layer reference: 'Ignition Points' (from Layer Registry)"
        ),
        examples=[
            "https://example.com/data/ignitions.geojson",
            "s3://bucket/project/ignitions.geojson",
            "Ignition Points",
        ],
        validation_alias=AliasChoices("ignitions", "ignition", "fire_start", "ignition_points"),
    )

    # ============================================================================
    # Wind (simple scalar inputs — most common use case)
    # ============================================================================

    wind_speed: float = Field(
        ...,
        title="Wind Speed (m/s)",
        description=(
            "Constant wind speed in meters per second for the entire simulation.\n"
            "Typical values: 1–5 m/s (calm), 5–10 m/s (moderate), 10+ m/s (strong)."
        ),
        examples=[5.0, 8.0, 12.0],
        validation_alias=AliasChoices("wind_speed", "wind_velocity", "windspeed"),
    )

    wind_direction: float = Field(
        ...,
        title="Wind Direction (degrees meteorological)",
        description=(
            "Constant wind direction in meteorological degrees (0=N, 90=E, 180=S, 270=W).\n"
            "The wind blows FROM this direction."
        ),
        examples=[225.0, 180.0, 45.0],
        validation_alias=AliasChoices("wind_direction", "wind_dir", "winddir"),
    )

    # ============================================================================
    # Spatial Scope
    # ============================================================================

    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Simulation Bounding Box",
        description=(
            "Geographic extent in EPSG:4326 using named keys west, south, east, north.\n"
            "If omitted, the full extent of the DEM is used.\n"
            "Tip: restrict to the area of interest to speed up computation."
        ),
        examples=[{"west": 12.31, "south": 45.42, "east": 12.44, "north": 45.52}],
        validation_alias=AliasChoices("bbox", "aoi", "extent", "bounds", "bounding_box"),
    )

    # ============================================================================
    # Land Use
    # ============================================================================

    landuse: Optional[str] = Field(
        default=None,
        title="Land Use Raster (mutually exclusive with `landuse_provider`)",
        description=(
            "Land use raster path used to derive the fuel map.\n\n"
            "Sources:\n"
            "• Direct URL: https://example.com/landuse.tif\n"
            "• S3 URI: s3://bucket/project/landuse.tif\n"
            "• Layer reference: 'Land Use 2024' (from Layer Registry)\n\n"
            "Mutually exclusive with `landuse_provider`. "
            "If neither is provided, a default fuel map is used."
        ),
        examples=[
            "https://example.com/landuse_2024.tif",
            "s3://bucket/project/landuse.tif",
            "Land Use 2024",
        ],
        validation_alias=AliasChoices("landuse", "land_use", "landuse_path", "lu"),
    )

    landuse_provider: Optional[str] = Field(
        default=None,
        title="Land Use Provider (mutually exclusive with `landuse`)",
        description=(
            "Provider identifier to automatically fetch land use data when no `landuse` file is given.\n\n"
            "Mutually exclusive with `landuse`.\n\n"
            "Available providers:\n"
            "• `RER/LANDUSE` — Emilia-Romagna region, Italy\n"
            "• `ESA/LANDUSE/V100` — ESA WorldCover (global)\n"
            "• `CUSTOM/LANDUSE/FBVI` — Custom fuel-based vegetation index\n"
            "• `CUSTOM/LANDUSE/RER/AIB` — RER AIB specific dataset"
        ),
        examples=["ESA/LANDUSE/V100", "RER/LANDUSE"],
        validation_alias=AliasChoices("landuse_provider", "land_use_provider", "lup", "fuel_provider"),
    )

    # ============================================================================
    # Simulation Parameters
    # ============================================================================

    start_datetime: Optional[str] = Field(
        default=None,
        title="Simulation Start Date/Time (ISO 8601)",
        description=(
            "Start date and time of the simulation in ISO 8601 format.\n"
            "If omitted, the current system time is used.\n"
            "Example: '2025-10-01T00:00:00Z'"
        ),
        examples=["2025-10-01T00:00:00Z", "2025-08-15T14:30:00Z"],
        validation_alias=AliasChoices("start_datetime", "start_time", "start_dt"),
    )

    time_max: Optional[int] = Field(
        default=DEFAULT_TIME_MAX,
        title="Maximum Simulation Duration (seconds)",
        description=(
            "Maximum fire propagation duration in seconds.\n"
            f"Default: {DEFAULT_TIME_MAX}s (1 hour). "
            "Use larger values for longer-running simulations (e.g., 7200 = 2h, 21600 = 6h)."
        ),
        examples=[3600, 7200, 21600],
        validation_alias=AliasChoices("time_max", "max_time", "duration", "simulation_duration"),
    )

    time_step_interval: Optional[int] = Field(
        default=DEFAULT_TIME_STEP_INTERVAL,
        title="Output Time Step Interval (seconds)",
        description=(
            "Interval between saved output snapshots in seconds.\n"
            f"Default: {DEFAULT_TIME_STEP_INTERVAL}s (5 minutes). "
            "Smaller values produce more output frames."
        ),
        examples=[300, 600, 1800],
        validation_alias=AliasChoices("time_step_interval", "output_interval", "step_interval"),
    )

    moisture: Optional[float] = Field(
        default=DEFAULT_MOISTURE,
        title="Fuel Moisture Content [0–1]",
        description=(
            "Constant fuel moisture content as a fraction in [0, 1].\n"
            f"Default: {DEFAULT_MOISTURE} (15%). "
            "Higher values reduce fire spread (wet fuel). "
            "Typical range: 0.05 (very dry) to 0.40 (wet)."
        ),
        examples=[0.05, 0.15, 0.25],
        validation_alias=AliasChoices("moisture", "fuel_moisture", "humidity"),
    )

    # ============================================================================
    # Output
    # ============================================================================

    t_srs: Optional[str] = Field(
        default=None,
        title="Target Spatial Reference System",
        description=(
            "Target CRS for all output rasters (e.g., 'EPSG:32633').\n"
            "If None, the DEM's native CRS is used."
        ),
        examples=["EPSG:4326", "EPSG:32633", "EPSG:3857"],
        validation_alias=AliasChoices("t_srs", "target_srs", "crs", "out_crs", "srs"),
    )

    out: Optional[str] = Field(
        default=None,
        title="Output Path",
        description=(
            "Destination URL or S3 URI for simulation output.\n"
            "If omitted, an S3 path is auto-generated."
        ),
        examples=[
            "s3://bucket/project/fire-simulation/",
            "https://example.com/results/fire_out/",
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
# SaferFire Tool
# ============================================================================

class SaferFireTool(BaseTool):
    """
    Tool for simulating wildland fire propagation using safer-fire.

    Features:
      • Fire spread simulation using DEM terrain and ignition sources
      • Constant scalar wind input (direction + speed)
      • Optional land use for fuel derivation (file or provider)
      • Configurable simulation duration and output time-step interval
      • Constant fuel moisture content
      • AWS-backed execution via SaferPlaces API

    Example use cases:
      • "Simulate wildfire spread from ignition points with 10 m/s southerly wind"
      • "Run fire propagation for 6 hours using DEM and ESA land use"
      • "Model fire extent given 15% fuel moisture and westerly wind at 8 m/s"
    """

    short_description: ClassVar[str] = (
        "Runs wildland fire propagation simulations and outputs fire spread rasters. "
        "Key params: dem (required, terrain elevation raster or layer reference), "
        "ignitions (required, ignition points/areas as vector file or layer reference), "
        "wind_speed (required, constant m/s), wind_direction (required, meteorological degrees, 0=N), "
        "time_max (simulation duration in seconds, default 3600), "
        "moisture (fuel moisture [0–1], default 0.15), "
        "landuse/landuse_provider (optional, for fuel mapping). "
        "Ideal when simulating fire spread extent and progression over terrain."
    )

    _graph_state: Optional[MABaseGraphState] = PrivateAttr(default=None)

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the SaferFire Tool."""
        super().__init__(
            name=N.SAFER_FIRE_TOOL,
            description=(
                "Simulate wildland fire propagation using the safer-fire model.\n\n"
                "Usage:\n"
                "• Specify DEM (terrain elevation raster)\n"
                "• Provide ignition sources (vector file or layer reference)\n"
                "• Set constant wind speed (m/s) and direction (meteorological degrees)\n"
                "• Optionally provide land use for fuel mapping, simulation duration, moisture\n\n"
                "Output: Fire spread rasters (burned area, fire arrival time) at selected time steps"
            ),
            args_schema=SaferFireInputSchema,
            **kwargs
        )

    def _set_graph_state(self, graph_state: MABaseGraphState) -> None:
        """Set the graph state for the tool."""
        self._graph_state = graph_state

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        return {
            'wind_speed': [
                self._validate_wind_speed,
            ],
            'wind_direction': [
                self._validate_wind_direction,
            ],
            'moisture': [
                self._validate_moisture,
            ],
            'time_max': [
                self._validate_time_max,
            ],
            'landuse': [
                self._validate_landuse_exclusivity,
            ],
        }

    @staticmethod
    def _validate_wind_speed(wind_speed: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate wind speed is non-negative."""
        if wind_speed is not None and wind_speed < 0:
            return f"wind_speed must be >= 0 m/s, got {wind_speed}"
        return None

    @staticmethod
    def _validate_wind_direction(wind_direction: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate wind direction is in [0, 360]."""
        if wind_direction is not None and not (WIND_DIRECTION_MIN <= wind_direction <= WIND_DIRECTION_MAX):
            return (
                f"wind_direction must be between {WIND_DIRECTION_MIN} and {WIND_DIRECTION_MAX} degrees "
                f"(meteorological), got {wind_direction}"
            )
        return None

    @staticmethod
    def _validate_moisture(moisture: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate fuel moisture content is in [0, 1]."""
        if moisture is not None and not (MOISTURE_MIN <= moisture <= MOISTURE_MAX):
            return f"moisture must be between {MOISTURE_MIN} and {MOISTURE_MAX}, got {moisture}"
        return None

    @staticmethod
    def _validate_time_max(time_max: Optional[int] = None, **kwargs) -> Optional[str]:
        """Validate simulation duration is positive."""
        if time_max is not None and time_max <= 0:
            return f"time_max must be > 0 seconds, got {time_max}"
        return None

    @staticmethod
    def _validate_landuse_exclusivity(
        landuse: Optional[str] = None,
        landuse_provider: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """Validate that landuse and landuse_provider are not both set."""
        if landuse is not None and landuse_provider is not None:
            return (
                "landuse and landuse_provider are mutually exclusive. "
                "Provide either a landuse file/layer or a provider name, not both."
            )
        return None

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""

        def infer_landuse_provider(**kwargs: Any) -> str:
            """Infer default landuse provider."""
            return DEFAULT_LANDUSE_PROVIDER if kwargs.get('landuse') is None else kwargs['landuse_provider']

        def infer_start_datetime(**kwargs: Any) -> str:
            """Infer default start datetime for the simulation."""
            return datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None).isoformat()
        
        def infer_time_max(**kwargs: Any) -> int:
            """Infer default maximum simulation time."""
            return DEFAULT_TIME_MAX if kwargs.get('time_max') is None else kwargs['time_max']
        
        def infer_time_step_interval(**kwargs: Any) -> int:
            """Infer default time step interval for the simulation."""
            return DEFAULT_TIME_STEP_INTERVAL if kwargs.get('time_step_interval') is None else kwargs['time_step_interval']

        def infer_out(**kwargs: Any) -> str:
            """Infer default S3 output path for fire simulation results."""
            state = kwargs.pop('_graph_state', None)
            run_id = utils.random_id8()
            return f"{s3_utils._STATE_BUCKET_(state)}/saferfire-out/{run_id}/"

        def infer_t_srs(**kwargs: Any) -> str:
            """Default to EPSG:3857 for maplibre compatibility."""
            return DEFAULT_T_SRS

        return {
            'landuse_provider': infer_landuse_provider,
            'start_datetime': infer_start_datetime,
            'time_max': infer_time_max,
            'time_step_interval': infer_time_step_interval,
            'out': infer_out,
            't_srs': infer_t_srs,
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the SaferFire simulation tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        payload = self._build_api_payload(kwargs)
        api_response = self._call_saferfire_api(payload)
        return self._process_api_response(payload, api_response)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""
        bbox_data = kwargs.get('bbox')
        if bbox_data is not None:
            if hasattr(bbox_data, 'to_list'):
                bbox_value = bbox_data.to_list()
            elif isinstance(bbox_data, dict):
                bbox_value = [bbox_data['west'], bbox_data['south'], bbox_data['east'], bbox_data['north']]
            else:
                bbox_value = bbox_data
        else:
            bbox_value = None

        tool_args = {
            'dem': kwargs['dem'],
            'ignitions': kwargs['ignitions'],
            'wind_speed': kwargs['wind_speed'],
            'wind_direction': kwargs['wind_direction'],
            'bbox': bbox_value,
            'landuse': kwargs.get('landuse'),
            'landuse_provider': kwargs.get('landuse_provider'),
            'start_datetime': kwargs.get('start_datetime'),
            'time_max': kwargs.get('time_max', DEFAULT_TIME_MAX),
            'time_step_interval': kwargs.get('time_step_interval', DEFAULT_TIME_STEP_INTERVAL),
            'moisture': kwargs.get('moisture', DEFAULT_MOISTURE),
            'out': kwargs.get('out'),
            't_srs': kwargs.get('t_srs'),
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

    def _call_saferfire_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the SaferFire execution API.

        Args:
            payload: Request payload

        Returns:
            API response object
        """
        api_url = self._get_api_url()

        print(f"Calling SaferFire API at {api_url} with payload:\n", json.dumps(payload, indent=2))

        return requests.post(api_url, json=payload)

    @staticmethod
    def _get_api_url() -> str:
        """Get SaferFire API URL from environment."""
        api_root = os.getenv('SAFERPLACES_API_ROOT', 'http://localhost:5000')
        return f"{api_root}/processes/safer-fire-process/execution"

    def _process_api_response(
        self,
        req_payload: Dict[str, Any],
        api_response: Any,
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            req_payload: Original request payload
            api_response: Response from SaferFire API

        Returns:
            Formatted tool response
        """
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"SaferFire API request failed: {api_response.status_code} - {api_response.text}",
            }

        response_data = api_response.json()

        if 'files' not in response_data:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected SaferFire API response format: {response_data}",
            }

        tool_output = {
            'status': STATUS_SUCCESS,
            'tool_output': self._format_tool_output(req_payload, response_data),
        }

        print('SaferFire tool output:\n', tool_output)
        return tool_output

    def _format_tool_output(
        self,
        req_payload: Dict[str, Any],
        response_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the tool output."""
        
        output_description = (
            "Fire propagation simulation completed. "
            "Here are the generated fire spread outputs with their S3 URIs and metadata."
        )

        formatted_layers: Dict[str, Any] = {}

        fire_probability_src = [uri for uri in response_data['files'] if uri.endswith('fire_probability.tif')]
        fire_probability_src = fire_probability_src[0] if len(fire_probability_src) > 0 else None
        if fire_probability_src is not None:
            formatted_fire_probability_layer = {
                'variable': 'fire-probability',
                'source': fire_probability_src,
                'metadata': {
                    'surface_type': 'fire-probability',
                    **utils.raster_specs(fire_probability_src),
                },
            }
            formatted_layers.append(formatted_fire_probability_layer)

        fire_probabilities_src = [uri for uri in response_data['files'] if uri.endswith('fire_probabilities.tif')]
        fire_probabilities_src = fire_probabilities_src[0] if len(fire_probabilities_src) > 0 else None
        if fire_probabilities_src is not None:
            formatted_fire_probabilities_layer = {
                'variable': 'fire-probabilities',
                'source': fire_probabilities_src,
                'metadata': {
                    'surface_type': 'fire-probabilities',
                    **utils.raster_ts_specs(fire_probabilities_src),
                },
            }
            formatted_layers.append(formatted_fire_probabilities_layer)

        return {
            'description': output_description,
            'data': formatted_layers
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
