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

from ....common import base_models, utils, s3_utils
from ....common import states as GraphStates
from ....common import names as N
from ....nodes.base import BaseAgentTool



# opzionale: enum per evitare variabili non supportate
Variable = Literal[
    'temperature',
    'dewpoint_temperature',
    'u_wind_component',
    'v_wind_component',
    'total_cloud_cover',
    'temperature_g',
    'snow_depth_water_equivalent',
    'pressure_reduced_to_msl',
    'total_precipitation'
]


class ICON2IRetrieverSchema(BaseModel):
    """
    Retrieve forecast data from the ICON-2I model for a given area and time window.

    ✅ Preferiti dall'agente:
      - Usa `bbox` con chiavi nominative (west,south,east,north).
      - Usa `time_start` e `time_end` in ISO8601.

    🔁 Supportati come alternativa (alias/fallback):
      - `lat_range` + `long_range` come liste [min, max].
      - `time_range` come lista [start, end] in ISO8601.

    Limite: l'orizzonte di previsione non può superare **72 ore avanti** dal momento corrente.
    Coordinate: **EPSG:4326 (WGS84)**.
    """

    variable: Variable = Field(
        ...,
        title="Forecast Variable",
        description="Meteorological variable to retrieve from ICON-2I. If not specified, use 'total_precipitation'.",
        examples=["total_precipitation"],
    )

    # ✅ Preferito: bbox nominato
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description="Geographic extent in EPSG:4326 as named keys: west,south,east,north.",
        examples=[{"west": 10.0, "south": 44.0, "east": 12.0, "north": 46.0}],
    )

    # 🔁 Fallback/compat: range lat/lon come liste [min,max]
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range (fallback)",
        description="Latitude range as [lat_min, lat_max] in EPSG:4326. Prefer `bbox`.",
        examples=[[44.0, 46.0]],
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range (fallback)",
        description="Longitude range as [lon_min, lon_max] in EPSG:4326. Prefer `bbox`.",
        examples=[[10.0, 12.0]],
    )

    # ✅ Preferiti: time_start / time_end
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time (ISO8601)",
        description="Forecast start time in ISO8601, e.g., 2025-09-18T00:00:00Z.",
        examples=["2025-09-18T00:00:00Z"],
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time (ISO8601)",
        description="Forecast end time in ISO8601 (≤ now+72h).",
        examples=["2025-09-19T00:00:00Z"],
    )

    # 🔁 Fallback/compat: lista [start, end]
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range (fallback)",
        description="Time range as [start, end] in ISO8601. Prefer `time_start` and `time_end`.",
        examples=[["2025-09-18T00:00:00Z", "2025-09-19T00:00:00Z"]],
    )

    out: Optional[str] = Field(
        default=None,
        title="Local Output Directory",
        description="Local folder where data will be saved. If omitted, data is returned in-memory.",
        examples=["/data/icon2i"],
    )

    bucket_source: Optional[str] = Field(
        default=None,
        title="Source S3 Bucket (Optional)",
        description="AWS S3 bucket to read from instead of querying ICON-2I. Format: s3://bucket/path",
        examples=["s3://my-source/icon2i"],
    )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="Destination S3 Bucket (Optional)",
        description=(
            "AWS S3 bucket where to store results. Format: s3://bucket/path. "
            "If neither `out` nor `bucket_destination` are provided, output is returned as a FeatureCollection."
        ),
        examples=["s3://my-dest/icon2i/results"],
    )

    @model_validator(mode="after")
    def _normalize_and_validate(self):
        # --- bbox fallback from lat/long ranges ---
        if self.bbox is None and self.lat_range and self.long_range:
            if len(self.lat_range) == 2 and len(self.long_range) == 2:
                lat_min, lat_max = self.lat_range
                lon_min, lon_max = self.long_range
                self.bbox = base_models.BBox(west=lon_min, south=lat_min, east=lon_max, north=lat_max)

        # require at least some spatial constraint (optional: puoi renderlo obbligatorio)
        if self.bbox is None:
            raise ValueError("Provide `bbox` or both `lat_range` and `long_range`.")

        # --- time fallback from time_range ---
        if (self.time_start is None or self.time_end is None) and self.time_range:
            if len(self.time_range) == 2:
                self.time_start, self.time_end = self.time_range

        # validate time order and horizon
        if self.time_start and self.time_end:
            try:
                # support 'Z' by replacing with +00:00
                ts = self.time_start.replace("Z", "+00:00")
                te = self.time_end.replace("Z", "+00:00")
                dt_start = datetime.datetime.fromisoformat(ts).replace(tzinfo=None)
                dt_end = datetime.datetime.fromisoformat(te).replace(tzinfo=None)
            except Exception as e:
                raise ValueError(f"Invalid ISO8601 in time_start/time_end: {e}")

            if dt_end <= dt_start:
                raise ValueError("`time_end` must be greater than `time_start`.")

            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            max_end = now.replace(microsecond=0)  # normalize
            # consentiamo end nel futuro ma ≤ 72h
            horizon = (dt_end - now).total_seconds() / 3600.0
            if horizon > 72:
                raise ValueError("`time_end` cannot exceed 72 hours ahead from now.")

        return self
    

class ICON2IRetrieverTool(BaseAgentTool):
    """
    Tool for retrieving forecast data from the ICON-2I weather model.

    This tool queries the **ICON-2I numerical weather prediction model** to retrieve 
    forecast data for a specific **geographic area** and **time window**.

    It supports:
      - Selecting a **forecast variable** such as precipitation, temperature, wind, etc.
      - Defining the area of interest using a **bounding box (bbox)** in EPSG:4326 (WGS84).
      - Specifying a **time window** with a start and end time in ISO8601 format.
      - Saving results **locally** or to an **AWS S3 bucket**.

    The forecast horizon cannot exceed **72 hours ahead** from the current time.

    Example use cases:
      - "Get the total precipitation forecast for northern Italy for the next 48 hours."
      - "Retrieve wind speed and direction for a specific area and save the results to S3."

    Output format:
      - If `bucket_destination` or `out` is provided → data is saved to that location.
      - If neither is provided → output is returned as a **GeoJSON FeatureCollection**.
    """

    def __init__(self, **kwargs: Any):
        """
        Initialize the ICON2IRetrieverTool.

        Args:
            **kwargs: Additional keyword arguments forwarded to BaseAgentTool.
        """
        super().__init__(
            name=N.ICON2I_RETRIEVER_TOOL,
            description=(
                "Use this tool to **retrieve weather forecast data** from the ICON-2I model. "
                "It is designed for tasks involving meteorological variables such as "
                "precipitation, temperature, cloud cover, wind components, and more.\n\n"
                "Provide:\n"
                "- `variable`: the forecast variable to retrieve. If not specified, defaults to `total_precipitation`.\n"
                "- `bbox`: bounding box for the area of interest in EPSG:4326.\n"
                "- `time_start` and `time_end`: ISO8601 timestamps (≤ 72 hours ahead).\n"
                "- Optional `bucket_destination` or `out` to save the data.\n\n"
                "If no storage location is provided, the tool returns the forecast data "
                "directly as a GeoJSON FeatureCollection."
            ),
            args_schema=ICON2IRetrieverSchema,
            **kwargs
        )
        self.execution_confirmed = False
        self.output_confirmed = True


    # DOC: Validation rules ( i.e.: valid init and lead time ... ) 
    def _set_args_validation_rules(self) -> dict:
        # TODO: | Validate time range  according icon2i API docs
        return dict()
    

    # DOC: Inference rules ( i.e.: from location name to bbox ... )
    def _set_args_inference_rules(self) -> dict:                  
        def infer_time_range(**kwargs):
            if kwargs.get('time_start', None) is not None and kwargs.get('time_end', None) is not None:
                # DOC: both time_start and time_end are provided, no inference needed
                return None
            time_range = kwargs.get('time_range', None)
            now = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None)
            if time_range is None:
                # DOC: default next hour range
                now = datetime.datetime.now(tz=datetime.timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
                time_range = [
                    now.replace(minute=0, second=0),
                    now.replace(minute=0, second=0) + relativedelta.relativedelta(hours=1)
                ]
            else:
                time_range = [datetime.datetime.fromisoformat(t).replace(tzinfo=None) for t in time_range]
            return [ time_range[0].replace(tzinfo=None).isoformat(), time_range[1].replace(tzinfo=None).isoformat() ]
        
        def infer_time_start(**kwargs):
            time_start = kwargs.get('time_start', None)
            now = datetime.datetime.now(tz=datetime .timezone.utc).replace(tzinfo=None)
            if time_start is None:
                # DOC: infer from time_range or default to current hour
                time_start = kwargs.get('time_range', [None,None])[0] or now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
            else:
                time_start = datetime.datetime.fromisoformat(time_start).replace(tzinfo=None)
            return time_start.isoformat()
        
        def infer_time_end(**kwargs):
            time_end = kwargs.get('time_end', None)
            now = datetime.datetime.now(tz=datetime.timezone.utc).replace(tzinfo=None)
            if time_end is None:
                # DOC: infer from time_range or next hour
                time_end = kwargs.get('time_range', [None,None])[1] or now.replace(hour=now.hour+1, minute=0, second=0, microsecond=0, tzinfo=None)
            else:
                time_end = datetime.datetime.fromisoformat(time_end).replace(tzinfo=None)
            return time_end.isoformat()
        
        def infer_bucket_source(**kwargs):
            """
            Infer the S3 bucket source based on user ID and project ID.
            """
            return kwargs.get('bucket_source', infer_bucket_destination(**kwargs))

        def infer_bucket_destination(**kwargs):
            """
            Infer the S3 bucket destination based on user ID and project ID.
            """
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/icon2i-out"
                  
        infer_rules = {
            'time_range': infer_time_range,
            'time_start': infer_time_start,
            'time_end': infer_time_end,
            'bucket_source': infer_bucket_source,
            'bucket_destination': infer_bucket_destination,
        }
        return infer_rules
    

    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file
    def _execute(
        self,
        /,
        **kwargs: Any,  # dict[str, Any] = None,
    ): 
        # DOC: Call the SaferBuildings API ...
        api_url = f"{os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')}/processes/icon2i-retriever-process/execution"
        
        kwargs = {
            'variable': kwargs['variable'],
            'lat_range': kwargs['bbox'].lat_range(),
            'long_range': kwargs['bbox'].long_range(),
            'time_range': [
                datetime.datetime.fromisoformat(kwargs['time_start']).replace(tzinfo=None).isoformat(),
                datetime.datetime.fromisoformat(kwargs['time_end']).replace(tzinfo=None).isoformat(),
            ],
            'bucket_source': kwargs['bucket_destination'],
            'bucket_destination': kwargs['bucket_destination']
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
        
        # DOC: If the api call fails, return an error response
        if api_response.status_code != 200:
            tool_response = {
                'tool_response': {
                    'error': f"Failed to execute ICON2I Retriever API: {api_response.status_code} - {api_response.text}"
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
                            'title': GraphStates.new_layer_title(self.graph_state, f"ICON2I_{payload['inputs']['variable']}"),
                            'description': f"ICON2I {payload['inputs']['variable']} data for bbox {[kwargs['long_range'][0], kwargs['lat_range'][0], kwargs['long_range'][1], kwargs['lat_range'][1]]} from {payload['inputs']['time_range'][0]} to {payload['inputs']['time_range'][1]}",
                            'src': api_response['uri'],
                            'type': 'raster',
                            'metadata': {
                                'surface_type': 'rain-timeseries', # !!!: to be refined based on variable >>> we need a mapping multi-provider-variable → surface-type
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
                    'error': f"Unexpected response from ICON2I Retriever API: {api_response}"
                }
            }
            
        # DOC: If there is an error in the tool response, update the messages to guide agent's next steps
        if 'error' in tool_response['tool_response']:
            tool_response['updates'] = {
                'messages': [ SystemMessage(content="An error occurred while executing the ICON2I Retriever tool. Explain the error to the user and then ask him if he wants to retry or not.") ],
            }
        
        return tool_response

    
    # DOC: Back to a consisent state
    def _on_tool_end(self):
        self.execution_confirmed = False
        self.output_confirmed = True
        
    
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