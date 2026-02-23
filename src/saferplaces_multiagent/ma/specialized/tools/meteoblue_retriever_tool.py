import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models
from . import _validators as validators
from . import _inferrers as inferrers


# ============================================================================
# Constants
# ============================================================================

# Meteoblue meteorological variables
MeteoblueVariable = Literal[
    "SNOWFRACTION",                 # Snow fraction of precipitation
    "WINDSPEED",                    # Wind speed (m/s)
    "TEMPERATURE",                  # Air temperature (°C)
    "PRECIPITATION_PROBABILITY",    # Probability of precipitation (%)
    "CONVECTIVE_PRECIPITATION",     # Convective precipitation amount
    "RAINSPOT",                     # Rain spot indicator
    "PICTOCODE",                    # Weather pictogram code
    "FELTTEMPERATURE",              # Apparent/felt temperature (°C)
    "PRECIPITATION",                # Precipitation amount (mm)
    "ISDAYLIGHT",                   # Daylight indicator
    "UVINDEX",                      # UV index
    "RELATIVEHUMIDITY",             # Relative humidity (%)
    "SEALEVELPRESSURE",             # Sea level pressure (hPa)
    "WINDDIRECTION"                 # Wind direction (degrees)
]

METEOBLUE_VARIABLES = list(MeteoblueVariable.__args__)

# Forecast configuration
METEOBLUE_DEFAULT_FORECAST_HOURS = 24  # Default forecast window
METEOBLUE_MAX_FORECAST_DAYS = 14       # Maximum forecast horizon

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_OK = "OK"


# ============================================================================
# Schema
# ============================================================================

class MeteoblueRetrieverSchema(BaseModel):
    """
    Schema for retrieving meteorological forecast data from Meteoblue.

    This tool retrieves high-resolution weather forecasts from Meteoblue,
    a global provider of meteorological data. It supports various weather variables
    and allows users to specify the geographic area and forecast time window.

    Preferred parameters:
      - `bbox` (west, south, east, north) in EPSG:4326
      - `time_start` and `time_end` in ISO8601 format

    Fallback parameters (for compatibility):
      - `lat_range` + `long_range` as lists [min, max]
      - `time_range` as list [start, end] in ISO8601
    """

    # Variable selection
    variable: MeteoblueVariable = Field(
        ...,
        title="Meteorological Variable",
        description=(
            "The meteorological variable to retrieve. Examples:\n"
            "• PRECIPITATION: Precipitation amount (mm)\n"
            "• TEMPERATURE: Air temperature (°C)\n"
            "• WINDSPEED: Wind speed (m/s)\n"
            "• WINDDIRECTION: Wind direction (degrees)\n"
            "• RELATIVEHUMIDITY: Relative humidity (%)\n"
            "• PRECIPITATION_PROBABILITY: Probability of precipitation (%)\n"
            "• SNOWFRACTION: Snow fraction\n"
            "• FELTTEMPERATURE: Apparent temperature (°C)\n"
            "• SEALEVELPRESSURE: Sea level pressure (hPa)\n"
            "• UVINDEX: UV index\n"
            "• PICTOCODE: Weather pictogram code\n"
            "• ISDAYLIGHT: Daylight indicator"
        ),
        examples=["PRECIPITATION", "TEMPERATURE", "WINDSPEED"]
    )

    # Geographic extent (preferred)
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description="Geographic extent in EPSG:4326 (west, south, east, north).",
        examples=[{"west": 7.0, "south": 45.0, "east": 7.1, "north": 45.1}]
    )

    # Geographic extent (fallback)
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range",
        description="Latitude range [min, max] in EPSG:4326. Prefer using bbox.",
        examples=[[45.0, 45.1]]
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range",
        description="Longitude range [min, max] in EPSG:4326. Prefer using bbox.",
        examples=[[7.0, 7.1]]
    )

    # Time window (preferred)
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time",
        description="Forecast start time in ISO8601 format (e.g., 2026-02-18T00:00:00Z)",
        examples=["2026-02-18T00:00:00Z"]
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time",
        description="Forecast end time in ISO8601 format. Must be after time_start.",
        examples=["2026-02-19T00:00:00Z"]
    )

    # Time window (fallback)
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range",
        description="Time range [start, end] in ISO8601. Prefer using time_start/time_end.",
        examples=[["2026-02-18T00:00:00Z", "2026-02-19T00:00:00Z"]]
    )

    # Output options
    out: Optional[str] = Field(
        default=None,
        title="Local Output Path",
        description="Local file path where retrieved data will be saved.",
        examples=["/tmp/meteoblue/result.tif"]
    )

    out_format: Optional[str] = Field(
        default="tif",
        title="Output Format",
        description="Format for returned data. Default: 'tif'.",
        examples=["tif"]
    )

    bucket_source: Optional[str] = Field(
        default=None,
        title="S3 Source Bucket",
        description="AWS S3 bucket where NetCDF source files are stored.",
        examples=["s3://my-source/meteoblue/"]
    )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="S3 Destination",
        description=(
            "AWS S3 bucket for storing output data (format: s3://bucket/path). "
            "If neither out nor bucket_destination provided, uses default S3 location."
        ),
        examples=["s3://my-dest/meteoblue-out"]
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging for troubleshooting.",
        examples=[True]
    )


# ============================================================================
# Meteoblue Retriever Tool
# ============================================================================

class MeteoblueRetrieverTool(BaseTool):
    """
    Tool for retrieving meteorological forecast data from Meteoblue.

    Features:
      • Download weather forecast data from Meteoblue global provider
      • Support for multiple variables: precipitation, temperature, wind, humidity, etc.
      • Define geographic area with bounding box (EPSG:4326)
      • Specify forecast time window with ISO8601 timestamps
      • Save locally or upload to AWS S3
      • Return data as GeoTIFF or other formats

    Example use cases:
      • "Retrieve precipitation forecast for northern Italy for the next 24 hours"
      • "Get temperature and wind speed data for a specific area"
      • "Download humidity forecast for the next 3 days for disaster planning"
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the Meteoblue Retriever Tool."""
        super().__init__(
            name=N.METEOBLUE_RETRIEVER_TOOL,
            description=(
                "Retrieve meteorological forecast data from Meteoblue.\n\n"
                "Usage:\n"
                "• Specify variable (e.g., PRECIPITATION, TEMPERATURE, WINDSPEED)\n"
                "• Define area with bbox (west, south, east, north) in EPSG:4326\n"
                "• Set forecast window using time_start and time_end (ISO8601)\n"
                "• Optionally save to bucket_destination or out path\n\n"
                "Ideal for weather forecasting, precipitation analysis, temperature monitoring, "
                "and other meteorological tasks."
            ),
            args_schema=MeteoblueRetrieverSchema,
            **kwargs
        )

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        return {
            'variable': [
                validators.value_in_list('variable', METEOBLUE_VARIABLES)
            ],
            'time_start': [
                validators.time_before('time_start', 'time_end'),
                # lambda **kw: None if 'time_start' in kw and validators.parse_dt(kw['time_start']) > datetime.now(tz=timezone.utc).date() else "Time start must be in the future."
                validators.time_after_datetime('time_start', datetime.now(tz=timezone.utc).date())
            ],
            'time_end': [
                validators.time_after('time_end', 'time_start')
            ]
        }

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""
        def infer_bucket_source(**kwargs: Any) -> str:
            """Infer default S3 source bucket."""
            state = kwargs.pop('_graph_state', None)
            return kwargs.get('bucket_source') or f"{s3_utils._STATE_BUCKET_(state)}/meteoblue-in"
        
        def infer_bucket_destination(**kwargs: Any) -> str:
            """Infer default S3 destination bucket."""
            state = kwargs.pop('_graph_state', None)
            return f"{s3_utils._STATE_BUCKET_(state)}/meteoblue-out"
        
        return {
            'time_range': inferrers.infer_time_range(
                default_hours_back=-METEOBLUE_DEFAULT_FORECAST_HOURS,  # Negative = future
                delay_minutes=0
            ),
            'time_start': inferrers.infer_time_start(
                default_hours_back=-METEOBLUE_DEFAULT_FORECAST_HOURS,  # Negative = future
                delay_minutes=0
            ),
            'time_end': inferrers.infer_time_end(
                delay_minutes=0
            ),
            'bucket_source': infer_bucket_source,
            'bucket_destination': infer_bucket_destination
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the Meteoblue retriever tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        # Build API payload
        payload = self._build_api_payload(kwargs)

        # Call Meteoblue API
        api_response = self._call_meteoblue_api(payload)

        # Process response
        return self._process_api_response(api_response, kwargs)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""
        tool_args = {
            'location_name': utils.random_id8(),
            'variable': kwargs['variable'],
            # Note: Uncomment when ready to use actual parameters
            # 'lat_range': kwargs['bbox']['lat_range'],
            # 'long_range': kwargs['bbox']['long_range'],
            # 'time_range': [
            #     datetime.fromisoformat(kwargs['time_start']).replace(tzinfo=None).isoformat(),
            #     datetime.fromisoformat(kwargs['time_end']).replace(tzinfo=None).isoformat(),
            # ],
            # 'bucket_source': kwargs['bucket_source'],
            # 'bucket_destination': kwargs['bucket_destination']
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

    def _call_meteoblue_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the Meteoblue Retriever API.

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
        """Get Meteoblue Retriever API URL from environment."""
        api_root = os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')
        return f"{api_root}/processes/meteoblue-retriever-process/execution"

    @staticmethod
    def _mock_api_response() -> Any:
        """Mock API response for testing purposes."""
        class MockResponse:
            status_code = 200

            def json(self) -> Dict[str, Any]:
                return {
                    'status': STATUS_OK,
                    'collected_data_info': [
                        {
                            'variable': 'PRECIPITATION',
                            'ref': 's3://example-bucket/meteoblue-out/meteoblue-precipitation.tif'
                        }
                    ]
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
            api_response: Response from Meteoblue API
            kwargs: Original tool arguments

        Returns:
            Formatted tool response
        """
        # Check for HTTP errors
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"Meteoblue API request failed: {api_response.status_code}"
            }

        # Parse response JSON
        response_data = api_response.json()

        # Validate response structure
        if response_data.get('status') != STATUS_OK:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected API response format: {response_data}"
            }

        # Return success response
        return {
            'status': STATUS_SUCCESS,
            'tool_output': {
                'data': response_data,
                'description': f"Meteoblue {kwargs['variable']} forecast data retrieved successfully"
            }
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