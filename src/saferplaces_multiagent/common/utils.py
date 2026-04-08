# DOC: Generic utils

import os
import sys
import re
import ast
import uuid
import math
import base64
import string
import secrets
import hashlib
import datetime
import requests
import tempfile
import textwrap
import urllib.parse

import numpy as np

import pyogrio
from shapely.geometry import box
import pandas as pd
import geopandas as gpd

import rasterio
import rioxarray as rxr
from rasterio.io import MemoryFile
from rasterio.warp import calculate_default_transform, reproject
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy
from rasterio.errors import RasterioIOError

from typing import Sequence

from langchain_openai import ChatOpenAI

from langchain_core.messages import RemoveMessage, AIMessage, ToolMessage, ToolCall

from . import s3_utils




# REGION: [Generic utils]

_temp_dir = os.path.join(tempfile.gettempdir(), 'saferplaces-agent')
os.makedirs(_temp_dir, exist_ok=True)


def guid():
    return str(uuid.uuid4())

def b64uuid():
    u = uuid.uuid4()
    return base64.urlsafe_b64encode(u.bytes).rstrip(b'=').decode('ascii')

def random_id8():
    chars = string.ascii_letters + string.digits  # A-Z a-z 0-9
    return ''.join(secrets.choice(chars) for _ in range(8))

def hash_string(s, hash_method=hashlib.md5):
    return hash_method(s.encode('utf-8')).hexdigest()

def download_url(src: str):
    if src.startswith('s3://'):
        return s3uri_to_https(src)
    elif src.startswith('https://') or src.startswith('http://'):
        return src
    else:
        return None

def s3uri_to_https(s3_uri):
    """
    Converte una URI S3 (s3://bucket/key) in un URL HTTP (https://bucket.s3.amazonaws.com/key)
    con encoding dei caratteri speciali.
    """
    if not s3_uri.startswith("s3://"):
        return s3_uri  # Already an HTTPS URL or invalid format
    # Rimuovi prefisso e separa bucket e key
    bucket, key = s3_utils.get_bucket_name_key(s3_uri)
    s3_region = os.getenv("AWS_REGION", "us-east-1")  # Default to us-east-1 if not set
    # Encode della chiave
    encoded_key = urllib.parse.quote(key, safe="/")
    return f"https://s3.{s3_region}.amazonaws.com/{bucket}/{encoded_key}"

def s3https_to_s3uri(s3_url):
    """
    Converte un URL S3 (sia virtual-hosted-style che path-style)
    in un URI S3 (s3://bucket/key), tenendo conto della regione.
    """
    parsed = urllib.parse.urlparse(s3_url)
    host = parsed.netloc
    path = parsed.path.lstrip('/')
    # Caso 1: Virtual-hosted-style → <bucket>.s3.<region>.amazonaws.com/<key>
    vh_match = re.match(r'^([^.]+)\.s3[.-]([a-z0-9-]+)?\.amazonaws\.com$', host)
    if vh_match:
        bucket = vh_match.group(1)
        key = urllib.parse.unquote(path)
        return f"s3://{bucket}/{key}"
    # Caso 2: Path-style → s3.<region>.amazonaws.com/<bucket>/<key>
    ps_match = re.match(r'^s3[.-]([a-z0-9-]+)?\.amazonaws\.com$', host)
    if ps_match:
        # Bucket è la prima parte del path
        parts = path.split('/', 1)
        if len(parts) != 2:
            raise ValueError("Path non valido per URL S3 path-style")
        bucket, key = parts
        key = urllib.parse.unquote(key)
        return f"s3://{bucket}/{key}"
    return s3_url  # Non è un URL S3 valido, restituisci come tale
    
def s3uri_to_vsis3(s3_uri):
    return s3_uri.replace('s3://', '/vsis3/')


def python_path():
    """ python_path - returns the path to the Python executable """
    pathname, _ = os.path.split(normpath(sys.executable))
    return pathname

def normpath(pathname):
    """ normpath - normalizes the path to use forward slashes """
    if not pathname:
        return ""
    return os.path.normpath(pathname.replace("\\", "/")).replace("\\", "/")

def juststem(pathname):
    """ juststem - returns the file name without the extension """
    pathname = os.path.basename(pathname)
    root, _ = os.path.splitext(pathname)
    return root

def justpath(pathname, n=1):
    """ justpath - returns the path without the last n components """
    for _ in range(n):
        pathname, _ = os.path.split(normpath(pathname))
    if pathname == "":
        return "."
    return normpath(pathname)

def justfname(pathname):
    """ justfname - returns the basename """
    return normpath(os.path.basename(normpath(pathname)))

def justext(pathname):
    """ justext - returns the file extension without the dot """
    pathname = os.path.basename(normpath(pathname))
    _, ext = os.path.splitext(pathname)
    return ext.lstrip(".")

def forceext(pathname, newext):
    """ forceext - replaces the file extension with newext """
    root, _ = os.path.splitext(normpath(pathname))
    pathname = root + ("." + newext if len(newext.strip()) > 0 else "")
    return normpath(pathname)


def try_default(f, default_value=None):
    """ try_default - returns the value if it is not None, otherwise returns default_value """
    try:
        value = f()
        return value
    except Exception as e:
        return default_value
    
     
def floor_decimals(number, decimals=0):
    factor = 10 ** decimals
    return math.floor(number * factor) / factor

def ceil_decimals(number, decimals=0):
    factor = 10 ** decimals
    return math.ceil(number * factor) / factor


def dedent(s: str, add_tab: int = 0, tab_first: bool = True) -> str:
    """Dedent a string by removing common leading whitespace."""
    out = textwrap.dedent(s).strip()
    if add_tab > 0:
        out_lines = out.split('\n')
        tab = ' ' * 4
        out = '\n'.join([tab * add_tab + line if (il==0 and tab_first) or (il>0) else line for il,line in enumerate(out_lines)])
    return out
    
# ENDREGION: [Generic utils]



# REGION: [Geospatial utils]

def common_specs(src: str):
    specs = dict()
    dl_url = download_url(src)
    if dl_url:
        specs['download_url'] = dl_url
    return specs

def get_geodataframe_crs(geo_df):
    epsg_code = geo_df.crs.to_epsg()
    if epsg_code is None:
        raise ValueError("GeoDataFrame does not have a defined CRS.")
    return f"EPSG:{epsg_code}"

def vector_specs(src: str) -> dict:
    """Get the specifications of a vector file."""

    def safe_float(x):
        if pd.isna(x):
            return 0.0
        return float(x)

    def attribute_specs(geo_df, attr):
        col = geo_df[attr]
        if pd.api.types.is_numeric_dtype(col):
            desc = col.describe()
            return {
                "type": "numeric",
                # "count": safe_float(desc.get("count")),
                "mean": safe_float(desc.get("mean")),
                # "std": safe_float(desc.get("std")),
                "min": safe_float(desc.get("min")),
                # "25%": safe_float(desc.get("25%")),
                # "50%": safe_float(desc.get("50%")),
                # "75%": safe_float(desc.get("75%")),
                "max": safe_float(desc.get("max")),
            }
        else:
            max_n_unique = 30
            unique_counts = col.value_counts()
            n_unique = len(unique_counts)
            n_total = len(col)
            if n_unique > 0.5 * n_total:
                return {
                    "type": "categorical",
                    "unique_values": []
                }
            if n_unique > max_n_unique:
                unique_counts = unique_counts.head(max_n_unique)
            return {
                "type": "categorical",
                "unique_values": unique_counts.index.tolist()
            }


    geo_df = gpd.read_file(src)
    epsg_code = get_geodataframe_crs(geo_df)
    bounds = gpd.GeoDataFrame(geometry=[box(*geo_df.total_bounds)], crs=geo_df.crs).to_crs(epsg=4326).total_bounds.tolist()
    return {
        'crs': epsg_code,
        'bounding-box-wgs84': {
            'minx': bounds[0],
            'miny': bounds[1],
            'maxx': bounds[2],
            'maxy': bounds[3]
        },
        'n_features': len(geo_df),
        'geometry_type': geo_df.geometry.type.unique().tolist(),
        # 'attributes': {col: str(geo_df[col].dtype) for col in geo_df.columns},
        'attributes': {col: attribute_specs(geo_df, col) for col in geo_df.columns},
        ** common_specs(src)
    }

def is_vector_4326(geo_df):
    """Check if the GeoDataFrame is in EPSG:4326."""
    eps_string = get_geodataframe_crs(geo_df)
    return eps_string == 'EPSG:4326'

def vector_to_geojson4326_local(path: str) -> str:
    """Ensure a local GeoJSON file is in EPSG:4326, reprojecting in-place if needed.
    Returns the (same) path."""
    gdf = gpd.read_file(path)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
        gdf.to_file(path, driver='GeoJSON')
    return path

def fast_is_vector_4326(src: str) -> bool:
    tmp = s3_utils.s3_download(s3https_to_s3uri(src), justfname(src))
    info = pyogrio.read_info(tmp)
    is_4326 = info["crs"] == "EPSG:4326"
    os.remove(tmp)
    return is_4326

def vector_to_geojson4326(src: str, dst: str = None, debug: bool = False) -> str:
    # DOC: if src is a s3 uri, convert it to https
    if src.startswith('s3://'):
        src = s3uri_to_https(src)
        
    # DOC: if src is a S3, check if its 4326 version exists
    if src.startswith('https://s3'):
        src_uri = s3uri_to_https(src)
        ext = justext(src_uri)
        src_4326_uri = src_uri.replace(f'.{ext}', '.4326.geojson')
        if s3_utils.s3_exists(s3https_to_s3uri(src_4326_uri)):
            return src_4326_uri
        
    # DOC: if dst is not provided, create a default one
    if dst is None:
        if src.startswith('https://s3'):
            ext = justext(src)
            dst = src.replace(f'.{ext}', '.4326.geojson')
        else:
            dst = f"{s3_utils._BASE_BUCKET_}/4326/{juststem(src)}.4326.geojson"
            
    # DOC: if src is already a 4326 geojson, return it
    if justext(src) == 'geojson' and fast_is_vector_4326(src):
        return src
    
    # DOC: if dst is a s3 uri, use a temporary local file
    if dst.startswith('s3://') or dst.startswith('https://s3'):
        use_tmp_dst = True
        dst_local = os.path.join(_temp_dir, juststem(dst) + '.4326.geojson')
    else:
        use_tmp_dst = False
        
    if debug:
        print(f"vector_to_geojson4326: Converting {src} to 4326 GeoJSON at {dst}")
        
    # DOC: run conversion
    gdf = gpd.read_file(src)
    gdf4326 = gdf.to_crs(epsg=4326)
    gdf4326.to_file(dst if not use_tmp_dst else dst_local, driver='GeoJSON')
    
    # DOC: if dst is a s3 uri, upload the local file to s3
    if use_tmp_dst:
        dst = s3https_to_s3uri(dst)
        s3_utils.s3_upload(filename=dst_local, uri=dst, remove_src=True)
        
    return dst
    

def raster_specs(src: str) -> dict:
    da = rxr.open_rasterio(src)
    nodata_val = da.rio.nodata
    if nodata_val is None:
        da_valid = da
    elif isinstance(nodata_val, float) and np.isnan(nodata_val):
        da_valid = da.where(~np.isnan(da))
    else:
        da_valid = da.where(da != nodata_val)
    nodata_val = nodata_val.item() if nodata_val is not None and not np.isnan(nodata_val) else str(np.nan)
    min_val = float(da_valid.min(skipna=True).compute())
    max_val = float(da_valid.max(skipna=True).compute())
    n_bands = da.sizes.get('band', 1)
    crs_str = da.rio.crs.to_string() if da.rio.crs else None
    bounds_proj = gpd.GeoDataFrame(geometry=[box(*da.rio.bounds())], crs=da.rio.crs).to_crs(epsg=4326).total_bounds.tolist()
    return {
        'min': min_val if not np.isnan(min_val) else None,
        'max': max_val if not np.isnan(max_val) else None,
        'nodata': nodata_val,
        'n_bands': n_bands,
        'crs': crs_str,
        'bounding-box-wgs84': {
            'minx': bounds_proj[0],
            'miny': bounds_proj[1],
            'maxx': bounds_proj[2],
            'maxy': bounds_proj[3]
        },
        ** common_specs(src)
    }

def raster_ts_specs(src: str, timestamps_attr: str = 'band_names') -> dict:
    """Get the specifications of a raster time series."""
    base_specs = raster_specs(src)
    da = rxr.open_rasterio(src)
    try:
        timestamps = da.attrs[timestamps_attr]
        timestamps = ast.literal_eval(timestamps)
        tstart = datetime.datetime.fromisoformat(timestamps[0]).replace(tzinfo=None)
        tend = datetime.datetime.fromisoformat(timestamps[-1]).replace(tzinfo=None)
        return {
            **base_specs,
            'time_start': tstart.isoformat(),
            'time_end': tend.isoformat()
        }
    except Exception:
        return base_specs 


def is_cog(src: str) -> bool:
    p = src if src.startswith(("/vsicurl/", "/vsis3/", "s3://")) else ("/vsicurl/"+src if src.startswith(("http://","https://")) else src)
    try:
        with rasterio.open(p) as ds:
            if ds.driver != "GTiff": return False
            if (ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT","").upper() == "COG"): return True
            tiled = ds.profile.get("tiled", False) or ds.tags(ns="IMAGE_STRUCTURE").get("TILED","").upper()=="YES"
            return tiled and len(ds.overviews(1)) > 0
    except RasterioIOError:
        return False

def is_raster_3857(src: str) -> bool:
    # Gestione percorsi remoti
    src = src if src.startswith(("/vsicurl/", "/vsis3/", "s3://")) else ("/vsicurl/"+src if src.startswith(("http://","https://")) else src)
    is_remote = src.startswith("http://") or src.startswith("https://") or src.startswith("s3://") or src.startswith("/vsis3/") or src.startswith("/vsicurl/") 

    if is_remote:
        # Prova con /vsicurl/
        # vsicurl_path = f"/vsicurl/{src}"
        try:
            with rxr.open_rasterio(src) as da:
                if da.rio.crs is not None and da.rio.crs.to_epsg() is not None:
                    return da.rio.crs.to_epsg() == 3857                    
            with rasterio.open(src) as ds:
                return ds.crs is not None and ds.crs.to_epsg() == 3857
        except Exception:
            # fallback: scarica temporaneamente il file
            with tempfile.NamedTemporaryFile(suffix=".tif", delete=True) as tmp:
                r = requests.get(src, timeout=30)
                r.raise_for_status()
                tmp.write(r.content)
                tmp.flush()
                try:
                    with rasterio.open(tmp.name) as ds:
                        return ds.crs is not None and ds.crs.to_epsg() == 3857
                except Exception:
                    return False
    else:
        # File locale
        if not os.path.exists(src):
            raise FileNotFoundError(f"File non trovato: {src}")
        try:
            with rasterio.open(src) as ds:
                return ds.crs is not None and ds.crs.to_epsg() == 3857
        except Exception:
            return False
    
def tif_to_cog3857(src: str, dst: str = None, debug: bool = False, **kwargs) -> str:
    # DOC: if src is already a COG in EPSG:3857, return it
    do_reproject = not is_raster_3857(src)
    do_cog = do_reproject or not is_cog(src)
    if not any([do_reproject, do_cog]):
        return src

    # DOC: if src is a s3 uri, convert it to https
    if src.startswith('s3://'):
        src = s3uri_to_https(src)
    
    # DOC: if src is a S3, check if its cog version exists   
    if src.startswith('https://s3'):
        src_cog_url = src.replace('.tif', '-cog3857.tif')
        if s3_utils.s3_exists(s3https_to_s3uri(src_cog_url)):
            return s3https_to_s3uri(src_cog_url)
    
    # DOC: if dst is not provided, create a default one
    if dst is None:
        if src.startswith('https://s3'):
            dst = src.replace('.tif', '-cog3857.tif')
        else:
            dst = f"{s3_utils._BASE_BUCKET_}/cog/{juststem(src)}-cog3857.tif"

    # DOC: if dst is a s3 uri, use a temporary local file     
    if dst.startswith('s3://') or dst.startswith('https://s3'):
        use_tmp_dst = True
        dst_local = os.path.join(_temp_dir, juststem(dst) + '-cog3857.tif')
    else:
        use_tmp_dst = False
    
    if debug:
        print(f"tif_to_cog: Converting {src} to COG at {dst}")

    def to_cog(src, dst, **kwargs):
        """Run the COG conversion."""
        rio_copy(
            src,
            dst,
            driver="COG",
            COMPRESS=kwargs.get("COMPRESS", "DEFLATE"),
            PREDICTOR=kwargs.get("PREDICTOR", "2"),           # 2 per continui, 3 per RGB
            BLOCKSIZE=kwargs.get("BLOCKSIZE", "256"),
            BIGTIFF=kwargs.get("BIGTIFF", "IF_SAFER"),
            NUM_THREADS=kwargs.get("NUM_THREADS", "ALL_CPUS"),
            OVERVIEWS=kwargs.get("OVERVIEWS", "IGNORE_EXISTING"),
            RESAMPLING=kwargs.get("RESAMPLING", "AVERAGE")     # stringa, non enum
        )
        # new_nodata = 0  # cambia qui
        # with rasterio.open(dst, "r+") as ds:
        #     ds.nodata = 0   # aggiorna il NoData (per-band sotto al cofano)

    if do_reproject:
        dst_crs = "EPSG:3857"
        src_rio = src if src.startswith(("/vsicurl/", "/vsis3/", "s3://")) else ("/vsicurl/"+src if src.startswith(("http://","https://")) else src)
        print(f"tif_to_cog: Reprojecting {src_rio} to {dst_crs}")
        with rasterio.open(src_rio) as src_ds:
            transform, width, height = calculate_default_transform(
                src_ds.crs, dst_crs, src_ds.width, src_ds.height, *src_ds.bounds
            )
            kwargs = src_ds.meta.copy()
            kwargs.update({
                "crs": dst_crs,
                "transform": transform,
                "width": width,
                "height": height
            })
            with MemoryFile() as memfile:
                with memfile.open(**kwargs) as dst_ds:
                    for i in range(1, src_ds.count + 1):
                        reproject(
                            source=rasterio.band(src_ds, i),
                            destination=rasterio.band(dst_ds, i),
                            src_transform=src_ds.transform,
                            src_crs=src_ds.crs,
                            dst_transform=transform,
                            dst_crs=dst_crs,
                            resampling=Resampling.average
                        )
                # DOC: run cog conversion
                # rio_copy(
                #     memfile.name,
                #     dst if not use_tmp_dst else dst_local,
                #     driver="COG",
                #     COMPRESS=kwargs.get("COMPRESS", "DEFLATE"),
                #     PREDICTOR=kwargs.get("PREDICTOR", "2"),           # 2 per continui, 3 per RGB
                #     BLOCKSIZE=kwargs.get("BLOCKSIZE", "256"),
                #     BIGTIFF=kwargs.get("BIGTIFF", "IF_SAFER"),
                #     NUM_THREADS=kwargs.get("NUM_THREADS", "ALL_CPUS"),
                #     OVERVIEWS=kwargs.get("OVERVIEWS", "IGNORE_EXISTING"),
                #     RESAMPLING=kwargs.get("RESAMPLING", "AVERAGE")     # stringa, non enum
                # )
                to_cog(memfile.name, dst if not use_tmp_dst else dst_local, **kwargs)
    
    elif do_cog:
        # DOC: run cog conversion
        # rio_copy(
        #     src,
        #     dst if not use_tmp_dst else dst_local,
        #     driver="COG",
        #     COMPRESS=kwargs.get("COMPRESS", "DEFLATE"),
        #     PREDICTOR=kwargs.get("PREDICTOR", "2"),           # 2 per continui, 3 per RGB
        #     BLOCKSIZE=kwargs.get("BLOCKSIZE", "256"),
        #     BIGTIFF=kwargs.get("BIGTIFF", "IF_SAFER"),
        #     NUM_THREADS=kwargs.get("NUM_THREADS", "ALL_CPUS"),
        #     OVERVIEWS=kwargs.get("OVERVIEWS", "IGNORE_EXISTING"),
        #     RESAMPLING=kwargs.get("RESAMPLING", "AVERAGE")     # stringa, non enum
        # )
        to_cog(src, dst if not use_tmp_dst else dst_local, **kwargs)
    
    # DOC: if dst is a s3 uri, upload the local file to s3
    print(f"tif_to_cog: Conversion completed, saving to {dst if not use_tmp_dst else dst_local}")
    if use_tmp_dst:
        dst = s3https_to_s3uri(dst)
        upload_status = s3_utils.s3_upload(filename=dst_local, uri=dst, remove_src=True)
        if not upload_status:
            raise Exception(f"Failed to upload {dst_local} to {dst}")
    
    return dst


def raster_like_lazy(da, da_template, resampling=Resampling.nearest, chunks={}):
    da = rxr.open_rasterio(da, chunks=chunks).squeeze() if isinstance(da, str) else da
    da_template = rxr.open_rasterio(da_template, chunks=chunks).squeeze() if isinstance(da_template, str) else da_template

    aligned = da.rio.reproject_match(da_template, resampling=resampling, num_threads=1)
    if (da_template.rio.nodata is not None and
        da_template.rio.nodata != aligned.rio.nodata):
        aligned = aligned.rio.write_nodata(da_template.rio.nodata, encoded=True)

    return aligned

# ENDREGION: [Geospatial utils]



# REGION: [Disable arnings]

def disable_warnings():
    import warnings
    
    # DOC: Disable warnings from langchain
    for warning in disable_langchain_warnings():
        warnings.filterwarnings("ignore", category=warning)
    
def disable_langchain_warnings():
    from langchain_core._api import LangChainBetaWarning
    return [
        LangChainBetaWarning,
    ]

# ENDREGION: [Disable arnings]



# REGION: [Prompt helpers]

def get_conversation_context(state, n: int = 5) -> str:
    """Return last n HumanMessage/AIMessage (no tool_calls) as a readable block.

    Shared helper used by all prompt modules to avoid triplication.
    """
    from langchain_core.messages import HumanMessage as _HM, AIMessage as _AI
    messages = state.get("messages") or []
    relevant = [
        m for m in messages
        if isinstance(m, _HM)
        or (isinstance(m, _AI) and not getattr(m, "tool_calls", None))
    ]
    if not relevant:
        return ""
    lines = []
    for m in relevant[-n:]:
        role = "User" if isinstance(m, _HM) else "Assistant"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)

# ENDREGION: [Prompt helpers]


# REGION: [LLM and Tools]

_base_llm = ChatOpenAI(
    model="gpt-4o-mini",
    max_completion_tokens=3000,
    temperature=0
)

def ask_llm(role, message, llm=_base_llm, eval_output=False):
    if type(message) is str:
        llm_out = llm.invoke([{"role": role, "content": message}])
    elif type(message) is list:
        llm_out = llm.invoke(message)
    if eval_output:
        try: 
            content = llm_out.content
            
            # TODO: Print if in debug mode (also maybe it is useful to write on a file? (saved on s3!!! wow))
            # print('\n\n')
            # print(type(content))
            # print(content)
            # print('\n\n')
            
            # DOC: LLM can asnwer with a python code block, so we need to extract the code and evaluate it
            if type(content) is str and content.startswith('```python'):
                content = content.split('```python')[1].split('```')[0]
            if type(content) is str and content.startswith('```json'):
                content = content.split('```json')[1].split('```')[0]
            
            # DOC: LLM can answer with a python dict but sometimes as json, so we need to convert some values from json to py
            content = re.sub(r'\bnull\b', 'None', content) # DOC: replace null with None
            content = re.sub(r'\btrue\b', 'True', content) # DOC: replace true with True
            content = re.sub(r'\bfalse\b', 'False', content) # DOC: replace false with False
            
            return ast.literal_eval(content)
        except: 
            pass
    return llm_out.content


def map_action_new_layer(layer_name, layer_src, layer_styles=[]):
    """Create a map action with the given type and data."""
    layer_styles = { 'styles': layer_styles } if len(layer_styles) > 0 else dict()
    action_new_layer = {
        'action': 'new_layer',
        'layer_data': {
            'name': layer_name,
            'type': 'vector' if layer_src.endswith('.geojson') else 'raster',   # TODO: add more extensions (e.g. .gpkg, .tif, tiff, geotiff, etc.)
            'src': s3uri_to_https(layer_src),
            ** layer_styles
        }
    }
    return action_new_layer

# ENDREGION: [LLM and Tools]



# REGION: [Message utils funtion]

def merge_sequences(left: Sequence[str], right: Sequence[str]) -> Sequence[str]:
    """Add two lists together."""
    return left + right

def merge_dictionaries(left: dict, right: dict) -> dict:
    """Add two dictionaries together but merging ad all levels."""
    for key, value in right.items():
        if key in left:
            if isinstance(left[key], dict) and isinstance(value, dict) and len(value) > 0:
                left[key] = merge_dictionaries(left[key], value)
            elif isinstance(left[key], list) and isinstance(value, list):
                left[key] = left[key] + value
            else:
                left[key] = value
        else:
            left[key] = value
    return left     

def merge_dict_sequences(left: Sequence[dict], right: Sequence[dict], unique_key: str | None = None, method: str | None = 'update') -> Sequence[dict]:
    """Add two lists of dictionaries together, merging by unique_key if provided and updating the values."""
    if unique_key is None:
        return merge_sequences(left, right)
    merged = {item[unique_key]: item for item in left}
    for item in right:
        if item[unique_key] in merged:
            if method == 'update':
                merged[item[unique_key]] = merge_dictionaries(merged[item[unique_key]], item)
            elif method == 'overwrite':
                merged[item[unique_key]] = item
        else:
            merged[item[unique_key]] = item       
    return list(merged.values())
            

def is_human_message(message):
    """Check if the message is a human message."""
    return hasattr(message, 'role') and message.role == 'human'


def remove_message(message_id):
    return RemoveMessage(id = message_id)

def remove_tool_messages(tool_messages):
    if type(tool_messages) is not list:
        return remove_message(tool_messages.id)
    else:
        return [remove_message(tm.id) for tm in tool_messages]
    
    
def build_tool_call_message(tool_name, tool_args=None, tool_call_id=None, message_id=None, message_content=None):
    message_id = hash_string(datetime.datetime.now().isoformat()) if message_id is None else message_id
    message_content = "" if message_content is None else message_content
    tool_call_id = hash_string(message_id) if tool_call_id is None else tool_call_id
    tool_call_message = AIMessage(
        id = message_id,
        content = message_content,
        tool_calls = [
            ToolCall(
                id = tool_call_id,
                name = tool_name,
                args = tool_args if tool_args is not None else dict()
            )
        ]
    )
    return tool_call_message

# ENDREGION: [Message utils funtion]