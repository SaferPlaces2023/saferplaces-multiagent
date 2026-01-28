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
from ....nodes.base import base_models, BaseAgentTool, BaseToolInterrupt


# Define supported variables based on _consts.py
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

class MeteoblueRetrieverSchema(BaseModel):
    """
    Schema for the Meteoblue Retriever Tool.

    This schema defines the parameters required to retrieve meteorological data
    from Meteoblue, a global provider of high-resolution weather data. The schema
    supports specifying spatial, temporal, and output-related parameters.

    Attributes:
        variable (Variable): The meteorological variable to retrieve, such as
            precipitation, temperature, or wind direction.
        bbox (Optional[dict]): A bounding box defining the geographic extent in EPSG:4326
            with keys: west, south, east, north.
        lat_range (Optional[List[float]]): Latitude range as [lat_min, lat_max] in EPSG:4326.
            Used as a fallback if `bbox` is not provided.
        long_range (Optional[List[float]]): Longitude range as [long_min, long_max] in EPSG:4326.
            Used as a fallback if `bbox` is not provided.
        time_start (Optional[str]): The start time for the data retrieval in ISO8601 format.
        time_end (Optional[str]): The end time for the data retrieval in ISO8601 format.
        time_range (Optional[List[str]]): Time range as [start, end] in ISO8601 format.
            Used as a fallback if `time_start` and `time_end` are not provided.
        out_format (Optional[str]): The desired output format (default: "tif").
        out (Optional[str]): The file path for saving the output. If not provided,
            data will be stored in a temporary directory.
        bucket_source (Optional[str]): The S3 bucket URI where NetCDF files are stored.
        bucket_destination (Optional[str]): The S3 bucket URI where output will be stored.
        debug (Optional[bool]): Enable debug mode for additional logging.
    """

    variable: Variable = Field(
        ..., title="Variable",
        description="The variable to retrieve. Possible values are defined in the Meteoblue constants.",
        examples=["PRECIPITATION"]
    )
    bbox: Optional[base_models.BBox] = Field(
        default=None,
        title="Bounding Box",
        description="Geographic extent in EPSG:4326 as named keys: west,south,east,north.",
        examples=[{"west": 7.0, "south": 45.0, "east": 7.1, "north": 45.1}]
    )
    lat_range: Optional[List[float]] = Field(
        default=None,
        title="Latitude Range",
        description="Latitude range as [lat_min, lat_max] in EPSG:4326. Prefer `bbox`.",
        examples=[[45.0, 45.1]]
    )
    long_range: Optional[List[float]] = Field(
        default=None,
        title="Longitude Range",
        description="Longitude range as [long_min, long_max] in EPSG:4326. Prefer `bbox`.",
        examples=[[7.0, 7.1]]
    )
    time_start: Optional[str] = Field(
        default=None,
        title="Start Time (ISO8601)",
        description="Forecast start time in ISO8601, e.g., 2026-01-27T00:00:00Z.",
        examples=["2026-01-27T00:00:00Z"]
    )
    time_end: Optional[str] = Field(
        default=None,
        title="End Time (ISO8601)",
        description="Forecast end time in ISO8601.",
        examples=["2026-01-28T00:00:00Z"]
    )
    time_range: Optional[List[str]] = Field(
        default=None,
        title="Time Range",
        description="Time range as [start, end] in ISO8601. Prefer `time_start` and `time_end`.",
        examples=[["2026-01-27T00:00:00", "2026-01-28T00:00:00"]]
    )
    out_format: Optional[str] = Field(
        "tif", title="Output Format",
        description="Output format (default: tif).",
        examples=["tif"]
    )
    out: Optional[str] = Field(
        default=None,
        title="Output File Path",
        description="The output file path. If not provided, data will be stored in a temporary directory.",
        examples=["/tmp/output.tif"]
    )
    bucket_source: Optional[str] = Field(
        default=None,
        title="Bucket Source",
        description="S3 bucket source URI where NetCDF files are stored.",
        examples=["s3://source-bucket/path"]
    )
    bucket_destination: Optional[str] = Field(
        default=None,
        title="Bucket Destination",
        description="S3 bucket destination URI where output will be stored.",
        examples=["s3://destination-bucket/path"]
    )

    @model_validator(mode="after")
    def _normalize_and_validate(self):
        """
        Normalize and validate spatial and temporal parameters.
        """
        # --- bbox fallback from lat/long ranges ---
        if self.bbox is None and self.lat_range and self.long_range:
            if len(self.lat_range) == 2 and len(self.long_range) == 2:
                lat_min, lat_max = self.lat_range
                lon_min, lon_max = self.long_range
                self.bbox = base_models.BBox(west=lon_min, south=lat_min, east=lon_max, north=lat_max)

        # require at least some spatial constraint (optional: puoi renderlo obbligatorio)
        if self.bbox is None:
            # raise ValueError("Provide `bbox` or both `lat_range` and `long_range`.")
            raise BaseToolInterrupt(
                interrupt_tool = N.METEOBLUE_RETRIEVER_TOOL,
                interrupt_type = BaseToolInterrupt.BaseToolInterruptType.PROVIDE_ARGS,
                interrupt_reason = f"Missing required arguments: [bbox].",
                interrupt_data = {
                    "missing_args": ["bbox"],
                    "args_schema": type(self).model_fields
                }
            )

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
            if horizon > 24*15:
                raise ValueError("`time_end` cannot exceed 15 days ahead from now.")

        return self

class MeteoblueRetrieverTool(BaseAgentTool):
    """
    Tool for retrieving data from the Meteoblue API.

    This tool interacts with the Meteoblue API, a global provider of high-resolution
    weather data, to retrieve meteorological information for a specified geographic
    area and time range. The tool supports various meteorological variables and
    provides options for output storage.

    Key Features:
        - Retrieve data for variables such as precipitation, temperature, and wind direction.
        - Define the area of interest using a bounding box or latitude/longitude ranges.
        - Specify the time range for data retrieval in ISO8601 format.
        - Save results locally or to an AWS S3 bucket.

    Example Use Cases:
        - "Retrieve precipitation data for northern Italy for the next 24 hours."
        - "Fetch temperature and wind data for a specific region and save the results to S3."

    Output:
        - If `bucket_destination` or `out` is provided, data is saved to the specified location.
        - If neither is provided, the tool returns the data directly as a JSON object.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(
            name=N.METEOBLUE_RETRIEVER_TOOL,
            description=(
                "This tool retrieves high-resolution meteorological data from the Meteoblue API, "
                "a global provider of weather information. It is designed to handle requests "
                "involving specific weather variables (e.g., precipitation, temperature, wind) "
                "for a defined geographic area and time range. Users can specify the area using "
                "bounding boxes or latitude/longitude ranges, and the time range in ISO8601 format. "
                "The tool supports saving results locally or to an AWS S3 bucket."
                "\n\n"
                "Ideal for tasks such as:\n"
                "- Retrieving precipitation forecasts for a specific region.\n"
                "- Fetching temperature and wind data for a given time period.\n"
                "- Analyzing weather patterns for urban planning or disaster management.\n"
                "\n\n"
                "If unsure whether this tool fits your request, consider if your query involves "
                "weather data retrieval for a specific location and time."
            ),
            args_schema=MeteoblueRetrieverSchema,
            **kwargs
        )
        self.execution_confirmed = False
        self.output_confirmed = True

    def _set_args_validation_rules(self) -> dict:
        """
        Define validation rules for the tool arguments.
        """
        # # TODO: | Validate time range  according meteoblue API docs
        # return {
        #     'bbox': [
        #         lambda **ka: f"BBox is not defined properly."
        #             if (ka.get('bbox') is None) or (ka.get('lat_range') is None and ka.get('long_range') is None) else None,
        #     ],
        #     'time_end': [
        #         lambda **ka: f"Invalid time range: time_end must be greater than time_start."
        #             if ka.get('time_start') is not None and ka.get('time_end') is not None and ka['time_end'] <= ka['time_start'] else None,
        #     ]
        # }
        return dict()

    def _set_args_inference_rules(self) -> dict:
        """
        Define inference rules for the tool arguments.
        """

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
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/meteoblue-out"
                  
        infer_rules = {
            'time_range': infer_time_range,
            'time_start': infer_time_start,
            'time_end': infer_time_end,
            'bucket_source': infer_bucket_source,
            'bucket_destination': infer_bucket_destination,
        }
        return infer_rules

    def _execute(self, **kwargs: Any):
        """
        Execute the tool logic.
        """
        # DOC: Call the SaferBuildings API ...
        api_url = f"{os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')}/processes/meteoblue-retriever-process/execution"
        
        kwargs = {
            'location_name': utils.random_id8(),
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

        # print(f'\n\n\n {payload} \n\n\n')
        # raise Exception('Debug stop')

        api_response = requests.post(api_url, json=payload)
        
        # DOC: If the api call fails, return an error response
        if api_response.status_code != 200:
            tool_response = {
                'tool_response': {
                    'error': f"Failed to execute Meteoblue Retriever API: {api_response.status_code} - {api_response.text}"
                }
            }
            
        # DOC: If the API call is successful, process the response 
        api_response = api_response.json()

        # DOC: Example response
        # {
        #     "status": "OK",
        #     "collected_data_info": [
        #         {
        #             "variable": "precipitation",
        #             "ref": "s3://saferplaces.co/SaferPlaces-Agent/dev/user=tommaso/project=au-000/meteoblue-out/Meteoblue__dmIrO2la__precipitation__2026-02-04T00:00:00.tif"
        #         }
        #     ]
        # }

        if api_response.get('status', None) == 'OK':
            tool_response = {
                'tool_response': api_response,
                'updates': {
                    'layer_registry': self.graph_state.get('layer_registry', []) + [
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, f"Meteoblue_{payload['inputs']['variable']}"),
                            'description': f"Meteoblue {payload['inputs']['variable']} data for bbox {[kwargs['long_range'][0], kwargs['lat_range'][0], kwargs['long_range'][1], kwargs['lat_range'][1]]} from {payload['inputs']['time_range'][0]} to {payload['inputs']['time_range'][1]}",
                            'src': collected_data['ref'],
                            'type': 'raster',
                            'metadata': {
                                'surface_type': f"rain-timeseries", # !!!: to be refined based on collected_data['variable'] >>> we need a mapping multi-provider-variable → surface-type
                                ** utils.raster_ts_specs(collected_data['ref']),
                            }
                        }
                        for collected_data in api_response.get('collected_data_info', [])
                        if not GraphStates.src_layer_exists(self.graph_state, collected_data['ref'])
                    ]
                    if len(api_response.get('collected_data_info', [])) > 0
                    else []
                }
            }    
            
        # DOC: If the API call is successful but the response is not as expected, return an error response
        else:
            tool_response = {
                'tool_response': {
                    'error': f"Unexpected response from Meteoblue Retriever API: {api_response}"
                }
            }
            
        # DOC: If there is an error in the tool response, update the messages to guide agent's next steps
        if 'error' in tool_response['tool_response']:
            tool_response['updates'] = {
                'messages': [ SystemMessage(content="An error occurred while executing the Meteoblue Retriever tool. Explain the error to the user and then ask him if he wants to retry or not.") ],
            }
        
        return tool_response

    def _on_tool_end(self):
        """
        Cleanup or finalize after tool execution.
        """
        self.execution_confirmed = False
        self.output_confirmed = True

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