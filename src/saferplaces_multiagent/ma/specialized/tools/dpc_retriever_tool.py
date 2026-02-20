import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ....common import s3_utils
from ....common import names as N
from ....common import base_models
from . import _validators as validators
from . import _inferrers as inferrers


# ============================================================================
# Constants
# ============================================================================

# DPC Product Codes
DPCProductCode = Literal[
    "VMI",          # Vertical Maximum Intensity (max reflectivity, dBZ) – ~5min
    "SRI",          # Surface Rainfall Intensity (mm/h from radar + rain gauges) – ~5min
    "SRT1",         # Cumulative precipitation over 1 hour – ~1h
    "SRT3",         # Cumulative precipitation over 3 hours – ~1h
    "SRT6",         # Cumulative precipitation over 6 hours – ~1h
    "SRT12",        # Cumulative precipitation over 12 hours – ~1h
    "SRT24",        # Cumulative precipitation over 24 hours – ~1h
    "IR108",        # Cloud cover derived from MSG IR 10.8 channel – ~5min
    "TEMP",         # Temperature map interpolated from ground stations – ~1h
    "LTG",          # Lightning strike map (LAMPINET network) – ~10min
    "AMV",          # Atmospheric Motion Vectors (wind in upper levels, 50x50 km grid) – ~20min
    "HRD",          # Heavy Rain Detection (multi-sensor severe rainfall index) – ~5min
    "RADAR_STATUS", # Radar site status (ON/OFF)
    "CAPPI1", "CAPPI2", "CAPPI3", "CAPPI4", "CAPPI5", 
    "CAPPI6", "CAPPI7", "CAPPI8"  # CAPPI reflectivity at fixed altitude (1–8 km) – ~10min
]

DPC_PRODUCT_CODES = list(DPCProductCode.__args__)

# Geographic coverage for Italy
DPC_ITALY_BBOX = {
    'west': 4.5233915,
    'south': 35.0650858,
    'east': 20.4766085,
    'north': 47.8489892
}

# API configuration
DPC_DATA_DELAY_MINUTES = 10  # DPC data has 10-minute delay

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


# ============================================================================
# Schema
# ============================================================================

class DPCRetrieverSchema(BaseModel):
    """
    Schema for retrieving meteorological products from the Italian Civil Protection Department (DPC).

    This tool downloads real-time and historical meteorological datasets from the DPC API
    for a specific geographic area and time window.

    Preferred parameters:
      - `bbox` (west, south, east, north) in EPSG:4326
      - `time_start` and `time_end` in ISO8601 format

    Fallback parameters (for compatibility):
      - `lat_range` + `long_range` as lists [min, max]
      - `time_range` as list [start, end] in ISO8601
    """

    # Product selection
    product: DPCProductCode = Field(
        ...,
        title="DPC Product Code",
        description=(
            "The code of the DPC dataset to retrieve. Examples:\n"
            "• SRI: Surface Rainfall Intensity (mm/h)\n"
            "• VMI: Vertical Maximum Intensity (max reflectivity, dBZ)\n"
            "• SRT1/3/6/12/24: Cumulative precipitation over time intervals\n"
            "• IR108: Cloud cover from satellite imagery\n"
            "• TEMP: Interpolated temperature map\n"
            "• LTG: Lightning strike frequency\n"
            "• AMV: Upper-level wind vectors\n"
            "• HRD: Heavy Rain Detection index\n"
            "• RADAR_STATUS: Radar network status\n"
            "• CAPPI1..8: Reflectivity at fixed altitudes (1-8 km)"
        ),
        examples=["SRI", "VMI", "SRT24"]
    )

    # Geographic extent (preferred)
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description=f"Geographic extent in EPSG:4326 (west, south, east, north). Default coverage: {DPC_ITALY_BBOX}",
        examples=[{"west": 10.0, "south": 44.0, "east": 12.0, "north": 46.0}]
    )

    # Geographic extent (fallback)
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range",
        description="Latitude range [min, max] in EPSG:4326. Prefer using bbox.",
        examples=[[44.0, 46.0]]
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range",
        description="Longitude range [min, max] in EPSG:4326. Prefer using bbox.",
        examples=[[10.0, 12.0]]
    )

    # Time window (preferred)
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time",
        description="Start timestamp in ISO8601 format (e.g., 2025-09-18T00:00:00Z)",
        examples=["2025-09-18T00:00:00Z"]
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time",
        description="End timestamp in ISO8601 format. Must be after time_start.",
        examples=["2025-09-18T06:00:00Z"]
    )

    # Time window (fallback)
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range",
        description="Time range [start, end] in ISO8601. Prefer using time_start/time_end.",
        examples=[["2025-09-18T00:00:00Z", "2025-09-18T06:00:00Z"]]
    )

    # Output options
    out: Optional[str] = Field(
        default=None,
        title="Local Output Path",
        description="Local file path where retrieved data will be saved.",
        examples=["/tmp/dpc/result.geojson"]
    )

    out_format: Optional[Literal["geojson", "dataframe"]] = Field(
        default=None,
        title="Output Format",
        description="Format for returned data: 'geojson' or 'dataframe'. Default: 'geojson'.",
        examples=["geojson"]
    )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="S3 Destination",
        description=(
            "AWS S3 bucket for storing data (format: s3://bucket/path). "
            "If neither out nor bucket_destination provided, returns in-memory data."
        ),
        examples=["s3://my-dest/dpc/results"]
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging for troubleshooting.",
        examples=[True]
    )


# ============================================================================
# DPC Retriever Tool
# ============================================================================

class DPCRetrieverTool(BaseTool):
    """
    Tool for retrieving meteorological products from the Italian Civil Protection Department (DPC).

    Features:
      • Download real-time and historical meteorological datasets
      • Support for multiple products: rainfall, reflectivity, temperature, lightning, etc.
      • Define geographic area with bounding box (EPSG:4326)
      • Specify time window with ISO8601 timestamps
      • Save locally or upload to AWS S3
      • Return data as GeoJSON or DataFrame

    Example use cases:
      • "Retrieve rainfall intensity for northern Italy in the last 6 hours"
      • "Get cloud cover and lightning data for a specific area"
      • "Download 24-hour cumulative precipitation for flood monitoring"
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the DPC Retriever Tool."""
        super().__init__(
            name=N.DPC_RETRIEVER_TOOL,
            description=(
                "Retrieve meteorological datasets from the Italian Civil Protection Department (DPC).\n\n"
                "Usage:\n"
                "• Specify product (e.g., SRI, VMI, IR108)\n"
                "• Define area with bbox (west, south, east, north) in EPSG:4326\n"
                "• Set time window using time_start and time_end (ISO8601)\n"
                "• Optionally save to bucket_destination or out path\n\n"
                "Ideal for precipitation analysis, storm tracking, radar monitoring, "
                "and real-time weather tasks."
            ),
            args_schema=DPCRetrieverSchema,
            **kwargs
        )

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        now = datetime.now(tz=timezone.utc)
        
        return {
            'product': [
                validators.value_in_list('product', DPC_PRODUCT_CODES)
            ],
            'bbox': [
                validators.bbox_inside('bbox', DPC_ITALY_BBOX)
            ],
            'time_start': [
                validators.time_within_days('time_start', 7),
                validators.time_before_datetime('time_start', now)
            ],
            'time_end': [
                validators.time_within_days('time_end', 7),
                validators.time_after('time_end', 'time_start'),
                validators.time_before_datetime('time_end', now)
            ]
        }

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""
        def infer_bucket_destination(**kwargs: Any) -> str:
            """Infer default S3 bucket destination."""
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/dpc-out"
        
        return {
            'time_range': inferrers.infer_time_range(
                default_hours_back=1,
                delay_minutes=DPC_DATA_DELAY_MINUTES
            ),
            'time_start': inferrers.infer_time_start(
                default_hours_back=1,
                delay_minutes=DPC_DATA_DELAY_MINUTES
            ),
            'time_end': inferrers.infer_time_end(
                delay_minutes=DPC_DATA_DELAY_MINUTES
            ),
            'bucket_destination': infer_bucket_destination
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the DPC retriever tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        # Build API payload
        payload = self._build_api_payload(kwargs)

        # Call DPC API
        api_response = self._call_dpc_api(payload)

        # Process response
        return self._process_api_response(api_response, kwargs)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""
        tool_args = {
            'product': kwargs['product'],
            'time_range': [
                # Note: Uncomment when ready to use actual time parameters
                # datetime.fromisoformat(kwargs['time_start']).replace(tzinfo=None).isoformat(),
                # datetime.fromisoformat(kwargs['time_end']).replace(tzinfo=None).isoformat(),
            ]
        }

        credentials = {
            'token': os.getenv("SAFERCAST_API_TOKEN")
        }

        debug_config = {
            'debug': kwargs.get('debug', True)
        }

        return {
            'inputs': {
                **tool_args,
                **credentials,
                **debug_config
            }
        }

    def _call_dpc_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the DPC Retriever API.

        Args:
            payload: Request payload

        Returns:
            API response object
        """
        api_url = self._get_api_url()

        # TODO: Uncomment when ready for production
        # import requests
        # return requests.post(api_url, json=payload)

        # Temporary mock response for testing
        return self._mock_api_response()

    @staticmethod
    def _get_api_url() -> str:
        """Get DPC Retriever API URL from environment."""
        api_root = os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')
        return f"{api_root}/processes/dpc-retriever-process/execution"

    @staticmethod
    def _mock_api_response() -> Any:
        """Mock API response for testing purposes."""
        class MockResponse:
            status_code = 200

            def json(self) -> Dict[str, str]:
                return {
                    "uri": "s3://saferplaces.co/packages/examples/dpc-rain.tif"
                }

        return MockResponse()

    def _process_api_response(
        self, 
        api_response: Any, 
        kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            api_response: Response from DPC API
            kwargs: Original tool arguments

        Returns:
            Formatted tool response
        """
        # Check for HTTP errors
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"DPC API request failed: {api_response.status_code}"
            }

        # Parse response JSON
        response_data = api_response.json()

        # Validate response structure
        if 'uri' not in response_data:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected API response format: {response_data}"
            }

        # Return success response
        return {
            'status': STATUS_SUCCESS,
            'tool_output': {
                'data': response_data,
                'description': f"DPC {kwargs['product']} data retrieved successfully"
            }
        }

    def _run(
        self,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Run the tool (LangChain BaseTool interface).

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool execution result
        """
        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop("run_manager", None)
        return super()._run(tool_args=kwargs, run_manager=run_manager)