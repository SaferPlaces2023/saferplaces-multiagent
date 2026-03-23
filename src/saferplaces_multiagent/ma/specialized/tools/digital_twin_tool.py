import os
import json
import requests

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, AliasChoices, PrivateAttr

from langchain_core.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ....common import utils, s3_utils
from ....common import names as N
from ....common import base_models
from ....common.states import MABaseGraphState


# ============================================================================
# Constants
# ============================================================================

# Response status constants
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"

# Default datasets/providers
DEFAULT_BUILDING_DATASET = "OSM/BUILDINGS"
DEFAULT_LANDUSE_DATASET = "ESA/WORLDCOVER/V100"
DEFAULT_PIXELSIZE = 5


# ============================================================================
# Schema
# ============================================================================

class DigitalTwinInputSchema(BaseModel):
    """
    Create a geospatial **Digital Twin** for a given Area of Interest (AOI) 
    by assembling:
    - a DEM/DTM raster from the specified elevation dataset,
    - building footprints from the chosen dataset/provider,
    - land-use/land-cover from the chosen dataset.

    The DEM is resampled to the requested pixel size (meters) and all 
    outputs are aligned over the AOI.

    Input Sources:
      • Direct URL: https://example.com/dem.tif
      • S3 URI: s3://bucket/project/dtm.tif
      • Local path: /tmp/dem.tif
      • Layer Registry reference: "Rome DTM" (uses layer's src)
    """

    # ============================================================================
    # Spatial Scope (Required)
    # ============================================================================

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

    # ============================================================================
    # Data Sources (Optional with Defaults)
    # ============================================================================

    dem_dataset: Optional[str] = Field(
        default=None,
        title="DEM/DTM dataset identifier",
        description=(
            "Identifier of the elevation dataset to derive the DTM/DEM (catalog key or provider path). "
            "Leave as `None` to let the tool auto-select the most suitable dataset from the AOI.\n\n"
            "Region-aware defaults (preferred sources by AOI):\n"
            "- **Italy** → GECOSISTEMA/ITALY\n"
            "- **Netherlands** → AHN/NETHERLANDS/05M\n"
            "- **Belgium** → GECOSISTEMA/BELGIUM/1M\n"
            "- **France** → IGN/RGE_ALTI/1M\n"
            "- **Spain** → IGN/ES/2M\n"
            "- **UK** → UK/LIDAR\n"
            "- **USA** → USGS/3DEP/1M\n"
            "- **Europe (fallback)** → COPERNICUS/EUDEM\n"
            "- **Global fallback** → NASA/NASADEM_HGT/001\n\n"
            "Selection rules:\n"
            "1) Prefer **highest native resolution** covering the AOI\n"
            "2) For **coastal AOI**, consider **DeltaDTM**\n"
            "3) If no national source fits, use **COPERNICUS/EUDEM** (Europe) or global fallback"
        ),
        examples=["COPERNICUS/EUDEM", "USGS/3DEP/1M", None],
        validation_alias=AliasChoices("dem_dataset", "dem", "dtm", "dem_dataset", "dtm_dataset"),
    )

    building_dataset: str = Field(
        default=DEFAULT_BUILDING_DATASET,
        title="Buildings dataset/provider",
        description=(
            f"Provider/dataset to fetch building footprints. "
            f"Default: '{DEFAULT_BUILDING_DATASET}'."
        ),
        examples=["OSM/BUILDINGS"],
        validation_alias=AliasChoices("building_dataset", "buildings", "buildings_provider"),
    )

    landuse_dataset: str = Field(
        default=DEFAULT_LANDUSE_DATASET,
        title="Land-use/land-cover dataset",
        description=(
            f"Dataset for land-use/land-cover classification. "
            f"Default: '{DEFAULT_LANDUSE_DATASET}'."
        ),
        examples=["ESA/WorldCover/v100"],
        validation_alias=AliasChoices("landuse_dataset", "landuse", "land_use", "landcover", "land_cover"),
    )

    # ============================================================================
    # Resolution
    # ============================================================================

    pixelsize: Optional[float] = Field(
        default=None,
        title="DEM pixel size (meters, optional)",
        description=(
            "Target ground sampling distance (meters) for the DEM/DTM resampling. "
            "Must be > 0. If None, output uses the native resolution of the DEM dataset. "
            f"Default: {DEFAULT_PIXELSIZE}"
        ),
        examples=[None, 1, 2, 5, 10, 30],
        validation_alias=AliasChoices("pixelsize", "pixel_size", "resolution", "res", "gsd"),
    )


# ============================================================================
# DigitalTwin Tool
# ============================================================================

class DigitalTwinTool(BaseTool):
    """
    Tool for generating a **geospatial Digital Twin** for a given AOI.

    Features:
      • Generate DEM/DTM raster resampled to requested pixel size
      • Retrieve building footprints from multiple providers
      • Extract land-use/land-cover layers
      • Create land/sea mask for coastal areas
      • Harmonize and align all outputs over the AOI
      • Support region-aware auto-selection of datasets

    Example use cases:
      • "Create a Digital Twin for flood risk assessment in Rome"
      • "Generate DEM, buildings, and land-use for urban planning in Netherlands"
      • "Prepare geospatial base layers for coastal erosion analysis"
    """

    short_description: ClassVar[str] = (
        "Creates a geospatial Digital Twin for a given Area of Interest: DEM/DTM raster, building footprints, "
        "land-use/land-cover, and sea/land mask — all spatially aligned and clipped to the AOI. "
        "Key params: bbox (required, EPSG:4326 west/south/east/north), dem_dataset (auto-selected by region if None, "
        "or explicit catalog key), pixelsize (output resolution in meters, optional). "
        "Ideal as the first step when base geospatial layers are needed for a new area before running simulations."
    )

    _graph_state: Optional[MABaseGraphState] = PrivateAttr(default=None)

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the DigitalTwin Tool."""
        super().__init__(
            name=N.DIGITAL_TWIN_TOOL,
            description=(
                "Generate a **geospatial Digital Twin** for a given Area of Interest (AOI).\n\n"
                "### Purpose\n"
                "This tool is typically the **first step** in a workflow. It provides harmonized "
                "base layers (DEM, buildings, land-use) that can later be used by other tools such as "
                "flood simulation, building analysis, or land-use planning.\n\n"
                "### What it creates\n"
                "- **DEM/DTM raster**, resampled to the requested pixel size\n"
                "- **Building footprints** from the selected provider (default: OSM)\n"
                "- **Land-use/land-cover** layer\n"
                "- **Sea/land mask** for coastal areas\n"
                "- All outputs are spatially aligned and clipped to the AOI\n\n"
                "### Inputs\n"
                "- `bbox` (required): AOI as EPSG:4326 bounding box\n"
                "- `dem_dataset` (optional): DEM identifier or None for auto-selection\n"
                "- `building_dataset` (optional): Building footprint provider\n"
                "- `landuse_dataset` (optional): Land-use/land-cover dataset\n"
                "- `pixelsize` (optional): DEM resolution in meters (prefer None for native resolution)\n\n"
                "### Output\n"
                "Paths/URIs for each generated layer (DEM, buildings, land-use, sea mask) "
                "forming the core components of the Digital Twin."
            ),
            args_schema=DigitalTwinInputSchema,
            **kwargs
        )

    def _set_graph_state(self, graph_state: MABaseGraphState) -> None:
        """Set the graph state for the tool."""
        self._graph_state = graph_state

    def _set_args_validation_rules(self) -> Dict[str, List]:
        """Define validation rules for tool arguments."""
        return {
            'pixelsize': [
                self._validate_pixelsize
            ],
        }

    @staticmethod
    def _validate_pixelsize(pixelsize: Optional[float] = None, **kwargs) -> Optional[str]:
        """Validate pixel size is positive if provided."""
        if pixelsize is not None and pixelsize <= 0:
            return f"pixelsize must be > 0, got {pixelsize}"
        return None

    def _set_args_inference_rules(self) -> Dict[str, Any]:
        """Define inference rules for missing arguments."""
        def infer_dem_dataset(**kwargs: Any) -> Optional[str]:
            """
            Infer appropriate DEM dataset based on AOI location.
            Returns None to trigger auto-selection in API.
            """
            # Return None to let the API auto-select based on bbox
            return None

        def infer_pixelsize(**kwargs: Any) -> Optional[float]:
            """
            Infer appropriate pixel size based on AOI extent.
            Apply clip forcing between 5 and 10 meters
            """
            pixelsize = kwargs.get('pixelsize')
            return max(5, min(10, pixelsize)) if pixelsize is not None else DEFAULT_PIXELSIZE


        return {
            'dem_dataset': infer_dem_dataset,
            'pixelsize': infer_pixelsize,
        }

    def _execute(self, **kwargs: Any) -> Dict[str, Any]:
        """
        Execute the DigitalTwin tool.

        Args:
            **kwargs: Tool arguments validated and inferred

        Returns:
            Dict with status and tool_output or error message
        """
        # Build API payload
        payload = self._build_api_payload(kwargs)

        # Call DigitalTwin API
        api_response = self._call_digitaltwin_api(payload)

        # Process response
        return self._process_api_response(api_response, kwargs)

    def _build_api_payload(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Build API request payload from tool arguments."""
        # Convert bbox to list format if needed
        bbox_data = kwargs['bbox']
        if hasattr(bbox_data, 'to_list'):
            bbox_list = bbox_data.to_list()
        elif isinstance(bbox_data, dict):
            bbox_list = list(bbox_data.values())
        else:
            bbox_list = bbox_data

        tool_args = {
            'bbox': bbox_list,
            'dem_dataset': kwargs.get('dem_dataset'),
            'building_dataset': kwargs.get('building_dataset', DEFAULT_BUILDING_DATASET),
            'landuse_dataset': kwargs.get('landuse_dataset', DEFAULT_LANDUSE_DATASET),
            'pixelsize': kwargs.get('pixelsize', DEFAULT_PIXELSIZE),
        }
        
        exec_uuid = utils.b64uuid()
        bucket_name, bucket_key = s3_utils.get_bucket_name_key(s3_utils._STATE_BUCKET_(self._graph_state))
        automatic_args = {
            "workspace": bucket_name,
            "project": bucket_key,
            "file_dem": f'dem-{exec_uuid}.tif',
            "file_building": f'building-{exec_uuid}.shp',
            "file_landuse": f'landuse-{exec_uuid}.tif',
            "file_dem_building": f'dem_building-{exec_uuid}.tif',
            "file_seamask": f'seamask-{exec_uuid}.tif',
        }

        credentials = {
            'user': os.getenv("SAFERPLACES_API_USER"),
            'token': os.getenv("SAFERPLACES_API_TOKEN"),
        }

        debug_config = {
            'debug': kwargs.get('debug', True)
        }

        return {
            'inputs': {
                **tool_args,
                **automatic_args,
                **credentials,
                **debug_config
            }
        }

    def _call_digitaltwin_api(self, payload: Dict[str, Any]) -> Any:
        """
        Call the DigitalTwin execution API.

        Args:
            payload: Request payload

        Returns:
            API response object
        """
        api_url = self._get_api_url()

        print(f"Calling DigitalTwin API at {api_url} with payload:\n", json.dumps(payload, indent=2))

        return requests.post(api_url, json=payload)

    @staticmethod
    def _get_api_url() -> str:
        """Get DigitalTwin API URL from environment."""
        api_root = os.getenv('SAFERPLACES_API_ROOT', 'http://localhost:5001')
        return f"{api_root}/processes/digital-twin-process/execution"

    def _process_api_response(
        self, 
        api_response: Any, 
        kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process API response and format tool output.

        Args:
            api_response: Response from DigitalTwin API
            kwargs: Original tool arguments

        Returns:
            Formatted tool response
        """
        # Check for HTTP errors
        if api_response.status_code != 200:
            return {
                'status': STATUS_ERROR,
                'message': f"DigitalTwin API request failed: {api_response.status_code} - {api_response.text}"
            }

        # Parse response JSON
        response_data = api_response.json()
        print(f"DigitalTwin API response:\n", json.dumps(response_data, indent=2))

        # DOC: Example structure
        # {
        #     "id": "digital-twin-process",
        #     "files": { 
        #          "building": "s3-uri", 
        #          "dem": "s3-uri", 
        #          "dem_building": "s3-uri", 
        #          "landuse": "s3-uri", 
        #          "seamask": "s3-uri" 
        #     }
        # }

        # Validate response structure (expect outputs with dem, buildings, landuse, seamask)
        required_fields = ['id', 'files']
        missing_fields = [field for field in required_fields if field not in response_data]
        if missing_fields:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected API response format. Missing fields: {missing_fields}"
            }
        required_files = ['dem', 'building', 'dem_building', 'landuse', 'seamask']
        missing_files = [file for file in required_files if file not in response_data.get('files', {})]
        if missing_files:
            return {
                'status': STATUS_ERROR,
                'message': f"Unexpected API response format. Missing files: {missing_files}"
            }

        # Return success response
        return {
            'status': STATUS_SUCCESS,
            'tool_output': {
                'data': response_data,
                'description': (
                    f"Digital Twin created successfully.\n"
                    f"• DEM: {response_data['files'].get('dem')}\n"
                    f"• Building: {response_data['files'].get('building')}\n"
                    f"• Land-use: {response_data['files'].get('landuse')}\n"
                    f"• Sea mask: {response_data['files'].get('seamask')}"
                )
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
