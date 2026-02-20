import os
import datetime
from dateutil import relativedelta
from enum import Enum
import requests

from typing import Optional, Union, List, Dict, Any, Literal
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

from langchain_core.messages import SystemMessage
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from ....common import utils, s3_utils
from ....common import states as GraphStates
from ....common import names as N
from ....common import base_models


from typing import Optional, List, Literal
from pydantic import BaseModel, Field, model_validator
from datetime import timezone

from langchain_core.tools import BaseTool

from . import _validators as validators
from . import _inferrers as inferrers


# ---- Product enum (allowed values) ----
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
    "CAPPI1", "CAPPI2", "CAPPI3", "CAPPI4", "CAPPI5", "CAPPI6", "CAPPI7", "CAPPI8"  # CAPPI reflectivity at fixed altitude (1–8 km) – ~10min
]
DPCProductCodeValues = list(DPCProductCode.__args__)

DPCBoundingBox = {'west': 4.5233915, 'south': 35.0650858, 'east': 20.4766085, 'north': 47.8489892}  # DOC: DPC data bbox → (west, south, east, north) for Italy in EPSG:4326


# ---- Main schema ----
class DPCRetrieverSchema(BaseModel):
    """
    Retrieve meteorological products from the Italian Civil Protection Department (DPC).

    This tool downloads a wide variety of **real-time and historical meteorological datasets**
    from the DPC API for a **specific geographic area** and **time window**.

    **Preferred parameters for the LLM:**
    - `bbox` (west, south, east, north) in EPSG:4326 to define the geographic extent.
    - `time_start` and `time_end` in ISO8601 format to specify the time range.

    **Fallback parameters (for compatibility):**
    - `lat_range` + `long_range` as lists `[min, max]` for latitude and longitude.
    - `time_range` as a list `[start, end]` in ISO8601.

    The `product` field determines **which dataset** to retrieve, such as precipitation,
    reflectivity, lightning strikes, or cloud cover.
    """

    # ---- WHAT to retrieve ----
    product: DPCProductCode = Field(
        ...,
        title="DPC Product",
        description=(
            "The code of the DPC dataset to retrieve. Examples:\n"
            "- `SRI`: Surface Rainfall Intensity (mm/h)\n"
            "- `VMI`: Vertical Maximum Intensity (max reflectivity, dBZ)\n"
            "- `SRT1/3/6/12/24`: Cumulative precipitation over different time intervals\n"
            "- `IR108`: Cloud cover from MSG IR 10.8 satellite imagery\n"
            "- `TEMP`: Interpolated temperature map from ground stations\n"
            "- `LTG`: Lightning strike frequency map\n"
            "- `AMV`: Upper-level wind vectors (Atmospheric Motion Vectors)\n"
            "- `HRD`: Heavy Rain Detection (multi-sensor severe rainfall index)\n"
            "- `RADAR_STATUS`: Status of radar network sites\n"
            "- `CAPPI1..8`: Reflectivity at fixed altitudes from 1 to 8 km"
        ),
        examples=["SRI"],
    )

    # ---- WHERE (preferred) ----
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description=f"Geographic extent in EPSG:4326 with named keys: west, south, east, north. Full coverage (Italy) is given by {DPCBoundingBox}.",
        examples=[{"west": 10.0, "south": 44.0, "east": 12.0, "north": 46.0}],
    )

    # ---- WHERE (fallback) ----
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range (fallback)",
        description="Latitude range as [lat_min, lat_max] in EPSG:4326. Prefer using `bbox`.",
        examples=[[44.0, 46.0]],
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range (fallback)",
        description="Longitude range as [lon_min, lon_max] in EPSG:4326. Prefer using `bbox`.",
        examples=[[10.0, 12.0]],
    )

    # ---- WHEN (preferred) ----
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time (ISO8601)",
        description="Start timestamp for the data query in ISO8601 format, e.g., 2025-09-18T00:00:00Z.",
        examples=["2025-09-18T00:00:00Z"],
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time (ISO8601)",
        description="End timestamp for the data query in ISO8601 format. Must be greater than `time_start`.",
        examples=["2025-09-18T06:00:00Z"],
    )

    # ---- WHEN (fallback) ----
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range (fallback)",
        description="Time range as [start, end] in ISO8601 format. Prefer using `time_start` and `time_end`.",
        examples=[["2025-09-18T00:00:00Z", "2025-09-18T06:00:00Z"]],
    )

    # ---- OUTPUT ----
    out: Optional[str] = Field(
        default=None,
        title="Local Output Path",
        description="Local file path where the retrieved data will be saved.",
        examples=["/tmp/dpc/result.geojson"],
    )

    out_format: Optional[Literal["geojson", "dataframe"]] = Field(
        default=None,
        title="Return Format",
        description=(
            'Format of the returned data when not saving to a file. '
            'Allowed values: `"geojson"` or `"dataframe"`. Default is `"geojson"`.'
        ),
        examples=["geojson"],
    )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="Destination S3 Bucket (Optional)",
        description=(
            "AWS S3 bucket where the data will be stored. Format: `s3://bucket/path`.\n\n"
            "If neither `out` nor `bucket_destination` are provided, the output will "
            "be returned in-memory in the format specified by `out_format` "
            "(default: GeoJSON FeatureCollection)."
        ),
        examples=["s3://my-dest/dpc/results"],
    )

    debug: Optional[bool] = Field(
        default=False,
        title="Debug Mode",
        description="Enable verbose logging and diagnostics for troubleshooting.",
        examples=[True],
    )


class DPCRetrieverTool(BaseTool):
    """
    Tool for retrieving meteorological products from the Italian Civil Protection Department (DPC).

    This tool allows you to **download and query real-time and historical meteorological datasets** 
    provided by the DPC. It is designed for use cases such as severe weather monitoring, 
    precipitation tracking, and environmental analysis.

    Supported features:
      - Select a specific **DPC product** such as rainfall intensity (SRI), reflectivity (VMI), 
        cumulative precipitation (SRT1/3/6/12/24), cloud cover (IR108), temperature (TEMP),
        lightning strikes (LTG), upper-level wind vectors (AMV), or radar network status.
      - Define the **geographic area** using a bounding box (`bbox`) in EPSG:4326.
      - Specify the **time window** with `time_start` and `time_end` (ISO8601).
      - Save retrieved data **locally** or **upload directly to AWS S3**.
      - Return data in multiple formats (`geojson` or `dataframe`).

    Example use cases:
      - "Retrieve rainfall intensity for northern Italy in the last 6 hours."
      - "Get cloud cover and lightning strike data for a specific area and save to S3."
      - "Download cumulative precipitation over 24 hours for monitoring flood risks."

    Output behavior:
      - If `bucket_destination` or `out` is provided → data will be saved to that location.
      - If neither is provided → data is returned **in-memory** in the format specified by `out_format` (default: GeoJSON FeatureCollection).
    """

    def __init__(self, **kwargs: Any):
        """
        Initialize the DPCRetrieverTool.

        Args:
            **kwargs**: Additional keyword arguments forwarded to BaseAgentTool.
        """
        super().__init__(
            name=N.DPC_RETRIEVER_TOOL,
            description=(
                "Use this tool to **retrieve meteorological datasets** from the Italian Civil Protection Department (DPC).\n\n"
                "Typical usage:\n"
                "- Specify `product` (e.g., SRI for rainfall intensity, VMI for reflectivity, IR108 for cloud cover).\n"
                "- Define the target **area** with a `bbox` (west, south, east, north) in EPSG:4326.\n"
                "- Set the **time window** using `time_start` and `time_end` (ISO8601).\n"
                "- Optionally provide `bucket_destination` or `out` to save results, "
                "or return them directly in memory as GeoJSON or DataFrame.\n\n"
                "Ideal for scenarios involving precipitation analysis, storm tracking, "
                "radar network monitoring, and other real-time weather-related tasks."
            ),
            args_schema=DPCRetrieverSchema,
            **kwargs
        )

    def _set_args_validation_rules(self) -> dict:
        """Validation rules for tool arguments."""
        now = datetime.datetime.now(tz=timezone.utc)
        
        return {
            'product': [validators.value_in_list('product', DPCProductCodeValues)],
            'bbox': [validators.bbox_inside('bbox', DPCBoundingBox)],
            'time_start': [
                validators.time_within_days('time_start', 7),
                validators.time_before('time_start', now),
            ],
            'time_end': [
                validators.time_within_days('time_end', 7),
                validators.time_after('time_end', 'time_start'),
                validators.time_before('time_end', now),
            ],
        }
    

    def _set_args_inference_rules(self) -> dict:
        """Inference rules for tool arguments."""
        DPC_DELAY_MINUTES = 10  # DPC data has 10-minute delay
        
        def infer_bucket_destination(**kwargs):
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/dpc-out"
        
        return {
            'time_range': inferrers.infer_time_range(
                default_hours_back=1,
                delay_minutes=DPC_DELAY_MINUTES
            ),
            'time_start': inferrers.infer_time_start(
                default_hours_back=1,
                delay_minutes=DPC_DELAY_MINUTES
            ),
            'time_end': inferrers.infer_time_end(
                delay_minutes=DPC_DELAY_MINUTES
            ),
            'bucket_destination': infer_bucket_destination,
        }
    

    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file
    def _execute(
        self,
        /,
        **kwargs: Any
    ): 
        # DOC: Call the SaferBuildings API ...
        api_url = f"{os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')}/processes/dpc-retriever-process/execution"
        
        kwargs = {
            'product': kwargs['product'],
            # 'lat_range': kwargs['bbox']['lat_range'],
            # 'long_range': kwargs['bbox']['long_range'],
            'time_range': [
                # datetime.datetime.fromisoformat(kwargs['time_start']).replace(tzinfo=None).isoformat(),
                # datetime.datetime.fromisoformat(kwargs['time_end']).replace(tzinfo=None).isoformat(),
            ],
            # 'bucket_destination': kwargs['bucket_destination']
        }
        
        credentials_args = {
            'token': os.getenv("SAFERCAST_API_TOKEN"),
        }
        
        debug_args = {
            'debug': kwargs.get('debug', True),     # TODO: use a global _is_debug_mode() to set this
        }
        
        payload = {
            'inputs': {
                **kwargs,                   # DOC: Unpack the tool arguments
                **credentials_args,         # DOC: Add credentials
                **debug_args                # DOC: Add debug mode
            }
        }
        
        # DOC: Call the DPC-Retriever API
        api_response = requests.post(api_url, json=payload)
        
        # TEST: Simulate successfull response
        # class ApiResponse200:
        #     status_code = 200
        #     def json(self):
        #         return {
        #             "id": "safer-rain-process",
        #             "water_depth_file": "s3://saferplaces.co/packages/safer_rain/Rimini/cesenatico-small-safer-rain-water.tif"
        #        } 
        # api_response = ApiResponse200()
        
        if api_response.status_code != 200:
            return {
                'status': 'error',
                'message': f"Failed to execute DPC Retriever API: {api_response.status_code}"
            }
            
        api_response = api_response.json()
        
        if 'uri' not in api_response:
            return {
                'status': 'error',
                'message': f"Unexpected response from DPC Retriever API: {api_response}"
            }
            
        return {
            'status': 'success',
            'tool_output': {
                'data': api_response,
                'description': f"DPC {kwargs['product']} data.",
            }
        }
        
        
        # REF: [↓↓↓ OLD ↓↓↓]
        
        # DOC: If the api call fails, return an error response
        if api_response.status_code != 200:
            tool_response = {
                'tool_response': {
                    'error': f"Failed to execute DPC Retriever API: {api_response.status_code} - {api_response.text}"
                }
            }
            
        # DOC: If the API call is successful, process the response 
        api_response = api_response.json()
        if 'uri' in api_response:
            tool_response = {
                'tool_response': api_response,
                'updates': {
                    'layer_registry': self.graph_state.get('layer_registry', []) + [
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, f"DPC_{payload['inputs']['product']}"),
                            'description': f"DPC {payload['inputs']['product']} data for bbox {[kwargs['long_range'][0], kwargs['lat_range'][0], kwargs['long_range'][1], kwargs['lat_range'][1]]} from {payload['inputs']['time_range'][0]} to {payload['inputs']['time_range'][1]}",
                            'src': api_response['uri'],
                            'type': 'raster',
                            'metadata': {
                                'surface_type': 'rain-timeseries',  # !!!: to be refined based on variable >>> we need a mapping multi-provider-variable → surface-type
                                ** utils.raster_ts_specs(api_response['uri']),
                            }
                        }
                    ]
                    if not GraphStates.src_layer_exists(self.graph_state, api_response['uri'])
                    else []
                }
            }    
            
        # DOC: If the API call is successful but the response is not as expected, return an error response
        else:
            tool_response = {
                'tool_response': {
                    'error': f"Unexpected response from DPC Retriever API: {api_response}"
                }
            }
            
        # DOC: If there is an error in the tool response, update the messages to guide agent's next steps
        if 'error' in tool_response['tool_response']:
            tool_response['updates'] = {
                'messages': [ SystemMessage(content="An error occurred while executing the DPC Retriever tool. Explain the error to the user and then ask him if he wants to retry or not.") ],
            }
        
        return tool_response
        
    
    # DOC: Try running AgentTool → Will check required, validity and inference over arguments thatn call and return _execute()
    def _run(
        self, 
        /,
        **kwargs: Any, # dict[str, Any] = None,
    ) -> dict:
        
        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop("run_manager", None)
        return super()._run(
            tool_args = kwargs,
            run_manager = run_manager
        )