import warnings
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

from dotenv import load_dotenv

load_dotenv()


from . import common
from .common import (
    states,
    names,
    utils,
    s3_utils
)

from . import nodes
from .nodes import (
    base,
    tools,
    subgraphs
)

from .graph import graph
from .agent_interface import __GRAPH_REGISTRY__, GraphInterface

# FIXME: Setting pyproj (use sys.prefx to get venv path)
import os
os.environ['PROJ_LIB'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), r'..\..', r'venv\Lib\site-packages\pyproj\proj_dir\share\proj')
