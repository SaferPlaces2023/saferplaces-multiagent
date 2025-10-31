import os
import datetime
from dateutil import relativedelta
from enum import Enum
import requests
import numpy as np

from typing import Optional, Union, List, Dict, Any
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from ....common import utils, s3_utils
from ....common import names as N
from ....common import states as GraphStates
from ....nodes.base import base_models, BaseAgentTool


class DigitalTwinInputSchema(BaseModel):
    """
    Create a geospatial **Digital Twin** for a given Area of Interest (AOI) by assembling:
    - a DEM/DTM raster from the specified elevation dataset,
    - building footprints from the chosen dataset/provider,
    - land-use/land-cover from the chosen dataset.

    The DEM is resampled to the requested `pixelsize` (meters) and all outputs are aligned over the AOI.
    """

    # ----------------------------- Data sources ------------------------------
    dem_dataset: Optional[str] = Field(
        default=None,
        title="DEM/DTM dataset",
        description=(
            "Identifier of the elevation dataset to derive the DTM/DEM (catalog key or provider path). "
            "You may set this explicitly (e.g., 'USGS/3DEP/1M') **or leave it as `None` to let the tool "
            "auto-select the most suitable dataset from the AOI (bbox/place) using region-aware rules**.\n\n"
            "Region-aware hints (preferred sources by AOI):\n"
            "- **Italy** → GECOSISTEMA/ITALY\n"
            "- **Netherlands** → AHN/NETHERLANDS/05M | AHN/NETHERLANDS/5M\n"
            "- **Belgium** → NGI/BELGIUM/5M;  Flanders → VLAANDEREN/FLANDERS/BE/1M;  Wallonia → GEOPORTAIL/WALLONIE/BE/1M\n"
            "- **France** → IGN/RGE_ALTI/1M\n"
            "- **Spain** → IGN/ES/2M\n"
            "- **UK** → UK/LIDAR\n"
            "- **Denmark** → DK-DEM\n"
            "- **Norway** → NO/KARTVERKET\n"
            "- **Switzerland** → SWISSALTI3D/SWISS\n"
            "- **Australia** → AU/GA/AUSTRALIA_5M_DEM | AU/GEOSCIENCE | ELVIS/AUSTRALIA | ICSM.GOV/AUSTRALIA\n"
            "- **New Zealand** → NZ/LINZ\n"
            "- **Canada** → NRCAN/CANADA/2M | NRCAN/CDEM\n"
            "- **USA** → USGS/3DEP/1M | USGS/3DEP/10m | US/NED3 | US/NED10\n"
            "- **Mexico** → MX/LIDAR\n"
            "- **Angola** → ANGOLA/HUAMBO | ANGOLA/KUITO | ANGOLA/LOBITO | AIRBUS/ANGOLA\n"
            "- **Pan-EU / Europe** → COPERNICUS/EUDEM\n"
            "- **Global fallback** → NASA/NASADEM_HGT/001 | NASA/SRTM; **coastal** → DeltaDTM\n\n"
            "Selection rules:\n"
            "1) Prefer the **highest native resolution** covering the AOI; "
            "2) for **coastal AOI**, consider **DeltaDTM**; "
            "3) if no national source fits, use **COPERNICUS/EUDEM** (Europe) or global fallback."
        ),
        examples=["COPERNICUS/EUDEM", "USGS/3DEP/1M", None],
        validation_alias=AliasChoices("dem_dataset", "dem", "dtm", "dem_dataset", "dtm_dataset"),
    )

    building_dataset: str = Field(
        default="OSM/BUILDINGS",
        title="Buildings dataset",
        description="Provider/dataset to fetch building footprints. Default: 'OSM/BUILDINGS'.",
        examples=["OSM/BUILDINGS"],
        validation_alias=AliasChoices("building_dataset", "building_dataset", "buildings_provider"),
    )
    
    
    landuse_dataset: str = Field(
        default="ESA/WorldCover/v100",
        title="Land-use dataset",
        description="Dataset for land-use/land-cover. Default: 'ESA/WorldCover/v100'.",
        examples=["ESA/WorldCover/v100"],
        validation_alias=AliasChoices("dataset_land_use", "land_use", "landcover", "land_cover", "landuse"),
    )

    # ------------------------------ Spatial scope ----------------------------
    bbox: base_models.BBox = Field(
        ...,
        title="Area of Interest (bbox)",
        description=(
            "Geographic extent in EPSG:4326 using named keys west,south,east,north. "
            "It is used to define the Area of Interest (AOI) for the Digital Twin. "
        ),
        examples=[
            {"west": 9.05, "south": 45.42, "east": 9.25, "north": 45.55},
        ],
        validation_alias=AliasChoices("bbox", "aoi", "extent", "bounds", "bounding_box"),
    )

    # ------------------------------ Resolution -------------------------------
    pixelsize: Optional[float] = Field(
        default = None,
        title="DEM pixel size (meters). If not explicitly provided, prefer default value `None` as output resolution will be the native resolution of the DEM dataset.",
        description="Target ground sampling distance (meters) for the DEM/DTM resampling. Must be > 0.",
        examples=[None, 1, 2, 5, 10, 30],
        validation_alias=AliasChoices("pixelsize", "pixel_size", "resolution", "res", "gsd"),
    )


class DigitalTwinTool(BaseAgentTool):

    # DOC: Initialize the tool with a name, description and args_schema
    def __init__(self, **kwargs):
        super().__init__(
            name = N.DIGITAL_TWIN_TOOL,
            description = (
                "Generate a **geospatial Digital Twin** for a given Area of Interest (AOI). "
                
                "### Purpose\n"
                "This tool is typically the **first step** in a workflow. It provides harmonized base layers "
                "that can later be used by other tools, such as flood simulation, building analysis, or land-use planning.\n\n"

                "### What it creates\n"
                "- **DEM/DTM raster**, resampled to the requested pixel size (`pixelsize`).\n"
                "- **Building footprints** for the AOI from the selected provider (`building_dataset`, default: 'OSM/BUILDINGS').\n"
                "- **Land-use/land-cover** layer for the AOI (`dataset_land_use`, default: 'ESA/WorldCover/v100').\n"
                "- **Sea mask** that separates land and water areas within the AOI.\n"
                "- All outputs are spatially aligned and clipped to the AOI.\n\n"
                
                "### Capabilities\n"
                "- Fetch a DEM/DTM from the specified dataset (if given, otherwise from the auto-detected most suitable dataset).\n"
                "- Retrieve **building footprints** from the given provider or use the default OSM-based source.\n"
                "- Retrieve **land-use/land-cover** information for better classification of terrain and regions.\n"
                "- Generate a **land/sea mask** covering the AOI.\n"
                "- Produce a set of harmonized layers ready for mapping, simulation, or other geospatial analyses.\n\n"

                "### Inputs\n"
                "- `dem_dataset (optional): Identifier of the DEM/DTM dataset **or `None`**. If `None`, the tool **auto-selects** the best dataset. \n"
                "- `building_dataset` (optional, default 'OSM/BUILDINGS'): Provider for building footprints.\n"
                "- `dataset_land_use` (optional, default 'ESA/WorldCover/v100'): Dataset for land-use/land-cover information.\n"
                "- `bbox` (required): AOI as EPSG:4326 bounding box. Use named keys `west,south,east,north`. If user provides a location name, you have to infer the bounding box.\n"
                "- `pixelsize` (optional): Desired DEM resolution in meters (> 0). Prefer None if user does not specify it, so the tool uses the native resolution of the DEM dataset.\n\n"

                "### When to use this tool\n"
                "- When the user explicitly asks for a **Digital Twin** of an area.\n"
                "- When harmonized layers of DEM, buildings, and land-use are needed for further analysis or simulations.\n"
                "- When a sea/land boundary mask is required for coastal or flood-related studies.\n"
                "- When the AOI is provided as geographic coordinates (bbox).\n\n"

                "### Behavior and defaults\n"
                "- The bounding box must be in EPSG:4326 coordinates.\n"
                "- If `dem_dataset` is **not provided** (None), the tool maps the AOI to country/region and selects a suitable DEM.\n"
                "- If `building_dataset` or `dataset_land_use` are not provided, the defaults are used.\n"
                "- Output is a set of raster and vector layers aligned on the same grid, ready for downstream tools and analyses.\n\n"

                "### Output\n"
                "The tool returns paths or URIs for each generated layer: DEM, buildings, land-use, and sea mask. "
                "These outputs form the core components of the Digital Twin for the specified AOI."
            ),
            args_schema = DigitalTwinInputSchema,
            **kwargs
        )
        self.execution_confirmed = False
        self.output_confirmed = True

    
    # DOC: Validation rules ( i.e.: valid init and lead time ... ) 
    def _set_args_validation_rules(self) -> dict:
        return dict()
        
    
    # DOC: Inference rules ( i.e.: from location name to bbox ... )
    def _set_args_inference_rules(self) -> dict:
        
        def infer_pixelsize(**kwargs):
            pixelsize = kwargs.get('pixelsize') or 0
            pixelsize = max(2, pixelsize)
            return pixelsize
        
        infer_rules = {
            'pixelsize': infer_pixelsize
        }
        return infer_rules
        
    
    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file
    def _execute(
        self,
        /,
        **kwargs: Any,  # dict[str, Any] = None,
    ): 
        # DOC: Prepare the payload for Digital-Twin API
        api_url = f"{os.getenv('SAFERPLACES_API_ROOT', 'http://localhost:5000')}/processes/digital-twin-process/execution"
        
        exec_uuid = utils.b64uuid()
        
        kwargs['bbox'] = kwargs['bbox'].to_list()
        
        additional_args = {
            "workspace": s3_utils.get_bucket_name_key(s3_utils._BASE_BUCKET)[0],
            "project": s3_utils.get_bucket_name_key(s3_utils._BASE_BUCKET)[1],
            "file_dem": f'dem-{exec_uuid}.tif',
            "file_building": f'building-{exec_uuid}.shp',
            "file_landuse": f'landuse-{exec_uuid}.tif',
            "file_dem_building": f'dem_building-{exec_uuid}.tif',
            "file_seamask": f'seamask-{exec_uuid}.tif',
        }
        
        credentials_args = {
            "user": os.getenv("SAFERPLACES_API_USER"),
            "token": os.getenv("SAFERPLACES_API_TOKEN"),
        }
        
        debug_args = {
            "debug": True,  # TODO: use a global _is_debug_mode() to set this
        }

        payload = { 
            "inputs": {
                **kwargs,               # DOC: This will include the args from the input schema
                **additional_args,      # DOC: Additional args for the API call
                **credentials_args,     # DOC: Credentials for the API call
                **debug_args,           # DOC: Debug args for the API call
            }
        }

        print('\n\n-------------------------------------------- \n')
        print(payload)
        print('\n\n-------------------------------------------- \n')
        
        # DOC: Call the Digital-Twin API ...
        api_response = requests.post(api_url, json=payload)
        
        # DOC: If the API call fails, return an error response
        if api_response.status_code != 200:
            tool_response = {
                'tool_response': {
                    'error': f"Failed to execute Digital Twin API: {api_response.status_code} - {api_response.text}"
                }
            }
        
        # DOC: If the API call is successful, process the response 
        api_response = api_response.json()
        if api_response.get('id') == 'digital-twin-process' and len(api_response.get('files', dict)) > 0:
            tool_response = {
                'tool_response': api_response,
                'updates': {
                    'layer_registry': self.graph_state.get('layer_registry', []) + ([
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, 'Digital Twin DEM'),
                            'description': 'Digital Twin DEM generated by SaferPlaces API',
                            'type': 'raster',
                            'src': api_response['files']['dem'],
                            'metadata': {
                                'surface_type': 'dem',
                                ** utils.raster_specs(api_response['files']['dem']),
                            },
                        }, 
                    ] if not GraphStates.src_layer_exists(self.graph_state, api_response['files']['dem']) else []) + ([
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, 'Digital Twin Buildings'),
                            'description': 'Digital Twin Buildings generated by SaferPlaces API',
                            'type': 'vector',
                            'src': api_response['files']['building'],
                            'metadata': dict(),
                        }
                    ] if not GraphStates.src_layer_exists(self.graph_state, api_response['files']['building']) else []) + ([
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, 'Digital Twin Land Use'),
                            'description': 'Digital Twin Land Use generated by SaferPlaces API',
                            'type': 'raster',
                            'src': api_response['files']['landuse'],
                            'metadata': {
                                'surface_type': 'land-use',
                                ** utils.raster_specs(api_response['files']['landuse']),
                            }
                        }
                    ] if not GraphStates.src_layer_exists(self.graph_state, api_response['files']['landuse']) else []) + ([
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, 'Digital Twin DEM + Buildings'),
                            'description': 'Digital Twin DEM + Buildings generated by SaferPlaces API',
                            'type': 'raster',
                            'src': api_response['files']['dem_building'],
                            'metadata': {
                                'surface_type': 'dem-building',
                                ** utils.raster_specs(api_response['files']['dem_building']),
                            }
                        }
                    ] if not GraphStates.src_layer_exists(self.graph_state, api_response['files']['dem_building']) else []) + ([
                        {
                            'title': GraphStates.new_layer_title(self.graph_state, 'Digital Twin Sea Mask'),
                            'description': 'Digital Twin Sea Mask generated by SaferPlaces API',
                            'type': 'raster',
                            'src': api_response['files']['seamask'],
                            'metadata': {
                                'surface_type': 'sea-mask',
                                'nodata': str(np.nan),
                                ** utils.raster_specs(api_response['files']['seamask']),
                            }
                        }
                    ] if not GraphStates.src_layer_exists(self.graph_state, api_response['files']['seamask']) else [])
                }
            }
            
        # DOC: If the API call is successful but the response is not as expected, return an error response
        else:
            tool_response = {
                'tool_response': {
                    'error': f"Unexpected response from Digital Twin API: {api_response}"
                }
            }
            
        # DOC: If there is an error in the tool response, update the messages to guide agent's next steps
        if 'error' in tool_response['tool_response']:
            tool_response['updates'] = {
                'messages': [ SystemMessage(content="An error occurred while executing the Digital Twin tool. Explain the error to the user and then ask him if he wants to retry or not.") ],
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
