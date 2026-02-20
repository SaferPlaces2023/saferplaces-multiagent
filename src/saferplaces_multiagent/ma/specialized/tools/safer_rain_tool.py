import os
import datetime
from dateutil import relativedelta
from typing import Optional, Literal, Union, List, Dict, Any

from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

from langchain_core.messages import SystemMessage
from langchain_core.callbacks import CallbackManagerForToolRun
from langchain_core.tools import BaseTool

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models


class SaferRainInputSchema(BaseModel):
    """
    Run a flood simulation using a terrain elevation raster (DEM/DTM) and rainfall input
    (either a single numeric amount applied uniformly or a rainfall raster).
    If the rainfall raster is multiband, bands are interpreted as a time series and
    can be cumulatively summed over a band range.
    """

    # ----------------------------- Required inputs -----------------------------
    dem: str = Field(
        ...,
        title="DEM (GeoTIFF)",
        description=(
            "Digital Elevation Model raster used as ground elevation.\n"
            "- You can pass a direct URL, S3 URI, or local path to a GeoTIFF.\n"
            "- Or you can reference an **existing project raster layer** "
            "from the Layer Registry (e.g., when the user says 'use the DTM of Rome').\n"
            "- When a layer is referenced, use that layer's `src` value."
        ),
        examples=[
            "https://example.com/dem_10m.tif",
            "s3://bucket/project/dtm_rome.tif",
            "Rome DTM"
        ],
        validation_alias=AliasChoices("dem", "dtm", "elevation", "dem_path"),
    )

    rain: Union[str, float] = Field(
        ...,
        title="Rainfall input (raster or constant)",
        description=(
            "Rainfall data for the simulation.\n"
            "- It can be a **numeric value** (uniform rainfall in millimeters applied to the whole DEM extent).\n"
            "- Or a URL, S3 URI, or local path to a rainfall raster (GeoTIFF).\n"
            "- It can be a reference an **existing project raster layer** "
            "from the Layer Registry (e.g., 'use layer rainfall-*').\n"
            "- When a layer is referenced, the tool will internally use that layer's `src` value."
        ),
        examples=[
            25.0,
            "https://example.com/rainfall_2025_05.tif",
            "s3://bucket/project/rainfall_v1.tif",
            "Rainfall V1"
        ],
        validation_alias=AliasChoices("rain", "rainfall", "rain_path", "precip", "precipitation"),
    )

    water: Optional[str] = Field(
        default=None,
        title="Output Water Depth (GeoTIFF, optional)",
        description=(
            f"Destination {base_models._URI_HINT} where the simulated water depth raster (GeoTIFF) will be written. "
            "If omitted, the tool returns the path/URI produced by the execution environment."
        ),
        examples=[
            "https://example.com/outputs/water_depth.tif",
            "s3://my-bucket/floods/wd.tif",
        ],
        validation_alias=AliasChoices("water", "waterdepth", "wd", "water_path"),
    )

    # ------------------------------- Parameters --------------------------------
    band: int = Field(
        default=1,  # ???: Default should be None (ora t leas 1) → FIRST
        title="Rain band start (1-based)",
        description=(
            "For multiband rainfall rasters: index of the first band to use (1-based). "
            "If `rain` is numeric (constant), this is ignored."
        ),
        examples=[1],
        validation_alias=AliasChoices("band", "rain_band", "input_band"),
    )

    to_band: int = Field(
        default=1,  # ???: Default should be None (ora t least -1) → LAST
        title="Rain band end (1-based, inclusive)",
        description=(
            "For multiband rainfall rasters: index of the last band to include (inclusive, 1-based). "
            "If `to_band` > `band`, rainfall is cumulatively summed over bands [band..to_band]. "
            "If `rain` is numeric (constant), this is ignored."
        ),
        examples=[1, 3],
        validation_alias=AliasChoices("to_band", "target_band", "out_band", "end_band"),
    )

    t_srs: Optional[str] = Field(
        default=None,
        title="Target SRS (EPSG)",
        description=(
            "Target spatial reference for outputs (e.g., 'EPSG:32633'). "
            "If None, the DEM CRS is used."
        ),
        examples=["EPSG:32633", "EPSG:4326"],
        validation_alias=AliasChoices("t_srs", "target_srs", "crs", "out_crs"),
    )

    mode: Literal["lambda", "batch"] = Field(
        default="lambda",
        title="Execution mode",
        description='Execution backend: "lambda" for AWS Lambda, "batch" for AWS Batch. Default is "lambda".',
        examples=["batch", "lambda"],
        validation_alias=AliasChoices("mode", "execution_mode", "run_mode"),
    )

class SaferRainTool(BaseTool):
    """Tool to run flood simulations (SaferRain) via an external API.

    This implements the same surface as the original `SaferRainTool` but follows
    the structure/style of the other retriever tools in `ma/specialized/tools`.
    """

    def __init__(self, **kwargs: Any):
        super().__init__(
            name=N.SAFER_RAIN_TOOL,
            description=(
                "Run a flood simulation using a DEM and rainfall input. "
                "Rain can be a constant (mm) or a rainfall raster. Outputs a water-depth raster or URI."
            ),
            args_schema=SaferRainInputSchema,
            **kwargs
        )

    def _set_args_validation_rules(self) -> dict:
        # Keep validation lightweight here; more checks happen at API side.
        return dict()

    def _set_args_inference_rules(self) -> dict:
        def infer_water(**kwargs):
            water = kwargs.get('water') or f"water-depth-{utils.b64uuid()}.tif"
            return f"{s3_utils._STATE_BUCKET_(self.graph_state)}/saferrain-out/{water}"

        def infer_mode(**kwargs):
            return 'lambda'

        return {
            'water': infer_water,
            'mode': infer_mode,
        }

    def _execute(self, /, **kwargs: Any):
        api_url = f"{os.getenv('SAFERPLACES_API_ROOT', 'http://localhost:5000')}/processes/safer-rain-process/execution"

        credentials_args = {
            "user": os.getenv("SAFERPLACES_API_USER"),
            "token": os.getenv("SAFERPLACES_API_TOKEN"),
        }

        debug_args = {"debug": kwargs.get('debug', True)}

        payload = {
            "inputs": {
                **kwargs,
                **credentials_args,
                **debug_args,
            }
        }
        
        # DOC: Call the SaferRain API
        # api_response = requests.post(api_url, json=payload)
        class ApiResponse200:
            status_code = 200
            def json(self):
                return dict(
                    uri = 's3://example-bucket/saferrain-out/water-depth.tif'
                )
                
        api_response = ApiResponse200()
        
        if api_response.status_code != 200:
            return {
                'status': 'error',
                'message': f"Failed to execute SaferRain API: {api_response.status_code}"
            }
            
        api_response = api_response.json()
        
        if 'uri' not in api_response:
            return {
                'status': 'error',
                'message': f"Unexpected response from SaferRain API: {api_response}"
            }
            
        return {
            'status': 'success',
            'tool_output': {
                'data': api_response,
                'description': f"Water depth data.",
            }
        }
        
        
       
    def _run(self, /, **kwargs: Any) -> dict:
        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop('run_manager', None)
        return super()._run(
            tool_args=kwargs,
            run_manager=run_manager
        )
