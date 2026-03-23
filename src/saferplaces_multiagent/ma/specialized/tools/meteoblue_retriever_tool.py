import os
import json
import requests
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, Field
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
STATUS_OK = "OK"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


METEOBLUE_VARIABLE_TO_SURFACE_TYPE = {
    'PRECIPITATION': 'rain-timeseries',
    'TEMPERATURE': 'temperature-timeseries',
    # Add more mappings as needed
}

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
    bbox: base_models.BBox = Field(
        ...,
        title="Area of Interest (bbox)",
        description=(
            "Geographic extent in EPSG:4326 using named keys west, south, east, north. "
            "It defines the Area of Interest (AOI) for the Digital Twin. "
            "Example: {'west': 9.05, 'south': 45.42, 'east': 9.25, 'north': 45.55}"
        ),
        examples=[
            {"west": 9.05, "south": 45.42, "east": 9.25, "north": 45.55},
        ],
        validation_alias=AliasChoices("bbox", "aoi", "extent", "bounds", "bounding_box"),
    )

    # # Geographic extent (fallback)
    # lat_range: Optional[List[float]] = Field(
    #     default=None,
    #     title="Latitude Range",
    #     description="Latitude range [min, max] in EPSG:4326. Prefer using bbox.",
    #     examples=[[45.0, 45.1]]
    # )
    # long_range: Optional[List[float]] = Field(
    #     default=None,
    #     title="Longitude Range",
    #     description="Longitude range [min, max] in EPSG:4326. Prefer using bbox.",
    #     examples=[[7.0, 7.1]]
    # )

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

    # # Time window (fallback)
    # time_range: Optional[List[str]] = Field(
    #     default=None,
    #     title="Time Range",
    #     description="Time range [start, end] in ISO8601. Prefer using time_start/time_end.",
    #     examples=[["2026-02-18T00:00:00Z", "2026-02-19T00:00:00Z"]]
    # )

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

    short_description: ClassVar[str] = (
        "Retrieves global weather forecast data (raster GeoTIFF) from Meteoblue, up to 14 days ahead. "
        "Key params: variable (required, e.g. PRECIPITATION=mm, TEMPERATURE=°C, WINDSPEED=m/s, "
        "WINDDIRECTION=degrees, RELATIVEHUMIDITY=%, PRECIPITATION_PROBABILITY=%, SNOWFRACTION, FELTTEMPERATURE, "
        "SEALEVELPRESSURE=hPa, UVINDEX, PICTOCODE), "
        "bbox (EPSG:4326 bounding box), "
        "time_start/time_end (ISO8601; must be future timestamps within 14-day forecast window). "
        "Global coverage."
    )

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

        # def infer_time_range(**kwargs: Any) -> str:
        #     time_range = kwargs.get('time_range')
        #     time_start = kwargs.get('time_start')
        #     time_end = kwargs.get('time_end')
        #     if time_range is None:
        #         if time_start is not None and time_end is not None:
        #             time_range = [time_start, time_end]
        #         else:
        #             time_range = inferrers.infer_time_range(
        #                 default_hours_back=-METEOBLUE_DEFAULT_FORECAST_HOURS,  # Negative = future
        #                 delay_minutes=0
        #             )(**kwargs)
        #     return time_range
            
        def infer_bucket_source(**kwargs: Any) -> str:
            """Infer default S3 source bucket."""
            state = kwargs.pop('_graph_state', None)
            return kwargs.get('bucket_source') or f"{s3_utils._STATE_BUCKET_(state)}/meteoblue-in"
        
        def infer_bucket_destination(**kwargs: Any) -> str:
            """Infer default S3 destination bucket."""
            state = kwargs.pop('_graph_state', None)
            return f"{s3_utils._STATE_BUCKET_(state)}/meteoblue-out"
        
        return {
            # 'time_range': infer_time_range,
            'time_start': inferrers.infer_time_start(
                default_hours_back=-METEOBLUE_DEFAULT_FORECAST_HOURS,
                delay_minutes=0
            ),
            'time_end': inferrers.infer_time_end(
                delay_minutes=-60
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
        req_payload = self._build_api_payload(kwargs)

        # Call Meteoblue API
        api_response = self._call_meteoblue_api(req_payload)

        # Process response
        return self._process_api_response(req_payload, api_response)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""

        bbox_data = kwargs['bbox']
        if hasattr(bbox_data, 'to_list'):
            bbox_list = bbox_data.to_list()
        elif isinstance(bbox_data, dict):
            bbox_list = list(bbox_data.values())
        else:
            bbox_list = bbox_data
        long_range = [bbox_list[0], bbox_list[2]]
        lat_range = [bbox_list[1], bbox_list[3]]
        
        time_start = inferrers.to_iso_naive(kwargs['time_start'])
        time_end = inferrers.to_iso_naive(kwargs['time_end'])

        tool_args = {
            'location_name': utils.random_id8(),
            'variable': kwargs['variable'],
            'lat_range': lat_range,
            'long_range': long_range,
            'time_range': [time_start, time_end],
            'bucket_source': kwargs['bucket_source'],
            'bucket_destination': kwargs['bucket_destination']
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

        print(f"Calling Meteoblue API at {api_url} with payload:\n", json.dumps(payload, indent=2))
        
        return requests.post(api_url, json=payload)

        # Temporary mock response for testing
        return self._mock_api_response()

    @staticmethod
    def _get_api_url() -> str:
        """Get Meteoblue Retriever API URL from environment."""
        api_root = os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5001')
        return f"{api_root}/processes/meteoblue-retriever-process/execution"

    @staticmethod
    def _mock_api_response() -> Any:
        """Mock API response for testing purposes."""
        class MockResponse:
            status_code = 200

            def json(self) -> Dict[str, Any]:
                # DOC: Example output structure
                return {
                    "status": "OK",
                    "collected_data_info": [
                        {
                            "variable": "precipitation",
                            "ref": "s3://saferplaces.co/packages/process-meteoblue-hub/retriever//Meteoblue__Piemonte2__precipitation__2026-03-21T00:00:00.tif"
                        },
                        {
                            "variable": "temperature",
                            "ref": "s3://saferplaces.co/packages/process-meteoblue-hub/retriever//Meteoblue__Piemonte2__temperature__2026-03-21T00:00:00.tif"
                        }
                    ]
                }

        return MockResponse()

    def _process_api_response(
        self, 
        req_payload: Dict[str, Any],
        api_response: Any
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            req_payload: Original request payload
            api_response: Response from Meteoblue API

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
        print(f"Meteoblue API response:\n", json.dumps(response_data, indent=2))
        
        # Validate response structure
        if response_data.get('status') != STATUS_OK:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected API response format: {response_data}"
            }

        # Return success response
        tool_output = {
            'status': STATUS_SUCCESS,
            'tool_output': self._format_tool_output(req_payload, response_data)
        }

        print('Meteoblue tool output: \n', tool_output)

        return tool_output
    

    def _surface_type_from_variable(self, variable: str) -> str:
        """
        Map a variable name to a well-known standard surface type.
        """
        return METEOBLUE_VARIABLE_TO_SURFACE_TYPE.get(variable, variable)


    def _format_tool_output(
            self,
            req_payload: Dict[str, Any],
            response_data: Dict[str, Any]
        ) -> Dict[str, Any]:
        """
        Generate tool output from API response data.
        """

        output_description = "Meteoblue forecast data retrieved successfully. Here is the collected layers data with their references and metadata:"

        def format_item(item: Dict[str, Any]) -> str:
            item_variable = item['variable']
            item_ref = item['ref']
            item_metadata = {
                'surface_type': self._surface_type_from_variable(item_variable),
                ** utils.raster_ts_specs(item_ref, timestamps_attr='band_names'),
            }
            return {
                'variable': item_variable,
                'source': item_ref,
                'metadata': item_metadata 
            }
        
        return {
            'description': output_description,
            'data': [
                format_item(item)
                for item in response_data.get('collected_data_info', [])
            ]
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