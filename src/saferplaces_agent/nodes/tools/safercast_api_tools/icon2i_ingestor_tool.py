import os
import datetime
from dateutil import relativedelta
from enum import Enum
import requests

from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from ....common import utils, s3_utils
from ....common import states as GraphStates
from ....common import names as N
from ....nodes.base import base_models, BaseAgentTool


class ICON2IIngestorSchema(BaseModel):

    """
    Schema for ingesting ICON-2I data.
    This schema defines the parameters required to ingest and store data from the ICON-2I API.
    It includes fields for specifying the variable, latitude, forecast, and bucket destination.
    """

    variable: str = Field(
        ...,
        title="Variable",
        description='The variable to retrieve. Allowed values are "dewpoint_temperature", "pressure_reduced_to_msl", "snow_depth_water_equivalent", "temperature", "temperature_g", "total_cloud_cover", "total_precipitation", "u_wind_component", "v_wind_component".',
        example="total_precipitation",
        validation_alias="variable"
    )

    forecast_run: Optional[Union[str, List[str]]] = Field(
        default=None,
        title="Forecast Run",
        description=(
            "Optional ICON-2I forecast run. "
            "If not provided, all available forecast runs from the current date will be considered. "
            "Must be an ISO 8601 date string with hour **00:00:00** or **12:00:00**, "
            "and at least two days in the past. "
            "You can provide a single string or a list of strings."
        ),
        example=["2024-09-15T00:00:00", "2024-09-15T12:00:00"],
        validation_alias="forecast_run"
    )

    # ???: This is not used by agent
    # out_dir: Optional[str] = Field(
    #     default=None,
    #     title="Output Directory",
    #     description=(
    #         "The local directory where the retrieved data will be stored. "
    #         "If not provided, the data will not be saved to disk."
    #     ),
    #     example="/path/to/output",
    #     validation_alias="out_dir"
    # )

    bucket_destination: Optional[str] = Field(
        default=None,
        title="Bucket Destination",
        description=(
            "The cloud bucket where the data will be saved. "
            "If not provided, the data will not be stored in a bucket. "
            "If **neither out_dir nor bucket_destination** are provided, "
            "the output will be returned as a tif file."
        ),
        example="s3://my-bucket/folder",
        validation_alias="bucket_destination"
    )


class ICON2IIngestorTool(BaseAgentTool):
    """
    Tool for ingesting ICON-2I data from the API.
    
    This tool retrieves and stores data from the ICON-2I API based on the specified parameters.
    It supports retrieving specific variables, forecast runs, and storing the data in a local directory or cloud bucket.
    """

    def __init__(self, **kwargs: Any):
        """
        Initialize the ICON2IIngestorTool with the provided parameters.
        
        Args:
            **kwargs: Additional keyword arguments to pass to the BaseAgentTool.
        """
        super().__init__(
            name=N.ICON2I_INGESTOR_TOOL,
            description=(
                "Ingests data from the ICON-2I API based on the specified parameters. "
                "Supports retrieving specific variables and forecast runs, "
                "and storing the data in a local directory or cloud bucket."
            ),
            args_schema=ICON2IIngestorSchema,
            **kwargs
        )
        self.execution_confirmed = False
        self.output_confirmed = True 


    # DOC: Validation rules ( i.e.: valid init and lead time ... ) 
    def _set_args_validation_rules(self) -> dict:
        # TODO: | forecast run in last 3-4 days | variable in allowed list |
        return dict()
    

    # DOC: Inference rules ( i.e.: from location name to bbox ... )
    def _set_args_inference_rules(self) -> dict:
        
        def infer_forecast_run(**kwargs):
            forecast_run = kwargs.get('forecast_run', None)
            if forecast_run is None:
                # DOC: default to last 12 o'clock hour from today
                today = datetime.datetime.now(tz=datetime.timezone.utc)
                forecast_run = today.replace(hour=12 if today.hour >= 12 else 0, minute=0, second=0, microsecond=0).date.isoformat()
            return forecast_run
                  
        infer_rules = {
            'forecast_run': infer_forecast_run,
        }
        return infer_rules
    

    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file
    def _execute(
        self,
        /,
        **kwargs: Any,  # dict[str, Any] = None,
    ): 
        # DOC: Call the SaferBuildings API ...
        api_url = f"{os.getenv('SAFERCAST_API_ROOT', 'http://localhost:5002')}/processes/icon2i-ingestor-process/execution"
        payload = { 
            "inputs": kwargs | {
                "token": os.getenv("SAFERCAST_API_TOKEN"),
                "user": os.getenv("SAFERCAST_API_USER"),
            } | {
                "bucket_destination": f"{s3_utils._STATE_BUCKET_(self.graph_state)}/icon2i-out"
                # FIXME: "bucket_destination": f"{s3_utils._BASE_BUCKET}/icon2i-out"
            } | {
                "debug": True,  # TEST: enable debug mode
            }
        }
        print(f"Executing {self.name} with args: {payload}")
        response = requests.post(api_url, json=payload)
        print(f"Response status code: {response.status_code} - {response.content}")
        response = response.json() 
        # TODO: Check output_code ...

        # TEST: Simulate a response for testing purposes
        # api_response = {}
        api_response = response

        # TODO: Check if the response is valid
        
        tool_response = {
            'tool_response': api_response,
        }
        
        print('\n', '-'*80, '\n')
        print('tool_response:', tool_response)
        print('\n', '-'*80, '\n')
        
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