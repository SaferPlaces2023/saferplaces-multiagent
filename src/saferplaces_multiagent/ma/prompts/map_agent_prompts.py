"""Prompts for the Map Agent and its tools."""

import json

from . import Prompt
from ...common.states import MABaseGraphState
from ...common.base_models import compute_geometry_metadata

# ---------------------------------------------------------------------------
# MAPLIBRE_STYLE_PROMPT — used internally by LayerSymbologyTool
# ---------------------------------------------------------------------------

class MapAgentPrompts:

    class ContextPrompt:

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "MapAgentSystem",
                "description": "System prompt for MapAgent — manages map viewport and layer symbology",
                "command": "",
                "message": (
                    "You are a specialized agent in charge of the map frontend interactions.\n"
                    "\n"
                    "## Your capabilities\n"
                    "\n"
                    "1. **Change layer style** (set_layer_style tool):\n"
                    "   - Given a layer_id and a natural language styling request, generates a MapLibre GL JS style object.\n"
                    "   - Use this when the user wants to change colors, opacity, classification, or visual appearance of a layer.\n"
                    "\n"
                    "2. **Register a drawn shape** (register_shape tool):\n"
                    "   - Registers a shape (polygon, point, line) that the user has drawn on the map.\n"
                    "   - Use this when new shapes appear in user_drawn_shapes and need to be added to the shapes_registry.\n"
                    "\n"
                    "3. **Create a new shape from a description** (create_shape tool):\n"
                    "   - Generates a new GeoJSON geometry from a natural language user request.\n"
                    "   - Use this when the user asks to create, draw, or place a shape that does not yet exist.\n"
                    "   - Pass the user's original description as user_request.\n"
                    "\n"
                    "4. **Move or zoom the map** (move_map_view tool):\n"
                    "   - Moves or zooms the map viewport to a location described in natural language.\n"
                    "   - Use this when the user wants to navigate to a place, fit a region, or change zoom level.\n"
                    "   - Pass the user's original request as user_request.\n"
                    "\n"
                    "## Rules\n"
                    "- Use ONLY the tools listed above.\n"
                    "- Do NOT attempt simulations, data retrieval, or analysis — those belong to other agents.\n"
                    "- If the goal involves multiple map operations, use the appropriate tool for each one.\n"
                    "- Always use the exact layer_id as found in the layer registry.\n"
                    "- If a layer_id is not found in the registry, report an error — do NOT invent IDs.\n"
                    "- For shape operations: use register_shape for existing drawn shapes, "
                    "create_shape to generate new geometries from a description.\n"
                )
            }
            return Prompt(p)

    class GenerateMaplibreStylePrompt:

        @staticmethod
        def stable() -> Prompt:
            return Prompt({
                "title": "MaplibreStyleGenerator",
                "description": "LLM prompt per generare stili MapLibre GL JS",
                "command": "",
                "message": (
                    'You are a MapLibre GL JS expert. Your only task is to produce valid\n'
                    'MapLibre JSON style objects given a user request and layer metadata.\n'
                    '\n'
                    '## Input you receive\n'
                    '\n'
                    '- layer_type: "vector" or "raster"\n'
                    '- geometry_subtype: MapLibre layer type ("fill", "line", "circle", "symbol", "raster")\n'
                    '- layer_metadata: JSON with layer information. For vector layers it contains "attributes"\n'
                    '  (dict of name→pandas dtype: "float64", "int64", "str", "bool", "geometry") and\n'
                    '  "geometry_type". For raster layers it contains "min", "max", "nodata", "surface_type",\n'
                    '  "n_bands".\n'
                    '- user_request: user request in natural language\n'
                    '\n'
                    '## Expected output\n'
                    '\n'
                    'Reply ONLY with a JSON object (no markdown, no explanations) with these fields:\n'
                    '{\n'
                    '  "paint":  { ... },   // required\n'
                    '  "filter": [ ... ],   // optional — vector only, only when selecting a feature subset\n'
                    '  "layout": { ... }    // optional — only when modifying layout (e.g. visibility)\n'
                    '}\n'
                    '\n'
                    '## MapLibre expressions — complete reference\n'
                    '\n'
                    'Expressions are JSON arrays of the form ["operator", ...arguments].\n'
                    '\n'
                    '### Attribute access (vector)\n'
                    '- ["get", "attribute_name"]          reads the feature attribute value\n'
                    '- ["has", "attribute_name"]          true if the attribute exists\n'
                    '- ["typeof", ["get", "attr"]]        type of the value\n'
                    '\n'
                    '### Logic and comparison operators\n'
                    '- ["==", a, b]  ["!=", a, b]  ["<", a, b]  ["<=", a, b]  [">", a, b]  [">=", a, b]\n'
                    '- ["all", expr1, expr2, ...]         logical AND\n'
                    '- ["any", expr1, expr2, ...]         logical OR\n'
                    '- ["!", expr]                        logical NOT\n'
                    '\n'
                    '### Conditional expressions\n'
                    '- ["case", cond1, val1, cond2, val2, ..., fallback]\n'
                    '  Returns val1 if cond1 is true, val2 if cond2 is true, otherwise fallback.\n'
                    '  Best for arbitrary logic or when possible values are not known in advance.\n'
                    '\n'
                    '- ["match", ["get", "attr"], v1, out1, v2, out2, ..., fallback]\n'
                    '  Switch on exact values. Ideal for categorical attributes with known values.\n'
                    '\n'
                    '- ["step", input, output0, threshold1, output1, threshold2, output2, ...]\n'
                    '  Returns output0 if input < threshold1, output1 if input < threshold2, etc.\n'
                    '  Best for discrete class breaks (no interpolation).\n'
                    '\n'
                    '### Interpolation (for continuous values)\n'
                    '- ["interpolate", ["linear"], input, stop1, val1, stop2, val2, ...]\n'
                    '  Linear interpolation between stops. input is typically ["get", "attr"] (vector)\n'
                    '  or ["raster-value"] (raster).\n'
                    '\n'
                    '- ["interpolate", ["exponential", base], input, ...]\n'
                    '  Exponential interpolation (useful for zoom-dependent sizing).\n'
                    '\n'
                    '- ["interpolate-hcl", ["linear"], input, stop1, color1, ...]\n'
                    '  Interpolation in HCL color space — more natural chromatic transitions.\n'
                    '\n'
                    '- ["interpolate-lab", ["linear"], input, stop1, color1, ...]\n'
                    '  Interpolation in Lab color space — perceptually uniform.\n'
                    '\n'
                    '### Numeric operators\n'
                    '- ["*", a, b]  ["+", a, b]  ["-", a, b]  ["/", a, b]  ["%", a, b]\n'
                    '- ["^", base, exponent]\n'
                    '- ["abs", n]  ["ceil", n]  ["floor", n]  ["round", n]  ["sqrt", n]  ["log2", n]\n'
                    '\n'
                    '### Zoom and feature state\n'
                    '- ["zoom"]                    current zoom level (for zoom-dependent expressions)\n'
                    '- ["feature-state", "prop"]   feature state (hover, selected, etc.)\n'
                    '\n'
                    '### Color values\n'
                    '- Hex string: "#rrggbb" or "#rrggbbaa"\n'
                    '- CSS: "rgb(r,g,b)", "rgba(r,g,b,a)", "hsl(h,s%,l%)", "hsla(h,s%,l%,a)"\n'
                    '- Named: "transparent", "red", etc.\n'
                    '\n'
                    '## Paint properties by layer type\n'
                    '\n'
                    '### fill (vector polygons)\n'
                    '- fill-color          : fill color (expression or string)\n'
                    '- fill-opacity        : 0–1\n'
                    '- fill-outline-color  : border color\n'
                    '- fill-antialias      : bool\n'
                    '\n'
                    '### line (vector lines)\n'
                    '- line-color          : color\n'
                    '- line-width          : number or expression (px)\n'
                    '- line-opacity        : 0–1\n'
                    '- line-dasharray      : [dash, gap, ...]\n'
                    '- line-blur           : number\n'
                    '\n'
                    '### circle (vector points)\n'
                    '- circle-color        : color\n'
                    '- circle-radius       : number or expression\n'
                    '- circle-opacity      : 0–1\n'
                    '- circle-stroke-color / circle-stroke-width\n'
                    '\n'
                    '### symbol (text/icons vector)\n'
                    '- text-color, text-halo-color, text-halo-width, text-opacity\n'
                    '- icon-color, icon-opacity\n'
                    '\n'
                    '### raster\n'
                    '- raster-opacity      : 0–1\n'
                    '- raster-color        : color expression using ["raster-value"] as input\n'
                    '- raster-color-range  : [min, max] — value range of the raster to map onto colors\n'
                    '- raster-brightness-min / raster-brightness-max : 0–1\n'
                    '- raster-saturation   : -1–1\n'
                    '- raster-contrast     : -1–1\n'
                    '- raster-fade-duration: ms\n'
                    '\n'
                    '## Guidelines for building the style\n'
                    '\n'
                    '1. Analyze the layer metadata: for vector layers check attribute dtypes\n'
                    '   ("float64"/"int64" → continuous, "str" → categorical, "bool" → boolean).\n'
                    '   For raster layers use the real min/max and exclude the nodata value from the range.\n'
                    '\n'
                    '2. Choose the most appropriate expression for the request:\n'
                    '   - Continuous attribute + gradient → interpolate (prefer interpolate-hcl for colors)\n'
                    '   - Categorical attribute + distinct colors → match\n'
                    '   - Arbitrary conditional logic → case\n'
                    '   - Discrete class breaks → step\n'
                    '   - Raster + colormap → raster-color with raster-value and raster-color-range\n'
                    '\n'
                    '3. Use filter only when the request targets a SUBSET of features\n'
                    '   (e.g. "only tall buildings", "exclude underground"). Do not use it for coloring.\n'
                    '\n'
                    '4. Choose colors that are semantically appropriate to the request and the data\n'
                    '   (blue for water, red/orange for heat or danger, green for vegetation,\n'
                    '   multicolor terrain for DEM, etc.). If the user specifies colors, use them.\n'
                    '\n'
                    '5. For raster layers always set raster-color-range using the real min/max values\n'
                    '   (excluding the nodata value).\n'
                    '\n'
                    'Reply ONLY with the JSON. Nothing else.'
                ),
            })

    class ExecutionContext:
        """Builds the runtime context HumanMessage for MapAgent and its tools (S3).

        Responsibilities (separate from ContextPrompt):
          ContextPrompt    → SystemMessage — static agent role and tool capabilities
          ExecutionContext → HumanMessage  — dynamic snapshot of map_viewport / shapes
        """

        @staticmethod
        def stable(
            state: MABaseGraphState,
            *,
            include_shapes: bool = True,
            **kwargs,
        ) -> Prompt:
            """Serialize current map state into a Prompt.

            Args:
                state: the current graph state.
                include_shapes: when True includes the shapes_registry with metadata
                    (needed by CreateShapeTool and MapAgent full context).
            """
            parts: list[str] = []

            map_viewport = state.get("map_viewport")
            map_zoom = state.get("map_zoom")
            if map_viewport:
                parts.append(
                    f"Current map view: bounds={{minx={map_viewport[0]}, miny={map_viewport[1]}, "
                    f"maxx={map_viewport[2]}, maxy={map_viewport[3]}}}, zoom={map_zoom}"
                )
            else:
                parts.append("Current map view: unknown")

            layer_registry = state.get("layer_registry") or []
            parts.append(
                f"\nAvailable layers:\n{_format_layer_registry_summary(layer_registry)}"
            )

            if include_shapes:
                shapes = state.get("shapes_registry") or []
                if shapes:
                    parts.append(f"\nRegistered shapes ({len(shapes)}):")
                    for s in shapes:
                        geom = s.get("geometry", {})
                        gtype = geom.get("type", "?") if isinstance(geom, dict) else "?"
                        meta = (
                            s.get("metadata") or compute_geometry_metadata(geom)
                            if isinstance(geom, dict) else {}
                        )
                        line = (
                            f"  • shape_id={s.get('shape_id', '?')}  "
                            f"type={s.get('shape_type', '?')} ({gtype})"
                        )
                        if s.get("label"):
                            line += f"  label=\"{s['label']}\""
                        if isinstance(meta, dict):
                            if "bbox" in meta:
                                b = meta["bbox"]
                                line += (
                                    f"  bbox=[west={b['west']}, south={b['south']}, "
                                    f"east={b['east']}, north={b['north']}]"
                                )
                            if "lon" in meta and "lat" in meta:
                                line += f"  coords=[lon={meta['lon']}, lat={meta['lat']}]"
                            if "area_km2" in meta:
                                line += f"  area=~{meta['area_km2']} km²"
                            if "length_km" in meta:
                                line += f"  length=~{meta['length_km']} km"
                        # Full geometry with truncation for coordinate-level queries
                        coords_str = _serialize_geometry_for_context(geom)
                        line += f"  coords={coords_str}"
                        parts.append(line)
                else:
                    parts.append("\nNo shapes registered yet.")

            return Prompt({
                "title": "MapAgentExecutionContext",
                "description": "Runtime snapshot of map_viewport, layers and shapes for the MapAgent HumanMessage",
                "command": "",
                "message": "\n".join(parts),
            })


    class GenerateShapePrompt:

        @staticmethod
        def stable() -> Prompt:
            return Prompt({
                "title": "ShapeGenerator",
                "description": "LLM prompt to generate a GeoJSON geometry from a natural language request",
                "command": "",
                "message": (
                    "You are a GeoJSON geometry expert. Your only task is to produce a valid GeoJSON "
                    "geometry object that satisfies a user's spatial request.\n"
                    "\n"
                    "## Input you receive\n"
                    "\n"
                    "- Current map view: bounding box [west, south, east, north] and zoom level "
                    "(use this as spatial context when the user does not specify exact coordinates)\n"
                    "- User request: a natural language description of what shape to create\n"
                    "\n"
                    "## Expected output\n"
                    "\n"
                    "Reply ONLY with a GeoJSON geometry object (no markdown, no explanations):\n"
                    "{\n"
                    '  "type": "Polygon" | "MultiPolygon" | "LineString" | "MultiLineString" | "Point",\n'
                    '  "coordinates": [ ... ]\n'
                    "}\n"
                    "\n"
                    "## Guidelines\n"
                    "\n"
                    "1. Use EPSG:4326 (WGS84) coordinates [longitude, latitude].\n"
                    "2. If the user names a well-known place (city, landmark, river, etc.), use your "
                    "geographic knowledge to generate plausible coordinates.\n"
                    "3. If the user provides no specific location and the map viewport is available, "
                    "generate a shape centered within the visible bounding box.\n"
                    "4. Choose the geometry type that best matches the request:\n"
                    "   - Area / region / bbox → Polygon\n"
                    "   - Path / route / river / road → LineString\n"
                    "   - Single location / marker → Point\n"
                    "5. Polygons must be closed (first and last coordinate identical).\n"
                    "6. Keep coordinate precision to 5 decimal places.\n"
                    "\n"
                    "Reply ONLY with the GeoJSON geometry object. Nothing else."
                ),
            })

    class GenerateMoveViewPrompt:

        @staticmethod
        def stable() -> Prompt:
            return Prompt({
                "title": "MoveViewGenerator",
                "description": "LLM prompt to generate a map viewport payload from a natural language request",
                "command": "",
                "message": (
                    "You are a geographic assistant. Your only task is to produce a JSON viewport"
                    " object that describes where to move a web map based on a user request.\n"
                    "\n"
                    "## Input you receive\n"
                    "\n"
                    "- Current map view: bounds [west, south, east, north] and zoom level\n"
                    "- User request: natural language description of the desired location/view\n"
                    "\n"
                    "## Expected output\n"
                    "\n"
                    "Reply ONLY with a JSON object (no markdown, no explanations) in ONE of these two forms:\n"
                    "\n"
                    "1. Point + zoom:\n"
                    '{\n'
                    '  "center": [longitude, latitude],\n'
                    '  "zoom": <number 1-18>\n'
                    '}\n'
                    "\n"
                    "2. Bounding box (use when the user names a region, country, or river basin):\n"
                    '{\n'
                    '  "bbox": { "west": <lon>, "south": <lat>, "east": <lon>, "north": <lat> }\n'
                    '}\n'
                    "\n"
                    "## Guidelines\n"
                    "\n"
                    "- Use EPSG:4326 (WGS84) coordinates: longitude first, then latitude.\n"
                    "- Use your geographic knowledge to produce accurate coordinates for named places.\n"
                    "- Prefer bbox for countries, regions, and river basins; prefer center+zoom for cities and landmarks.\n"
                    "- For zoom, use: 4-5 continent, 6-7 country, 8-9 region, 10-11 city, 12-13 district, 14-16 street.\n"
                    "- Keep coordinate precision to 4 decimal places.\n"
                    "\n"
                    "Reply ONLY with the JSON object. Nothing else."
                ),
            })

    def _viewport_context(state: MABaseGraphState) -> str:
        if state.get("map_viewport") is None:
            return "No map viewport available."
        viewport = state["map_viewport"]
        zoom = state.get("map_zoom", "unknown")
        return f"Current map view: bounds={{minx={viewport[0]}, miny={viewport[1]}, maxx={viewport[2]}, maxy={viewport[3]}}}, zoom={zoom}"

# ---------------------------------------------------------------------------
# Module-level helpers (used by ExecutionContext.stable)
# ---------------------------------------------------------------------------



def _format_layer_registry_summary(layer_registry: list) -> str:
    """Return a concise bullet-list summary of the layer registry."""
    if not layer_registry:
        return "No layers available."
    lines = []
    for layer in layer_registry:
        title = layer.get("title", "unknown")
        ltype = layer.get("type", "?")
        src = layer.get("src", "")
        line = f"- {title} (type: {ltype}, src: {src})"
        meta = layer.get("metadata") or {}
        attrs = meta.get("attributes")
        if attrs:
            attr_names = ", ".join(str(k) for k in attrs.keys())
            line += f"\n  attributes: {attr_names}"
        lines.append(line)
    return "\n".join(lines)


_MAX_COORDS_PER_RING = 50


def _serialize_geometry_for_context(geom: dict) -> str:
    """Serialize a GeoJSON geometry for LLM context with truncation.

    - Point: returns [lon, lat]
    - LineString / Polygon ring with ≤ 50 points: returns full coordinate JSON
    - LineString / Polygon ring with > 50 points: returns bbox + count summary
    - MultiPolygon / MultiLineString: returns bbox + feature count
    """
    if not isinstance(geom, dict):
        return "?"
    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if coords is None:
        return "?"

    if gtype == "Point":
        return f"[lon={coords[0]}, lat={coords[1]}]"

    if gtype == "LineString":
        if len(coords) <= _MAX_COORDS_PER_RING:
            return json.dumps(coords)
        west = min(p[0] for p in coords)
        east = max(p[0] for p in coords)
        south = min(p[1] for p in coords)
        north = max(p[1] for p in coords)
        return (
            f"(truncated — {len(coords)} points, "
            f"bbox=[W={west:.4f}, S={south:.4f}, E={east:.4f}, N={north:.4f}])"
        )

    if gtype == "Polygon":
        outer = coords[0] if coords else []
        total_pts = sum(len(ring) for ring in coords)
        if total_pts <= _MAX_COORDS_PER_RING:
            return json.dumps(coords)
        west = min(p[0] for p in outer)
        east = max(p[0] for p in outer)
        south = min(p[1] for p in outer)
        north = max(p[1] for p in outer)
        return (
            f"(truncated — outer ring has {len(outer)} points, "
            f"bbox=[W={west:.4f}, S={south:.4f}, E={east:.4f}, N={north:.4f}])"
        )

    if gtype in ("MultiPolygon", "MultiLineString", "MultiPoint", "GeometryCollection"):
        all_pts: list = []
        if gtype == "MultiPoint":
            all_pts = list(coords)
        elif gtype == "MultiLineString":
            all_pts = [p for line in coords for p in line]
        elif gtype == "MultiPolygon":
            all_pts = [p for poly in coords for ring in poly for p in ring]
        if all_pts:
            west = min(p[0] for p in all_pts)
            east = max(p[0] for p in all_pts)
            south = min(p[1] for p in all_pts)
            north = max(p[1] for p in all_pts)
            bbox_str = f"bbox=[W={west:.4f}, S={south:.4f}, E={east:.4f}, N={north:.4f}]"
        else:
            bbox_str = "bbox=unknown"
        return f"(multi-geometry, {len(coords)} features, {bbox_str})"

    return json.dumps(coords)


# ---------------------------------------------------------------------------
# MapAgentInstructions — structured prompt hierarchy (InvokeOneShot pattern)
# ---------------------------------------------------------------------------

from langchain_core.messages import SystemMessage as _SystemMessage


class MapAgentInstructions:

    class InvokeTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return MapAgentPrompts.ContextPrompt.stable()

            class _ExecutionContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return MapAgentPrompts.ExecutionContext.stable(state)

            class _Request:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    request = state.get("map_request")
                    if not request:
                        # DOC: When invoked from a supervisor plan, map_request is None;
                        # use the current plan step goal as the request.
                        plan = state.get("plan") or []
                        current_step = state.get("current_step") or 0
                        if plan and current_step < len(plan):
                            request = plan[current_step]["goal"]
                    return Prompt(dict(
                        header="[REQUEST]",
                        message=str(request or ""),
                    ))

        class Invocation:

            class InvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role = MapAgentInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
                    ctx = MapAgentInstructions.InvokeTools.Prompts._ExecutionContext.stable(state)
                    req = MapAgentInstructions.InvokeTools.Prompts._Request.stable(state)

                    system_message = _SystemMessage(content=role.message)
                    human_content = (
                        f"{ctx.message}\n\n"
                        f"[REQUEST]\n{req.message}\n"
                    )
                    from langchain_core.messages import HumanMessage as _HumanMessage
                    return [system_message, _HumanMessage(content=human_content)]
