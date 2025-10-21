import os
import io
import re
import uuid
import base64
import datetime
from dateutil import relativedelta
from enum import Enum
import requests
import contextlib

from typing import Optional, Union, List, Dict, Any, Literal
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator, validator

from langchain_core.messages import SystemMessage
from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)

from ....common import utils, s3_utils
from ....common import states as GraphStates
from ....common import names as N
from ....nodes.base import BaseAgentTool



_FILENAME_RE = re.compile(r"^[a-z0-9_-]+\.(geojson|gpkg|shp|tif|tiff)$")

class GeospatialOpsInputSchema(BaseModel):
    """
    Arguments for generating ready-to-run Python code that fulfills a geospatial request.
    If `output_file` is provided, a new dataset MUST be produced and saved to the
    preconfigured S3 prefix combined with this filename.
    """

    prompt: str = Field(
        ...,
        title="User Prompt",
        description=(
            "Natural-language request for a geospatial operation. Be explicit about "
            "layer names, filters/expressions, thresholds, and expected outputs when relevant."
        ),
        min_length=3,
        examples=[
            "Give me the bounding box of Rome",
            "Count features where property1 > 10 in buildings.geojson",
            "Clip buildings.geojson by flood_zone.geojson",
            "Intersect landuse.gpkg with protected_areas.gpkg",
        ],
    )

    output_file: Optional[str] = Field(
        default=None,
        title="Output filename (no path)",
        description=(
            "Filename ONLY (no folders/URIs). Lowercase, [a-z0-9_-], ending with one of: "
            ".geojson, .gpkg, .shp, .tif, .tiff. If provided, the code MUST create a dataset "
            "and save it to the preconfigured S3 prefix joined with this filename."
        ),
        examples=["result.geojson", "bbox_rome.geojson", "ndvi_clip.tif"],
    )

    return_kind: Literal["auto", "value", "geometry", "layer"] = Field(
        default="auto",
        title="Preferred result shape",
        description=(
            "Hint for the code about the expected result form: 'value' (numbers/stats), "
            "'geometry' (single geometry / GeoJSON-like), 'layer' (dataset), or 'auto'."
        ),
    )

    target_crs: Optional[str] = Field(
        default=None,
        title="Target CRS",
        description="Optional CRS to enforce on outputs (e.g., 'EPSG:4326').",
    )

    # @validator("output_file")
    # def _validate_filename(cls, v):
    #     if v is None:
    #         return v
    #     if not _FILENAME_RE.fullmatch(v):
    #         raise ValueError(
    #             "output_file must be filename only, lowercase, [a-z0-9_-], "
    #             "with extension .geojson|.gpkg|.shp|.tif|.tiff"
    #         )
    #     return v

    # class Config:
    #     # Impedisci campi sconosciuti; abilita uso dei nomi/alias indifferentemente
    #     extra = "forbid"
    #     populate_by_name = True


# DOC: This is a demo tool to retrieve weather data.
class GeospatialOpsTool(BaseAgentTool):

    # DOC: Initialize the tool with a name, description and args_schema
    def __init__(self, **kwargs):
        super().__init__(
            name=N.GEOSPATIAL_OPS_TOOL,
            description = """
            Generate ready-to-run Python code that executes a geospatial operation from a natural-language request.

            Inputs:
            - `prompt` (required): The user's request (e.g., bbox/centroid/count/stats; clip/intersect/union/buffer/reproject; vector or raster).
            - `output_file` (optional): Filename ONLY (no paths/URIs). Lowercase with [a-z0-9_-], ending in .geojson/.gpkg/.shp/.tif/.tiff.
            - `return_kind` (optional): Hint for the expected result form ('value' | 'geometry' | 'layer' | 'auto').
            - `target_crs` (optional): CRS to enforce on outputs (e.g., 'EPSG:4326').

            Behavior:
            - If `output_file` is provided, the operation MUST produce a new dataset and save it to the preconfigured S3 prefix combined with that filename. The code must not alter the filename or prepend any local path.
            - If `output_file` is not provided, return an in-memory result or print a concise summary (e.g., bbox coordinates, counts, stats).
            - Layers referenced in the prompt correspond to entries in the layer registry (provided in a separate system message). Load them by their URIs and types from that registry.
            - The generated code must be complete (no placeholders), use only approved libraries, and end by printing a short one-line summary of the operation and outputs.
            """,
            args_schema=GeospatialOpsInputSchema,
            **kwargs
        )
        self.execution_confirmed = True
        self.output_confirmed = False

    # DOC: Validation rules ( i.e.: valid init and lead time ... )

    def _set_args_validation_rules(self) -> dict:
        return dict()

    # DOC: Inference rules ( i.e.: from location name to bbox ... )

    def _set_args_inference_rules(self) -> dict:
        
        def infer_output_file(**kwargs):
            if kwargs.get('output_file') is not None:
                filename = utils.justfname(kwargs['output_file'])
                output_file = f"{s3_utils._BASE_BUCKET}/{filename}"
                return output_file
            return None
        
        infer_rules = {
            # 'output_file': lambda **kwargs: f"{s3_utils._BASE_BUCKET}/{kwargs['output_file']}" if kwargs.get('output_file', None) is not None else None,
            'output_file': infer_output_file,
        }
        return infer_rules

    # DOC: Execute the tool → Build notebook, write it to a file and return the path to the notebook and the zarr output file

    def _execute(
        self,
        /,
        **kwargs: Any,  # dict[str, Any] = None,
    ):

        if not self.output_confirmed:
            output = utils.ask_llm(
                role='system',
                message=[
                    GraphStates.build_layer_registry_system_message(self.graph_state.get('layer_registry', [])),
                    SystemMessage(content=f"""
You are a Python code generator specialized in geospatial operations.

OUTPUT REQUIREMENT:
- Return ONLY valid, executable Python code. No comments, no markdown, no explanations.
- Use ONLY these libraries: geopandas, shapely, pandas, fiona, rasterio, numpy, pyproj.
- Forbid any other imports (no os, sys, subprocess, shutil, requests, pathlib, etc.).
- No shell or network calls, except reading the URIs provided by the layer registry system message.

LAYER REGISTRY:
- A separate system message lists available layers with their names, types (vector/raster), and URIs.
- When the user references a layer by name, use the corresponding source ('src' field) to load it in the code.

PERSISTENCE RULE (NO AMBIGUITY):
- If `output_file` is provided, YOU MUST create a dataset and save it to:
  DEST_URI = "{kwargs.get('output_file')}".
- Do NOT modify the filename. Do NOT prepend local paths. Always save exactly to DEST_URI.
- Choose the save method by file extension:
  - Vector: .geojson/.gpkg/.shp via GeoPandas .to_file (driver GeoJSON/GPKG/ESRI Shapefile).
  - Raster: .tif/.tiff via rasterio (use appropriate profile/dtype).

DESCRIPTIVE REQUESTS:
- If the request is descriptive (e.g., "bbox of a city", counts, raster stats), compute the value.
- If `output_file` is not provided: print the value and keep results in memory.
- If `output_file` is provided and a dataset representation makes sense (e.g., bbox polygon as a GeoDataFrame, or a small raster mask), create it and save to DEST_URI.

CITY BBOX FALLBACK:
- If the request asks for the bbox of a well-known city and no suitable registry layer is used, provide a minimal internal gazetteer for a few major cities (approximate EPSG:4326 bounds) such as Rome, Paris, London, New York to compute the bbox.

TRANSFORMATIVE REQUESTS:
- For clip/intersect/union/dissolve/buffer/difference/reproject or raster crop/mask/reproject/statistics:
  - Load inputs from the registry (vector or raster).
  - Handle CRS carefully (GeoDataFrame.to_crs / rasterio reproject). If `target_crs` is provided, enforce it on outputs.
  - When `output_file` is provided, save the resulting dataset to DEST_URI as specified above.

FINAL PRINT (MANDATORY, LAST LINES ONLY):
- Print one single-line summary including:
  - the operation performed,
  - key result info (e.g., count, bbox coordinates, or raster stats),
  - and if saved, the exact DEST_URI.

INPUTS:
- User request (prompt): {kwargs['prompt']!r}
- output_file (filename-only or None): {kwargs.get('output_file')}
- return_kind: {kwargs.get('return_kind', 'auto')}
- target_crs (or None): {kwargs.get('target_crs')}
- persist_prefix (S3 prefix, preconfigured): {repr('{persist_prefix}')}

Generate the code now. Only code. No comments.
""")],
                eval_output=False
            )
            

            tool_response = {
                'generated_code': output
            }

        else:

            generated_code = self.output['generated_code']
            generated_code = generated_code.replace('```python', '').replace('```', '').strip()
            # execute the code and capture the output
            buffer = io.StringIO()

            # Reindirizziamo stdout dentro il buffer mentre eseguiamo il codice
            with contextlib.redirect_stdout(buffer):
                exec(generated_code)

            # Recuperiamo l'output come stringa
            output = buffer.getvalue()

            # map_actions = {
            #     'map_actions': [
            #         {
            #             'action': 'new_layer',
            #             'layer_data': {
            #                 'name': utils.juststem(kwargs['output_layer']),
            #                 'type': 'vector' if kwargs['output_layer'].endswith('.geojson') else 'raster',
            #                 'src': kwargs['output_layer']
            #             }
            #         }
            #     ]
            # } if kwargs.get('output_layer', None) else dict()
            
            
            tool_output = {
                'execution_output': output,
                ** ({'output_file': kwargs['output_file']} if kwargs.get('output_file', None) else dict()),
            }
            
            tool_updates = {
                'layer_registry': self.graph_state.get('layer_registry', []) + [
                    {
                        'title': f"{utils.juststem(kwargs['output_file'])}",
                        'description': f"Generated data from the request: \"{kwargs['prompt']}\"",
                        'src': kwargs['output_file'],
                        'type': 'raster' if kwargs['output_file'].endswith(('.tif', '.tiff')) else 'vector',
                        'metadata': dict()  # TODO: To be well defined (maybe class)
                    }
                ]
                if 'output_file' in kwargs and not GraphStates.src_layer_exists(self.graph_state, kwargs['output_file'])
                else []
            }
            

            tool_response = {
                'geospatial_ops_output': tool_output,
                'updates': tool_updates,
            }

        return tool_response

    # DOC: Back to a consisent state

    def _on_tool_end(self):
        self.execution_confirmed = True
        self.output_confirmed = False

    # DOC: Try running AgentTool → Will check required, validity and inference over arguments thatn call and return _execute()

    def _run(
        self,
        /,
        **kwargs: Any,  # dict[str, Any] = None,
    ) -> dict:

        run_manager: Optional[CallbackManagerForToolRun] = kwargs.pop(
            "run_manager", None)
        return super()._run(
            tool_args=kwargs,
            run_manager=run_manager
        )
