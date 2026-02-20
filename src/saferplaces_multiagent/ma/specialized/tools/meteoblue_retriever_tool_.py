import os
import datetime
from dateutil import relativedelta

from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, model_validator

from langchain_core.messages import SystemMessage
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool

from ....common import utils, s3_utils
from ....common import states as GraphStates
from ....common import names as N
from ....common import base_models

from . import _validators as validators
from . import _inferrers as inferrers


# ---- Variable enum (allowed values) ----
Variable = Literal[
    "SNOWFRACTION",
    "WINDSPEED",
    "TEMPERATURE",
    "PRECIPITATION_PROBABILITY",
    "CONVECTIVE_PRECIPITATION",
    "RAINSPOT",
    "PICTOCODE",
    "FELTTEMPERATURE",
    "PRECIPITATION",
    "ISDAYLIGHT",
    "UVINDEX",
    "RELATIVEHUMIDITY",
    "SEALEVELPRESSURE",
    "WINDDIRECTION"
]
VariableValues = list(Variable.__args__)


# ---- Main schema ----
class MeteoblueRetrieverSchema(BaseModel):
    """
    Retrieve meteorological forecast data from Meteoblue.

    This tool retrieves high-resolution weather forecasts from Meteoblue,
    a global provider of meteorological data. It supports various weather variables
    and allows users to specify the **geographic area** and **forecast time window**.

    **Preferred parameters:**
    - `bbox` (west, south, east, north) in EPSG:4326 to define the geographic extent.
    - `time_start` and `time_end` in ISO8601 format to specify the forecast period.

    **Fallback parameters (for compatibility):**
    - `lat_range` + `long_range` as lists `[min, max]` for latitude and longitude.
    - `time_range` as a list `[start, end]` in ISO8601.

    The `variable` field determines which meteorological parameter to retrieve
    (e.g., precipitation, temperature, wind).
    """

    # ---- WHAT to retrieve ----
    variable: Variable = Field(
        ...,
        title="Meteorological Variable",
        description=(
            "The meteorological variable to retrieve from Meteoblue. Examples:\n"
            "- `PRECIPITATION`: Precipitation amount (mm)\n"
            "- `TEMPERATURE`: Air temperature (°C)\n"
            "- `WINDSPEED`: Wind speed (m/s)\n"
            "- `WINDDIRECTION`: Wind direction (degrees)\n"
            "- `RELATIVEHUMIDITY`: Relative humidity (%)\n"
            "- `PRECIPITATION_PROBABILITY`: Probability of precipitation (%)\n"
            "- `SNOWFRACTION`: Snow fraction of precipitation\n"
            "- `FELTTEMPERATURE`: Apparent/felt temperature (°C)\n"
            "- `SEALEVELPRESSURE`: Sea level pressure (hPa)\n"
            "- `UVINDEX`: UV index\n"
            "- `PICTOCODE`: Weather pictogram code\n"
            "- `ISDAYLIGHT`: Daylight indicator"
        ),
        examples=["PRECIPITATION"],
    )

    # ---- WHERE (preferred) ----
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description="Geographic extent in EPSG:4326 with named keys: west, south, east, north.",
        examples=[{"west": 7.0, "south": 45.0, "east": 7.1, "north": 45.1}],
    )

    # ---- WHERE (fallback) ----
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range (fallback)",
        description="Latitude range as [lat_min, lat_max] in EPSG:4326. Prefer using `bbox`.",
        examples=[[45.0, 45.1]],
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range (fallback)",
        description="Longitude range as [lon_min, lon_max] in EPSG:4326. Prefer using `bbox`.",
        examples=[[7.0, 7.1]],
    )

    # ---- WHEN (preferred) ----
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time (ISO8601)",
        description="Forecast start time in ISO8601 format, e.g., 2026-02-18T00:00:00Z.",
        examples=["2026-02-18T00:00:00Z"],
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time (ISO8601)",
        description="Forecast end time in ISO8601 format. Must be greater than `time_start`.",
        examples=["2026-02-19T00:00:00Z"],
    )

    # ---- WHEN (fallback) ----
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range (fallback)",
        description="Time range as [start, end] in ISO8601 format. Prefer using `time_start` and `time_end`.",
        examples=[["2026-02-18T00:00:00Z", "2026-02-19T00:00:00Z"]],
    )

    # ---- OUTPUT ----
    out: Optional[str] = Field(
        default=None,
        title="Local Output Path",
        description="Local file path where the retrieved data will be saved.",
        examples=["/tmp/meteoblue/result.tif"],
    )

    out_format: Optional[str] = Field(
        default="tif",
        title="Output Format",
        description="Format of the output file. Default is 'tif'.",
        examples=["tif"],
    )

    bucket_source: Optional[str] = Field(
        default=None,
        title="Source S3 Bucket (Optional)",
        description="AWS S3 bucket where NetCDF source files are stored.",
        examples=["s3://my-source/meteoblue/"],
    )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="Destination S3 Bucket (Optional)",
        description=(
            "AWS S3 bucket where the output data will be stored. Format: `s3://bucket/path`.\n\n"
            "If neither `out` nor `bucket_destination` are provided, the output will "
            "be stored in a default S3 location."
        ),
        examples=["s3://my-dest/meteoblue-out"],
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging and diagnostics for troubleshooting.",
        examples=[True],
    )


class MeteoblueRetrieverTool(BaseTool):
    """
    Tool for retrieving meteorological forecast data from Meteoblue.

    This tool allows you to **download and query weather forecast data** from Meteoblue,
    a global provider of high-resolution meteorological information. It is designed for
    use cases such as weather forecasting, precipitation prediction, and environmental planning.

    Supported features:
      - Select a specific **meteorological variable** such as precipitation, temperature,
        wind speed, humidity, or pressure.
      - Define the **geographic area** using a bounding box (`bbox`) in EPSG:4326.
      - Specify the **forecast time window** with `time_start` and `time_end` (ISO8601).
      - Save retrieved data **locally** or **upload directly to AWS S3**.
      - Return data in various formats (default: GeoTIFF).

    Example use cases:
      - "Retrieve precipitation forecast for northern Italy for the next 24 hours."
      - "Get temperature and wind speed data for a specific area and save to S3."
      - "Download humidity forecast for the next 3 days for disaster planning."

    Output behavior:
      - If `bucket_destination` or `out` is provided → data will be saved to that location.
      - If neither is provided → data is saved to a default S3 bucket.
    """

    def __init__(self, **kwargs: Any):
        """
        Initialize the MeteoblueRetrieverTool.

        Args:
            **kwargs: Additional keyword arguments forwarded to BaseTool.
        """
        super().__init__(
            name=N.METEOBLUE_RETRIEVER_TOOL,
            description=(
                "Use this tool to **retrieve meteorological forecast data** from Meteoblue.\n\n"
                "Typical usage:\n"
                "- Specify `variable` (e.g., PRECIPITATION, TEMPERATURE, WINDSPEED).\n"
                "- Define the target **area** with a `bbox` (west, south, east, north) in EPSG:4326.\n"
                "- Set the **forecast time window** using `time_start` and `time_end` (ISO8601).\n"
                "- Optionally provide `bucket_destination` or `out` to save results.\n\n"
                "Ideal for scenarios involving weather forecasting, precipitation analysis, "
                "temperature monitoring, and other meteorological tasks."
            ),
            args_schema=MeteoblueRetrieverSchema,
            **kwargs
        )

    def _set_args_validation_rules(self) -> dict:
        """Validation rules for tool arguments."""
        return {
            'variable': [validators.value_in_list('variable', VariableValues)],
            'time_end': [validators.time_after('time_end', 'time_start')],
        }

    def _set_args_inference_rules(self) -> dict:
        """Inference rules for tool arguments."""
        
        def infer_bucket_source(**kwargs):
            return kwargs.get('bucket_source') or f"{s3_utils._STATE_BUCKET_(self.graph_state)}/meteoblue-out"
        
        def infer_bucket_destination(**kwargs):
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/meteoblue-out"
        
        return {
            'time_range': inferrers.infer_time_range(
                default_hours_back=-1,  # Negative = future (next hour)
                delay_minutes=0
            ),
            'time_start': inferrers.infer_time_start(
                default_hours_back=-1,  # Negative = future (next hour)
                delay_minutes=0
            ),
            'time_end': inferrers.infer_time_end(
                delay_minutes=0
            ),
            'bucket_source': infer_bucket_source,
            'bucket_destination': infer_bucket_destination,
        }

    def _execute(self, /, **kwargs: Any):
        """Execute the tool logic."""
        api_url = f"{os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')}/processes/meteoblue-retriever-process/execution"
        
        api_kwargs = {
            'location_name': utils.random_id8(),
            'variable': kwargs['variable'],
            # 'lat_range': kwargs['bbox']['lat_range']
            # 'long_range': kwargs['bbox']['long_range'],
            # 'time_range': [
            #     datetime.datetime.fromisoformat(kwargs['time_start']).replace(tzinfo=None).isoformat(),
            #     datetime.datetime.fromisoformat(kwargs['time_end']).replace(tzinfo=None).isoformat(),
            # ],
            # 'bucket_source': kwargs['bucket_source'],
            # 'bucket_destination': kwargs['bucket_destination']
        }
        
        credentials_args = {
            'token': os.getenv("SAFERCAST_API_TOKEN"),
        }
        
        debug_args = {
            'debug': kwargs.get('debug', True),
        }
        
        payload = {
            'inputs': {
                **api_kwargs,
                **credentials_args,
                **debug_args
            }
        }

        # Mock response for development
        class ApiResponse200:
            status_code = 200
            def json(self):
                return {
                    'status': 'OK',
                    'collected_data_info': [
                        {
                            'variable': 'PRECIPITATION',
                            'ref': 's3://example-bucket/meteoblue-out/meteoblue-precipitation.tif'
                        }
                    ]
                }
        
        api_response = ApiResponse200()
        # api_response = requests.post(api_url, json=payload)
        
        if api_response.status_code != 200:
            return {
                'status': 'error',
                'message': f"Failed to execute Meteoblue Retriever API: {api_response.status_code}"
            }
        
        api_response = api_response.json()
        
        if api_response.get('status') != 'OK':
            return {
                'status': 'error',
                'message': f"Unexpected response from Meteoblue Retriever API: {api_response}"
            }
        
        return {
            'status': 'success',
            'tool_output': {
                'data': api_response,
                'description': f"Meteoblue {kwargs['variable']} forecast data.",
            }
        }

    def _run(self, /, **kwargs: Any) -> dict:
        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop("run_manager", None)
        return super()._run(
            tool_args=kwargs,
            run_manager=run_manager
        )
