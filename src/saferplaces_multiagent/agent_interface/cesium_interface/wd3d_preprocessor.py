#!/usr/bin/env python3
"""
CesiumFlood Preprocessor

Offline preprocessing pipeline that converts GeoTIFF flood depth rasters
into CBOR mesh data for the CesiumFlood 3D viewer. Performs medial axis
skeleton generation, constrained Delaunay triangulation, depth sampling,
and skeleton-based mesh segmentation.

Usage:
    python preprocess.py flood.tif [--output PATH] [--depth-threshold 0.05]
        [--min-distance 50] [--target-area 2.25e-7] [--no-skeleton]
        [--viz] [--gzip] [--verbose]


Requirements:
    [
        "pyproj",
        "rasterio>=1.3",
        "shapely>=2.0",
        "scikit-image>=0.21",
        "contourpy>=1.2",
        "scipy>=1.10",
        "networkx>=3.0",
        "numpy>=1.24",
        "triangle>=20220202",
        "cbor2>=5.6",
        "matplotlib>=3.7",
        "contextily>=1.4"
    ]
"""

import os
import argparse
import gzip
import logging
import math
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cbor2
# import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import rasterio
import shapely
import triangle as tr
from cbor2 import CBORTag
from contourpy import FillType, contour_generator
from matplotlib.collections import LineCollection
from pyproj import Transformer
from scipy.ndimage import label as ndimage_label, map_coordinates
from scipy.spatial import KDTree
from shapely.geometry import MultiPolygon, Polygon
from skimage.morphology import medial_axis

# =============================================================================
# Constants (matching JS codebase)
# =============================================================================

WATER_THRESHOLD = 0.05              # meters — depth threshold
POLYGON_AREA_THRESHOLD = 2e-8       # deg^2 (~200m^2) — large vs small polygon
MESH_SIMPLIFY_TOLERANCE = 6.74e-6    # deg (~0.75m) — mesh boundary simplification
MIN_RADIUS_THRESHOLD = 1e-10        # filter boundary skeleton points
MAX_SEGMENT_LENGTH = 0.00025        # deg (~25m) — split long skeleton segments
MAX_BONE_LENGTH = 0.001             # deg (~100m) — bone chain length limit
POISSON_MIN_DISTANCE = 50           # meters — controls mesh density
TARGET_SEGMENT_AREA = 2.25e-7       # deg^2 (~225m^2) — merge threshold
MIN_SEGMENT_AREA = 5e-8             # deg^2 (~50m^2) — tiny segment merge
SEED_SEARCH_RADIUS = 0.0002         # deg — triangle seeding search box

# CBOR RFC 8746 typed array tags (little-endian)
CBOR_TAG_UINT32_LE = 70
CBOR_TAG_INT32_LE = 78
CBOR_TAG_FLOAT32_LE = 85
CBOR_TAG_FLOAT64_LE = 86

# NODATA detection constants (matching JS isNoDataValue)
NODATA_KNOWN_VALUES = {0, -9999, -9999.0}
NODATA_ABS_THRESHOLD = 1e10

log = logging.getLogger("preprocess")


# =============================================================================
# 1. GeoTIFF Parsing
# =============================================================================

def parse_geotiff(path: str) -> dict:
    """Read GeoTIFF, detect CRS, transform bbox to WGS84."""
    log.info(f"Opening {path}")
    with rasterio.open(path) as ds:
        data = ds.read(1).astype(np.float32)
        width, height = ds.width, ds.height
        nodata = ds.nodata
        bounds = ds.bounds
        src_crs = ds.crs

        log.info(f"  Raster: {width}x{height}, CRS: {src_crs}, nodata: {nodata}")

        if src_crs is None or src_crs.to_epsg() == 4326:
            bbox_wgs84 = {
                "minLon": bounds.left, "minLat": bounds.bottom,
                "maxLon": bounds.right, "maxLat": bounds.top,
            }
            crs_info = {"epsg": 4326, "name": "WGS84", "isWGS84": True}
        else:
            transformer = Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
            x_coords = [bounds.left, bounds.right, bounds.left, bounds.right]
            y_coords = [bounds.bottom, bounds.bottom, bounds.top, bounds.top]
            lons, lats = transformer.transform(x_coords, y_coords)
            bbox_wgs84 = {
                "minLon": min(lons), "minLat": min(lats),
                "maxLon": max(lons), "maxLat": max(lats),
            }
            epsg = src_crs.to_epsg()
            crs_info = {
                "epsg": epsg if epsg else 0,
                "name": str(src_crs),
                "isWGS84": False,
            } 
        
        clean = data.copy()
        mask = ~np.isfinite(clean)
        if nodata is not None:
            mask |= clean == nodata
        for v in NODATA_KNOWN_VALUES:
            mask |= clean == v
        mask |= np.abs(clean) > NODATA_ABS_THRESHOLD
        clean[mask] = 0.0

        log.info(f"  BBox WGS84: ({bbox_wgs84['minLon']:.6f}, {bbox_wgs84['minLat']:.6f}) - "
                 f"({bbox_wgs84['maxLon']:.6f}, {bbox_wgs84['maxLat']:.6f})")
        log.info(f"  Depth range: {clean[clean > 0].min():.3f} - {clean.max():.3f} m"
                 if np.any(clean > 0) else "  No water found")

    return {
        "data": data, "clean_data": clean,
        "width": width, "height": height,
        "bbox_wgs84": bbox_wgs84, "nodata": nodata,
        "crs_info": crs_info,
        "original_bounds": {"left": bounds.left, "bottom": bounds.bottom,
                            "right": bounds.right, "top": bounds.top},
    }

# =============================================================================
# 2. Contour Extraction
# =============================================================================


def _contourpy_to_polygons(filled_result, bbox, width, height, divisor_w, divisor_h):
    """Convert contourpy OuterOffset filled result to Shapely Polygons in WGS84.

    OuterOffset filled() returns a tuple of 2 lists:
      - points_list: list of numpy arrays, one per polygon group
      - offsets_list: list of numpy arrays, offsets into each points array
    for outer ring and hole boundaries
    """
    points_list, offsets_list = filled_result
    min_lon, max_lon = bbox["minLon"], bbox["maxLon"]
    min_lat, max_lat = bbox["minLat"], bbox["maxLat"]
    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    polygons = []
    for pts, offsets in zip(points_list, offsets_list):
        # Each (pts, offsets) pair is one polygon group
        # offsets delineate the outer ring and holes within pts
        rings_wgs84 = []
        for k in range(len(offsets) - 1):
            ring_start = int(offsets[k])
            ring_end = int(offsets[k + 1])
            ring_pts = pts[ring_start:ring_end]
            if len(ring_pts) < 4:
                continue
            # Convert pixel coords to WGS84
            lons = min_lon + (ring_pts[:, 0] / divisor_w) * lon_range
            lats = max_lat - (ring_pts[:, 1] / divisor_h) * lat_range
            coords = list(zip(lons.tolist(), lats.tolist()))
            rings_wgs84.append(coords)

        if not rings_wgs84:
            continue

        outer = rings_wgs84[0]
        holes = rings_wgs84[1:]
        try:
            poly = Polygon(outer, holes)
            if poly.is_valid and poly.area > 0:
                polygons.append(poly)
        except Exception:
            pass

    return polygons


def extract_smooth_contours(raster: dict, depth_threshold: float = WATER_THRESHOLD) -> list:
    """Extract smooth water polygons for skeleton generation.
    Ref: skeletonGenerator.ts:extractSmoothPolygons()
    """
    clean = raster["clean_data"]
    w, h = raster["width"], raster["height"]
    bbox = raster["bbox_wgs84"]

    gen = contour_generator(
        x=np.arange(w, dtype=np.float64),
        y=np.arange(h, dtype=np.float64),
        z=clean,
        fill_type=FillType.OuterOffset,
    )
    upper = float(clean.max()) + 1.0
    filled = gen.filled(depth_threshold, upper)

    # Smooth contours use divisor = width/height (matching JS: x / width)
    polygons = _contourpy_to_polygons(filled, bbox, w, h, w, h)
    log.info(f"  Extracted {len(polygons)} smooth contour polygons")
    return polygons


def extract_binary_contours(raster: dict, depth_threshold: float = WATER_THRESHOLD) -> list:
    """Extract binary water boundary polygons for mesh generation.
    Ref: meshGenerator.ts:extractWaterPolygons()
    """
    clean = raster["clean_data"]
    w, h = raster["width"], raster["height"]
    bbox = raster["bbox_wgs84"]

    binary = np.where(clean >= depth_threshold, 1.0, 0.0)
    gen = contour_generator(
        x=np.arange(w, dtype=np.float64),
        y=np.arange(h, dtype=np.float64),
        z=binary,
        fill_type=FillType.OuterOffset,
    )
    filled = gen.filled(0.5, 1.5)

    # Binary contours use divisor = width-1/height-1 (matching JS: x / (width-1))
    polygons = _contourpy_to_polygons(filled, bbox, w, h, w - 1, h - 1)

    simplified = []
    for poly in polygons:
        s = poly.simplify(MESH_SIMPLIFY_TOLERANCE, preserve_topology=True)
        if s.is_valid and s.area > 0:
            if isinstance(s, MultiPolygon):
                simplified.extend(s.geoms)
            else:
                simplified.append(s)

    log.info(f"  Extracted {len(simplified)} binary contour polygons")
    return simplified


# =============================================================================
# 3. Polygon Classification
# =============================================================================

def classify_polygons_by_area(polygons: list) -> tuple:
    """Split polygons into large (skeleton) and small (centroid)."""
    large, small = [], []
    for i, poly in enumerate(polygons):
        area = poly.area
        entry = (poly, area, i)
        if area >= POLYGON_AREA_THRESHOLD:
            large.append(entry)
        else:
            small.append(entry)
    log.info(f"  Classified: {len(large)} large, {len(small)} small polygons")
    return large, small


# =============================================================================
# 4. Skeleton Generation (shapely-polyskel)
# =============================================================================

def compute_raster_mat(raster: dict, depth_threshold: float = WATER_THRESHOLD) -> tuple:
    """Compute Medial Axis Transform on the binary water mask.

    Uses skimage.morphology.medial_axis — a true MAT that naturally stays
    inside water regions and respects holes. Replaces per-polygon straight
    skeleton which had issues with hole handling.

    Returns:
        (skeleton_segments, centroids) matching the format expected by
        process_skeleton_segments() and partition_into_bones().
    """
    clean = raster["clean_data"]
    bbox = raster["bbox_wgs84"]
    w, h = raster["width"], raster["height"]

    binary = clean >= depth_threshold

    labeled, num_components = ndimage_label(binary)
    log.info(f"  Found {num_components} connected water regions")

    pixel_area_deg2 = ((bbox["maxLon"] - bbox["minLon"]) / w) * \
                      ((bbox["maxLat"] - bbox["minLat"]) / h)
    component_sizes = np.bincount(labeled.ravel())  # index 0 = background

    large_ids = set()
    small_ids = set()
    for comp_id in range(1, num_components + 1):
        area = component_sizes[comp_id] * pixel_area_deg2
        if area >= POLYGON_AREA_THRESHOLD:
            large_ids.add(comp_id)
        else:
            small_ids.add(comp_id)

    log.info(f"  Large: {len(large_ids)}, Small: {len(small_ids)} water regions")

    skel, dist = medial_axis(binary, return_distance=True)

    px_size_lon = (bbox["maxLon"] - bbox["minLon"]) / w
    px_size_lat = (bbox["maxLat"] - bbox["minLat"]) / h
    px_to_deg = (px_size_lon + px_size_lat) / 2.0

    def px_to_lonlat(col, row):
        lon = bbox["minLon"] + (col / w) * (bbox["maxLon"] - bbox["minLon"])
        lat = bbox["maxLat"] - (row / h) * (bbox["maxLat"] - bbox["minLat"])
        return lon, lat

    skel_yx = np.argwhere(skel)
    if len(skel_yx) == 0:
        log.info("  MAT: no skeleton pixels")
        return [], []

    skel_components = labeled[skel_yx[:, 0], skel_yx[:, 1]]
    large_mask = np.isin(skel_components, list(large_ids))
    large_skel_yx = skel_yx[large_mask]

    log.info(f"  MAT: {len(large_skel_yx)} skeleton pixels from large regions")

    px_set = set()
    px_to_idx = {}
    for i, (y, x) in enumerate(large_skel_yx.tolist()):
        px_set.add((y, x))
        px_to_idx[(y, x)] = i

    skel_lons = np.empty(len(large_skel_yx), dtype=np.float64)
    skel_lats = np.empty(len(large_skel_yx), dtype=np.float64)
    skel_radii = np.empty(len(large_skel_yx), dtype=np.float64)
    for i, (y, x) in enumerate(large_skel_yx.tolist()):
        skel_lons[i], skel_lats[i] = px_to_lonlat(x, y)
        skel_radii[i] = dist[y, x] * px_to_deg

    segments = []
    visited = set()
    neighbors_8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    for idx, (y, x) in enumerate(large_skel_yx.tolist()):
        for dy, dx in neighbors_8:
            ny, nx = y + dy, x + dx
            if (ny, nx) in px_set:
                edge = (min((y, x), (ny, nx)), max((y, x), (ny, nx)))
                if edge not in visited:
                    visited.add(edge)
                    nidx = px_to_idx[(ny, nx)]
                    segments.append({
                        "p0": {"x": float(skel_lons[idx]), "y": float(skel_lats[idx]),
                               "radius": float(skel_radii[idx])},
                        "p1": {"x": float(skel_lons[nidx]), "y": float(skel_lats[nidx]),
                               "radius": float(skel_radii[nidx])},
                    })

    log.info(f"  MAT: {len(segments)} skeleton segments")

    centroids = []
    if small_ids:
        from scipy.ndimage import center_of_mass
        small_list = sorted(small_ids)
        com = center_of_mass(binary, labeled, small_list)
        for cy, cx in com:
            lon, lat = px_to_lonlat(cx, cy)
            centroids.append({"x": lon, "y": lat, "type": "centroid"})

    log.info(f"  Centroids: {len(centroids)} from small regions")
    return segments, centroids


# =============================================================================
# 5. Skeleton Segment Processing
# =============================================================================

def process_skeleton_segments(segments: list) -> tuple:
    """Filter boundary segments, split long segments, collect unique nodes.
    Ref: skeletonGenerator.ts:processSkeletonSegments()
    """
    filtered = [s for s in segments
                if s["p0"]["radius"] > MIN_RADIUS_THRESHOLD
                and s["p1"]["radius"] > MIN_RADIUS_THRESHOLD]

    processed = []
    for seg in filtered:
        dx = seg["p1"]["x"] - seg["p0"]["x"]
        dy = seg["p1"]["y"] - seg["p0"]["y"]
        length = math.sqrt(dx * dx + dy * dy)

        if length <= MAX_SEGMENT_LENGTH:
            processed.append(seg)
        else:
            n = math.ceil(length / MAX_SEGMENT_LENGTH)
            for i in range(n):
                t0 = i / n
                t1 = (i + 1) / n
                processed.append({
                    "p0": {
                        "x": seg["p0"]["x"] + t0 * dx,
                        "y": seg["p0"]["y"] + t0 * dy,
                        "radius": seg["p0"]["radius"] + t0 * (seg["p1"]["radius"] - seg["p0"]["radius"]),
                    },
                    "p1": {
                        "x": seg["p0"]["x"] + t1 * dx,
                        "y": seg["p0"]["y"] + t1 * dy,
                        "radius": seg["p0"]["radius"] + t1 * (seg["p1"]["radius"] - seg["p0"]["radius"]),
                    },
                })

    # Collect unique nodes (dedup by 8 decimals, matching JS toFixed(8))
    node_map = {}
    for seg in processed:
        for endpoint in ("p0", "p1"):
            p = seg[endpoint]
            key = f"{p['x']:.8f},{p['y']:.8f}"
            if key not in node_map:
                node_map[key] = {
                    "x": p["x"], "y": p["y"],
                    "radius": p["radius"], "type": "skeleton",
                }

    nodes = list(node_map.values())
    log.info(f"  Processed: {len(processed)} segments, {len(nodes)} unique nodes")
    return processed, nodes


# =============================================================================
# 6. Skeleton Graph
# =============================================================================

def pos_key(x: float, y: float) -> str:
    """Position key matching JS posKey(): toFixed(7)."""
    return f"{x:.7f},{y:.7f}"


def build_skeleton_graph(segments: list) -> tuple:
    """Build NetworkX undirected graph from skeleton segments.
    Ref: bonePartitioner.ts
    """
    G = nx.Graph()
    nodes_map = {}

    for seg in segments:
        p0, p1 = seg["p0"], seg["p1"]
        k0 = pos_key(p0["x"], p0["y"])
        k1 = pos_key(p1["x"], p1["y"])

        if not G.has_node(k0):
            G.add_node(k0)
            nodes_map[k0] = {"x": p0["x"], "y": p0["y"], "radius": p0["radius"]}
        if not G.has_node(k1):
            G.add_node(k1)
            nodes_map[k1] = {"x": p1["x"], "y": p1["y"], "radius": p1["radius"]}

        if not G.has_edge(k0, k1):
            dx = p0["x"] - p1["x"]
            dy = p0["y"] - p1["y"]
            G.add_edge(k0, k1, length=math.sqrt(dx * dx + dy * dy))

    log.info(f"  Skeleton graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G, nodes_map


def simplify_skeleton_graph(G: nx.Graph, nodes_map: dict,
                            resplit_length: float = MAX_SEGMENT_LENGTH) -> tuple:
    """Collapse degree-2 chains in the skeleton graph.

    The pixel-resolution MAT creates a node per skeleton pixel (~94K nodes,
    ~100K edges).  This function removes all degree-2 "pass-through" nodes,
    keeping only junctions (degree >= 3) and endpoints (degree 1).
    Optionally re-splits long edges so bone partitioning still has
    intermediate sample points.

    Returns (simplified_graph, simplified_nodes_map).
    """
    key_nodes = {n for n in G.nodes() if G.degree(n) != 2}

    if not key_nodes:
        # Pure cycle — keep one arbitrary node as anchor
        if G.number_of_nodes() > 0:
            key_nodes.add(next(iter(G.nodes())))
        else:
            return G, nodes_map

    new_G = nx.Graph()
    for n in key_nodes:
        new_G.add_node(n)

    visited = set()

    for start in key_nodes:
        for neighbor in G.neighbors(start):
            ek = frozenset((start, neighbor))
            if ek in visited:
                continue

            # Walk the degree-2 chain
            chain = [start]
            cur = neighbor
            total_len = 0.0

            while True:
                prev = chain[-1]
                total_len += G[prev][cur]["length"]
                visited.add(frozenset((prev, cur)))
                chain.append(cur)

                if cur in key_nodes:
                    break  # reached another junction/endpoint

                nbs = [n for n in G.neighbors(cur) if n != prev]
                if not nbs:
                    # dead end not in key_nodes (shouldn't happen, but be safe)
                    key_nodes.add(cur)
                    new_G.add_node(cur)
                    break
                cur = nbs[0]

            end = chain[-1]
            if start != end and not new_G.has_edge(start, end):
                new_G.add_edge(start, end, length=total_len, chain=chain)

    # Re-split long edges by inserting intermediate nodes
    edges_to_split = [(u, v, d) for u, v, d in new_G.edges(data=True)
                      if d["length"] > resplit_length and len(d.get("chain", [])) > 2]

    for u, v, data in edges_to_split:
        chain = data["chain"]
        # Subsample the chain to keep roughly one node per resplit_length
        n_keep = max(2, int(round(data["length"] / resplit_length)))
        step = max(1, len(chain) // n_keep)
        sub = chain[::step]
        if sub[-1] != chain[-1]:
            sub.append(chain[-1])

        new_G.remove_edge(u, v)
        for i in range(len(sub) - 1):
            a, b = sub[i], sub[i + 1]
            if not new_G.has_node(a):
                new_G.add_node(a)
            if not new_G.has_node(b):
                new_G.add_node(b)
            if not new_G.has_edge(a, b):
                pa, pb = nodes_map[a], nodes_map[b]
                dx = pa["x"] - pb["x"]
                dy = pa["y"] - pb["y"]
                new_G.add_edge(a, b, length=math.sqrt(dx * dx + dy * dy))

    # Build simplified nodes_map
    new_nodes_map = {n: nodes_map[n] for n in new_G.nodes() if n in nodes_map}

    log.info(f"  Simplified skeleton: {G.number_of_nodes()} → {new_G.number_of_nodes()} nodes, "
             f"{G.number_of_edges()} → {new_G.number_of_edges()} edges")

    return new_G, new_nodes_map


# =============================================================================
# 7. Bone Partitioning
# =============================================================================

def partition_into_bones(graph, nodes_map: dict, centroids: list) -> list:
    """Partition skeleton graph into bone chains.
    Ref: bonePartitioner.ts:partitionSkeletonIntoBones()
    """
    bones = []
    next_bone_id = 0

    break_points = set()
    for node in graph.nodes():
        if graph.degree(node) != 2:
            break_points.add(node)
    if not break_points and graph.number_of_nodes() > 0:
        break_points.add(list(graph.nodes())[0])

    visited_edges = set()

    for root in break_points:
        for neighbor in graph.neighbors(root):
            edge_key = frozenset((root, neighbor))
            if edge_key in visited_edges:
                continue

            bone_segments = []
            curr = neighbor
            prev = root
            accumulated_len = 0.0

            visited_edges.add(edge_key)
            p0 = nodes_map[prev]
            p1 = nodes_map[curr]
            bone_segments.append({"p0": p0, "p1": p1})
            dx = p0["x"] - p1["x"]
            dy = p0["y"] - p1["y"]
            accumulated_len += math.sqrt(dx * dx + dy * dy)

            while graph.degree(curr) == 2 and accumulated_len < MAX_BONE_LENGTH:
                neighbors = list(graph.neighbors(curr))
                nxt = None
                for n in neighbors:
                    if n != prev:
                        nxt = n
                        break
                if nxt is None:
                    break

                next_edge_key = frozenset((curr, nxt))
                if next_edge_key in visited_edges:
                    break

                visited_edges.add(next_edge_key)
                curr_pt = nodes_map[curr]
                next_pt = nodes_map[nxt]
                bone_segments.append({"p0": curr_pt, "p1": next_pt})
                dx = curr_pt["x"] - next_pt["x"]
                dy = curr_pt["y"] - next_pt["y"]
                accumulated_len += math.sqrt(dx * dx + dy * dy)

                prev = curr
                curr = nxt

            if bone_segments:
                sample_pts = [bone_segments[0]["p0"]]
                min_x = min_y = float("inf")
                max_x = max_y = float("-inf")
                for seg in bone_segments:
                    sample_pts.append(seg["p1"])
                    for p in (seg["p0"], seg["p1"]):
                        min_x = min(min_x, p["x"])
                        min_y = min(min_y, p["y"])
                        max_x = max(max_x, p["x"])
                        max_y = max(max_y, p["y"])

                bones.append({
                    "id": next_bone_id, "type": "skeleton",
                    "segments": bone_segments, "samplePoints": sample_pts,
                    "minX": min_x, "minY": min_y, "maxX": max_x, "maxY": max_y,
                })
                next_bone_id += 1

    # Handle unvisited edges (cycles)
    for u, v in graph.edges():
        edge_key = frozenset((u, v))
        if edge_key not in visited_edges:
            visited_edges.add(edge_key)
            p0 = nodes_map[u]
            p1 = nodes_map[v]
            bones.append({
                "id": next_bone_id, "type": "skeleton",
                "segments": [{"p0": p0, "p1": p1}],
                "samplePoints": [p0, p1],
                "minX": min(p0["x"], p1["x"]), "minY": min(p0["y"], p1["y"]),
                "maxX": max(p0["x"], p1["x"]), "maxY": max(p0["y"], p1["y"]),
            })
            next_bone_id += 1

    # Add centroid bones
    for c in centroids:
        bones.append({
            "id": next_bone_id, "type": "centroid",
            "segments": [],
            "samplePoints": [{"x": c["x"], "y": c["y"]}],
            "minX": c["x"] - 0.00001, "minY": c["y"] - 0.00001,
            "maxX": c["x"] + 0.00001, "maxY": c["y"] + 0.00001,
        })
        next_bone_id += 1

    log.info(f"  Bones: {len(bones)} ({sum(1 for b in bones if b['type'] == 'skeleton')} skeleton, "
             f"{sum(1 for b in bones if b['type'] == 'centroid')} centroid)")
    return bones


# =============================================================================
# 8. PSLG Construction
# =============================================================================

def build_pslg(mesh_polygons: list, skeleton_nodes: list, centroids: list,
               bbox_wgs84: dict, min_distance: float = POISSON_MIN_DISTANCE) -> dict:
    """Build PSLG for triangle library with injected skeleton/centroid vertices."""
    log.info(f"  Building PSLG from {len(mesh_polygons)} polygons, "
             f"{len(skeleton_nodes)} skeleton nodes, {len(centroids)} centroids...")
    all_vertices = []
    all_segments = []
    all_holes = []
    vertex_offset = 0

    # STRtree for finding water islands nested inside holes
    poly_tree = shapely.STRtree(mesh_polygons)

    for poly_idx, poly in enumerate(mesh_polygons):
        ext_coords = list(poly.exterior.coords)
        if ext_coords[-1] == ext_coords[0]:
            ext_coords = ext_coords[:-1]
        n_ext = len(ext_coords)
        for coord in ext_coords:
            all_vertices.append([coord[0], coord[1]])
        for i in range(n_ext):
            all_segments.append([vertex_offset + i, vertex_offset + (i + 1) % n_ext])
        vertex_offset += n_ext

        for interior in poly.interiors:
            hole_coords = list(interior.coords)
            if hole_coords[-1] == hole_coords[0]:
                hole_coords = hole_coords[:-1]
            n_hole = len(hole_coords)
            if n_hole < 3:
                continue
            for coord in hole_coords:
                all_vertices.append([coord[0], coord[1]])
            for i in range(n_hole):
                all_segments.append([vertex_offset + i, vertex_offset + (i + 1) % n_hole])
            vertex_offset += n_hole

            # Hole interior point — must land on dry land, not inside a
            # nested water island.  Subtract any water polygons contained
            # within this hole so representative_point() hits dry ground.
            hole_poly = Polygon(interior)
            dry_region = hole_poly
            candidates = poly_tree.query(hole_poly, predicate="contains")
            for ci in candidates:
                if ci != poly_idx:
                    dry_region = dry_region.difference(mesh_polygons[ci])
            if dry_region.is_empty:
                # Entire hole is water — skip hole point (triangle will
                # naturally triangulate the interior via the nested polygon)
                continue
            rep = dry_region.representative_point()
            all_holes.append([rep.x, rep.y])

    # Inject skeleton nodes and centroid points as unconstrained vertices
    injected = 0
    for pt in skeleton_nodes:
        all_vertices.append([pt["x"], pt["y"]])
        injected += 1
    for pt in centroids:
        all_vertices.append([pt["x"], pt["y"]])
        injected += 1

    log.info(f"  PSLG: {len(all_vertices)} vertices ({injected} injected), "
             f"{len(all_segments)} segments, {len(all_holes)} holes")

    # Compute max_area for quality meshing
    avg_lat = (bbox_wgs84["minLat"] + bbox_wgs84["maxLat"]) / 2
    m_per_deg_lon = 111320 * math.cos(math.radians(avg_lat))
    m_per_deg_lat = 111320
    min_dist_lon = min_distance / m_per_deg_lon
    min_dist_lat = min_distance / m_per_deg_lat
    max_area = 0.5 * min_dist_lon * min_dist_lat

    pslg = {
        "vertices": np.array(all_vertices, dtype=np.float64),
        "segments": np.array(all_segments, dtype=np.int32),
        "max_area": max_area,
    }
    if all_holes:
        pslg["holes"] = np.array(all_holes, dtype=np.float64)

    return pslg


# =============================================================================
# 9. Triangulation
# =============================================================================

def quality_triangulate(pslg: dict) -> tuple:
    """Run triangle library quality meshing.
    Replaces JS cdt2d + clean-pslg + Poisson disk sampling.
    """
    max_area = pslg["max_area"]
    tri_input = {
        "vertices": pslg["vertices"],
        "segments": pslg["segments"],
    }
    if "holes" in pslg:
        tri_input["holes"] = pslg["holes"]

    flags = f"pDq20a{max_area:.15e}"
    log.info(f"  Triangle flags: {flags}")

    try:
        result = tr.triangulate(tri_input, flags)
    except RuntimeError as e:
        log.warning(f"  Triangle failed with quality flags, retrying relaxed: {e}")
        try:
            flags = f"pDq15a{max_area * 4:.15e}"
            result = tr.triangulate(tri_input, flags)
        except RuntimeError as e2:
            log.warning(f"  Relaxed also failed, trying minimal: {e2}")
            result = tr.triangulate(tri_input, "pD")

    vertices = result["vertices"]
    triangles = result["triangles"]

    log.info(f"  Triangulation: {len(vertices)} vertices, {len(triangles)} triangles")
    return vertices, triangles


# =============================================================================
# 10. Depth Sampling
# =============================================================================

def sample_depths(vertices: np.ndarray, raster: dict) -> np.ndarray:
    """Sample water depth at each vertex via bilinear interpolation.
    Ref: meshGenerator.ts:bilinearDepthSample()
    """
    clean = raster["clean_data"]
    bbox = raster["bbox_wgs84"]
    w, h = raster["width"], raster["height"]

    lons = vertices[:, 0]
    lats = vertices[:, 1]

    # Convert lon/lat to pixel coords (matching JS: x / (width-1))
    px = ((lons - bbox["minLon"]) / (bbox["maxLon"] - bbox["minLon"])) * (w - 1)
    py = ((bbox["maxLat"] - lats) / (bbox["maxLat"] - bbox["minLat"])) * (h - 1)

    # Clamp to valid range
    px = np.clip(px, 0, w - 1)
    py = np.clip(py, 0, h - 1)

    # Bilinear interpolation using scipy
    depths = map_coordinates(clean, [py, px], order=1, mode="nearest")
    depths = np.maximum(depths, 0).astype(np.float32)

    log.info(f"  Depths: range [{depths[depths > 0].min():.3f}, {depths.max():.3f}] m"
             if np.any(depths > 0) else "  Depths: all zero")
    return depths


# =============================================================================
# 11. Union-Find
# =============================================================================

class UnionFind:
    """Disjoint set with path compression and union by rank."""

    def __init__(self, size: int):
        self.parent = np.arange(size, dtype=np.int32)
        self.rank = np.zeros(size, dtype=np.int32)

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]  # path halving
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> int:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return ri
        if self.rank[ri] < self.rank[rj]:
            self.parent[ri] = rj
            return rj
        elif self.rank[ri] > self.rank[rj]:
            self.parent[rj] = ri
            return ri
        else:
            self.parent[rj] = ri
            self.rank[ri] += 1
            return ri


# =============================================================================
# 12. Mesh Segmentation
# =============================================================================

def segment_mesh(positions: np.ndarray, indices: np.ndarray,
                 bones: list, target_area: float = TARGET_SEGMENT_AREA) -> tuple:
    """Segment mesh by skeleton using region growing.
    Ref: meshSegmenter.ts:segmentMeshBySkeleton()
    """
    num_vertices = len(positions) // 2
    num_triangles = len(indices) // 3

    if num_triangles == 0 or not bones:
        return [], np.zeros(num_vertices, dtype=np.int32)

    idx = indices.reshape(-1, 3)

    x0 = positions[idx[:, 0] * 2]
    y0 = positions[idx[:, 0] * 2 + 1]
    x1 = positions[idx[:, 1] * 2]
    y1 = positions[idx[:, 1] * 2 + 1]
    x2 = positions[idx[:, 2] * 2]
    y2 = positions[idx[:, 2] * 2 + 1]

    tri_areas = np.abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) * 0.5
    tri_cx = (x0 + x1 + x2) / 3.0
    tri_cy = (y0 + y1 + y2) / 3.0

    adjacency = [[] for _ in range(num_triangles)]
    edge_map = {}
    for t in range(num_triangles):
        i0, i1, i2 = int(idx[t, 0]), int(idx[t, 1]), int(idx[t, 2])
        for a, b in ((i0, i1), (i1, i2), (i2, i0)):
            key = (min(a, b), max(a, b))
            if key in edge_map:
                other = edge_map[key]
                adjacency[t].append(other)
                adjacency[other].append(t)
            else:
                edge_map[key] = t

    centroids_arr = np.column_stack([tri_cx, tri_cy])
    tree = KDTree(centroids_arr)

    num_bones = len(bones)
    uf = UnionFind(num_bones)
    seg_areas = np.zeros(num_bones, dtype=np.float64)

    tri_owner = np.full(num_triangles, -1, dtype=np.int32)
    frontier = deque()
    in_frontier = np.zeros(num_triangles, dtype=np.int8)

    total_seeds = 0
    for bone_idx, bone in enumerate(bones):
        bone_area = 0.0
        for pt in bone["samplePoints"]:
            # KDTree query within SEED_SEARCH_RADIUS
            candidates = tree.query_ball_point([pt["x"], pt["y"]], SEED_SEARCH_RADIUS)
            if not candidates:
                # Fall back to nearest neighbor
                _, nearest_idx = tree.query([pt["x"], pt["y"]])
                candidates = [nearest_idx]

            best_tri = -1
            min_dist = float("inf")
            for c in candidates:
                d = (tri_cx[c] - pt["x"]) ** 2 + (tri_cy[c] - pt["y"]) ** 2
                if d < min_dist:
                    min_dist = d
                    best_tri = c

            if best_tri != -1 and tri_owner[best_tri] == -1:
                tri_owner[best_tri] = bone_idx
                bone_area += tri_areas[best_tri]
                total_seeds += 1

                for nb in adjacency[best_tri]:
                    if tri_owner[nb] == -1 and in_frontier[nb] == 0:
                        frontier.append((nb, bone_idx))
                        in_frontier[nb] = 1

        seg_areas[bone_idx] = bone_area

    log.info(f"  Seeded {total_seeds} triangles from {num_bones} bones")

    # Region growing
    merge_count = 0
    claim_count = 0

    while frontier:
        target_tri, source_seg_raw = frontier.popleft()
        source_seg = uf.find(source_seg_raw)

        if tri_owner[target_tri] != -1:
            existing_seg = uf.find(tri_owner[target_tri])
            if existing_seg != source_seg:
                area_a = seg_areas[existing_seg]
                area_b = seg_areas[source_seg]
                if area_a + area_b <= target_area:
                    new_root = uf.union(existing_seg, source_seg)
                    seg_areas[new_root] = area_a + area_b
                    merge_count += 1
            continue

        tri_owner[target_tri] = source_seg
        seg_areas[source_seg] += tri_areas[target_tri]
        claim_count += 1

        for nb in adjacency[target_tri]:
            if tri_owner[nb] == -1:
                if in_frontier[nb] == 0:
                    frontier.append((nb, source_seg))
                    in_frontier[nb] = 1
            else:
                nb_seg = uf.find(tri_owner[nb])
                if nb_seg != source_seg:
                    area_a = seg_areas[nb_seg]
                    area_b = seg_areas[source_seg]
                    if area_a + area_b <= target_area:
                        new_root = uf.union(nb_seg, source_seg)
                        seg_areas[new_root] = area_a + area_b
                        merge_count += 1

    log.info(f"  Growing: {claim_count} claimed, {merge_count} merges")

    # Orphan assignment
    orphan_count = 0
    for t in range(num_triangles):
        if tri_owner[t] == -1:
            orphan_count += 1
            cx, cy = tri_cx[t], tri_cy[t]
            best_bone = 0
            min_dist = float("inf")
            for b_idx, bone in enumerate(bones):
                if bone["samplePoints"]:
                    pt = bone["samplePoints"][0]
                    d = (cx - pt["x"]) ** 2 + (cy - pt["y"]) ** 2
                    if d < min_dist:
                        min_dist = d
                        best_bone = b_idx
            tri_owner[t] = uf.find(best_bone)

    if orphan_count > 0:
        log.info(f"  Assigned {orphan_count} orphan triangles")

    # Tiny segment merging
    seg_to_tris = {}
    for t in range(num_triangles):
        seg = uf.find(tri_owner[t])
        seg_to_tris.setdefault(seg, []).append(t)

    final_areas = {}
    for seg, tris in seg_to_tris.items():
        final_areas[seg] = sum(tri_areas[t] for t in tris)

    tiny_merge_count = 0
    for seg, area in list(final_areas.items()):
        if area < MIN_SEGMENT_AREA:
            tris = seg_to_tris.get(seg, [])
            neighbor_segs = {}
            for t in tris:
                for adj_t in adjacency[t]:
                    adj_seg = uf.find(tri_owner[adj_t])
                    if adj_seg != uf.find(seg):
                        neighbor_segs[adj_seg] = neighbor_segs.get(adj_seg, 0) + 1
            if not neighbor_segs:
                continue
            best_neighbor = max(neighbor_segs, key=lambda s: final_areas.get(s, 0))
            new_root = uf.union(seg, best_neighbor)
            final_areas[new_root] = final_areas.get(seg, 0) + final_areas.get(best_neighbor, 0)
            tiny_merge_count += 1

    if tiny_merge_count > 0:
        log.info(f"  Merged {tiny_merge_count} tiny segments")

    vtx_to_seg = np.full(num_vertices, -1, dtype=np.int32)
    active_segments = {}
    for t in range(num_triangles):
        owner = uf.find(tri_owner[t])
        i0, i1, i2 = int(idx[t, 0]), int(idx[t, 1]), int(idx[t, 2])
        vtx_to_seg[i0] = owner
        vtx_to_seg[i1] = owner
        vtx_to_seg[i2] = owner
        active_segments.setdefault(owner, []).append(t)

    bones_by_seg = {}
    for b_idx in range(len(bones)):
        root = uf.find(b_idx)
        bones_by_seg.setdefault(root, []).append(b_idx)

    segment_items = []
    for seg_id, tri_ids in active_segments.items():
        v_indices = set()
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for t in tri_ids:
            i0, i1, i2 = int(idx[t, 0]), int(idx[t, 1]), int(idx[t, 2])
            v_indices.update((i0, i1, i2))
        unique_v = list(v_indices)
        for v in unique_v:
            vx = positions[v * 2]
            vy = positions[v * 2 + 1]
            min_x = min(min_x, vx)
            min_y = min(min_y, vy)
            max_x = max(max_x, vx)
            max_y = max(max_y, vy)

        # Collect merged bone data via lookup
        merged_sample_pts = []
        merged_skel_edges = []
        for b_idx in bones_by_seg.get(seg_id, []):
            bone = bones[b_idx]
            merged_sample_pts.extend(bone["samplePoints"])
            merged_skel_edges.extend(bone["segments"])

        segment_items.append({
            "segmentId": int(seg_id),
            "minX": float(min_x), "minY": float(min_y),
            "maxX": float(max_x), "maxY": float(max_y),
            "vertexIndices": [int(v) for v in unique_v],
            "triangleIds": [int(t) for t in tri_ids],
            "samplePoints": [
                {"x": float(p["x"]), "y": float(p["y"]),
                 **({"radius": float(p["radius"])} if "radius" in p else {})}
                for p in merged_sample_pts
            ],
            "skeletonEdges": [
                {"p0": {"x": float(e["p0"]["x"]), "y": float(e["p0"]["y"])},
                 "p1": {"x": float(e["p1"]["x"]), "y": float(e["p1"]["y"])}}
                for e in merged_skel_edges
            ],
            "boneType": "merged",
            "area": float(seg_areas[seg_id]),
        })

    log.info(f"  Segmentation complete: {len(segment_items)} final segments")
    return segment_items, vtx_to_seg


# =============================================================================
# 13. CBOR Encoding
# =============================================================================

def cbor_default_encoder(encoder, value):
    """Custom CBOR encoder for numpy arrays → RFC 8746 typed array tags."""
    if isinstance(value, np.ndarray):
        tag_map = {
            np.dtype("float64"): CBOR_TAG_FLOAT64_LE,
            np.dtype("float32"): CBOR_TAG_FLOAT32_LE,
            np.dtype("uint32"): CBOR_TAG_UINT32_LE,
            np.dtype("int32"): CBOR_TAG_INT32_LE,
        }
        tag = tag_map.get(value.dtype)
        if tag is None:
            raise TypeError(f"Unsupported numpy dtype: {value.dtype}")
        le = value.astype(value.dtype.newbyteorder("<"), copy=False)
        encoder.encode(CBORTag(tag, le.tobytes()))
    else:
        raise TypeError(f"Cannot CBOR-encode {type(value)}")


def encode_cbor(positions, indices, water_depths, bbox, depth_range,
                vertex_count, triangle_count, segment_items, skeleton_data,
                vertex_to_segment, metadata) -> bytes:
    """Encode all data to CBOR with RFC 8746 typed array tags."""
    data = {
        "metadata": metadata,
        "mesh": {
            "positions": positions.astype(np.float64),
            "indices": indices.astype(np.uint32),
            "waterDepths": water_depths.astype(np.float32),
            "vertexCount": int(vertex_count),
            "triangleCount": int(triangle_count),
            "boundingBox": {k: float(v) for k, v in bbox.items()},
            "depthRange": {k: float(v) for k, v in depth_range.items()},
        },
        "segments": segment_items,
        "skeleton": skeleton_data,
        "vertexToSegment": vertex_to_segment.astype(np.int32),
    }
    return cbor2.dumps(data, default=cbor_default_encoder)


def serialize_skeleton_graph(graph, nodes_map: dict) -> dict:
    """Convert NetworkX graph + nodes_map to serializable dict."""
    nodes = list(graph.nodes())
    edges = [
        {"source": u, "target": v,
         "attributes": {"length": float(attr.get("length", 0))}}
        for u, v, attr in graph.edges(data=True)
    ]
    nm = {
        key: {"x": float(val["x"]), "y": float(val["y"]),
              "radius": float(val["radius"])}
        for key, val in nodes_map.items()
    }
    return {"graph": {"nodes": nodes, "edges": edges}, "nodesMap": nm}


# =============================================================================
# Main
# =============================================================================

# def run(input_tif: str, output: str | None = None,
#         depth_threshold: float = WATER_THRESHOLD,
#         min_distance: float = POISSON_MIN_DISTANCE,
#         target_area: float = TARGET_SEGMENT_AREA,
#         no_skeleton: bool = False, viz: bool = False,
#         gzip_output: bool = False, verbose: bool = False):
#     """Run preprocessing with explicit arguments (callable from other code).

#     Parameters mirror the command-line options from `main()`.
#     """
#     level = logging.DEBUG if verbose else logging.INFO
#     logging.basicConfig(level=level, format="%(message)s")

#     input_path = Path(input_tif)
#     if not input_path.exists():
#         raise FileNotFoundError(f"Error: File not found: {input_path}")

#     if output:
#         output_cbor = Path(output)
#     elif gzip_output:
#         output_cbor = input_path.with_suffix(".cbor.gz")
#     else:
#         output_cbor = input_path.with_suffix(".cbor")

#     # ---- Pipeline ----

#     print(f"[1/8] Parsing GeoTIFF...")
#     raster = parse_geotiff(str(input_path))

#     skeleton_segments = []
#     skeleton_nodes = []
#     centroids = []
#     bones = []
#     graph = nx.Graph()
#     nodes_map = {}

#     if not no_skeleton:
#         print(f"[2/8] Computing Medial Axis Transform...")
#         skeleton_segments, centroids = compute_raster_mat(raster, depth_threshold)

#         if skeleton_segments:
#             print(f"[3/8] Processing skeleton segments...")
#             processed_segs, skeleton_nodes = process_skeleton_segments(skeleton_segments)
#             skeleton_segments = processed_segs

#             print(f"[4/8] Partitioning into bones...")
#             graph, nodes_map = build_skeleton_graph(skeleton_segments)
#             graph, nodes_map = simplify_skeleton_graph(graph, nodes_map)
#             # Re-derive skeleton_nodes from simplified graph for PSLG injection
#             skeleton_nodes = [{"x": v["x"], "y": v["y"], "radius": v["radius"], "type": "skeleton"}
#                               for v in nodes_map.values()]
#             bones = partition_into_bones(graph, nodes_map, centroids)
#         elif centroids:
#             log.info("  No skeleton segments, using centroids only")
#             bones = partition_into_bones(nx.Graph(), {}, centroids)
#         else:
#             log.warning("  No water regions found for skeleton")
#     else:
#         print(f"[2/8] Skipping skeleton (--no-skeleton)")
#         print(f"[3/8] Skipping skeleton")
#         print(f"[4/8] Skipping bones")

#     print(f"[5/8] Extracting mesh boundary...")
#     mesh_polys = extract_binary_contours(raster, depth_threshold=depth_threshold)

#     if not mesh_polys:
#         raise RuntimeError("Error: No water regions found. Try a lower --depth-threshold.")

#     print(f"[6a/8] Building PSLG...")
#     pslg = build_pslg(mesh_polys, skeleton_nodes, centroids, raster["bbox_wgs84"],
#                       min_distance=min_distance)
#     print(f"[6b/8] Triangulating ({len(pslg['vertices'])} vertices, "
#           f"{len(pslg['segments'])} segments, max_area={pslg['max_area']:.2e})...")
#     vertices, triangles = quality_triangulate(pslg)

#     print(f"[7/8] Sampling depths...")
#     water_depths = sample_depths(vertices, raster)

#     positions = vertices.flatten().astype(np.float64)
#     indices_flat = triangles.flatten().astype(np.uint32)
#     vertex_count = len(vertices)
#     triangle_count = len(triangles)

#     mesh_bbox = {
#         "minLon": float(vertices[:, 0].min()),
#         "minLat": float(vertices[:, 1].min()),
#         "maxLon": float(vertices[:, 0].max()),
#         "maxLat": float(vertices[:, 1].max()),
#     }
#     max_depth = float(water_depths.max()) if water_depths.max() > 0 else 1.0
#     depth_range = {"min": 0.0, "max": max_depth}

#     segment_items = []
#     vtx_to_seg = np.zeros(vertex_count, dtype=np.int32)

#     if bones:
#         print(f"[8/8] Segmenting mesh...")
#         segment_items, vtx_to_seg = segment_mesh(
#             positions, indices_flat, bones, target_area)
#     else:
#         print(f"[8/8] Skipping segmentation (no bones)")

#     skeleton_data = serialize_skeleton_graph(graph, nodes_map)

#     metadata = {
#         "version": "1.0",
#         "timestamp": datetime.now(timezone.utc).isoformat(),
#         "sourceFile": input_path.name,
#         "crs": raster["crs_info"],
#         "originalBoundingBox": raster["original_bounds"],
#         "rasterDimensions": {"width": raster["width"], "height": raster["height"]},
#         "processingOptions": {
#             "depthThreshold": float(depth_threshold),
#             "minDistance": float(min_distance),
#             "targetSegmentArea": float(target_area),
#             "enableSkeleton": not no_skeleton,
#         },
#     }

#     print("Writing CBOR output...")
#     cbor_bytes = encode_cbor(
#         positions, indices_flat, water_depths, mesh_bbox, depth_range,
#         vertex_count, triangle_count, segment_items, skeleton_data,
#         vtx_to_seg, metadata)

#     output_cbor.parent.mkdir(parents=True, exist_ok=True)
#     if gzip_output:
#         compressed = gzip.compress(cbor_bytes, compresslevel=9)
#         with open(output_cbor, "wb") as f:
#             f.write(compressed)
#         ratio = len(compressed) / len(cbor_bytes) * 100
#         print(f"CBOR written: {output_cbor} ({len(compressed):,} bytes, "
#               f"gzip {ratio:.1f}% of {len(cbor_bytes):,})")
#     else:
#         with open(output_cbor, "wb") as f:
#             f.write(cbor_bytes)
#         print(f"CBOR written: {output_cbor} ({len(cbor_bytes):,} bytes)")

#     # if viz:
#     #     print("Creating visualization...")
#     #     create_visualization(
#     #         raster, skeleton_segments, bones, vertices, triangles,
#     #         segment_items, vtx_to_seg, str(output_viz))
#     #     print(f"Visualization saved: {output_viz}")

#     print("Done.")


class WD3dMeshPreprocessor:
    """Object-oriented wrapper for the wd3d preprocessing pipeline.

    This class provides instance methods that mirror the module-level
    functions and stores configuration options so the pipeline can be
    invoked from other Python code more easily.
    """

    def __init__(self, depth_threshold: float = WATER_THRESHOLD,
                 min_distance: float = POISSON_MIN_DISTANCE,
                 target_area: float = TARGET_SEGMENT_AREA,
                 no_skeleton: bool = False, viz: bool = False,
                 gzip_output: bool = False, verbose: bool = False):
        self.depth_threshold = depth_threshold
        self.min_distance = min_distance
        self.target_area = target_area
        self.no_skeleton = no_skeleton
        self.viz = viz
        self.gzip_output = gzip_output
        self.verbose = verbose

        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(level=level, format="%(message)s")

    # Thin wrappers around module-level functions (keeps original funcs)
    def parse_geotiff(self, path: str):
        return parse_geotiff(path)

    def extract_binary_contours(self, raster: dict):
        return extract_binary_contours(raster, depth_threshold=self.depth_threshold)

    def compute_raster_mat(self, raster: dict):
        return compute_raster_mat(raster, self.depth_threshold)

    def process_skeleton_segments(self, segments: list):
        return process_skeleton_segments(segments)

    def build_skeleton_graph(self, segments: list):
        return build_skeleton_graph(segments)

    def simplify_skeleton_graph(self, G: nx.Graph, nodes_map: dict):
        return simplify_skeleton_graph(G, nodes_map)

    def partition_into_bones(self, graph, nodes_map: dict, centroids: list):
        return partition_into_bones(graph, nodes_map, centroids)

    def build_pslg(self, mesh_polys: list, skeleton_nodes: list, centroids: list, bbox_wgs84: dict):
        return build_pslg(mesh_polys, skeleton_nodes, centroids, bbox_wgs84, min_distance=self.min_distance)

    def quality_triangulate(self, pslg: dict):
        return quality_triangulate(pslg)

    def sample_depths(self, vertices: np.ndarray, raster: dict):
        return sample_depths(vertices, raster)

    def segment_mesh(self, positions: np.ndarray, indices: np.ndarray, bones: list):
        return segment_mesh(positions, indices, bones, self.target_area)

    def serialize_skeleton_graph(self, graph, nodes_map: dict):
        return serialize_skeleton_graph(graph, nodes_map)

    def encode_cbor(self, positions, indices, water_depths, bbox, depth_range,
                    vertex_count, triangle_count, segment_items, skeleton_data,
                    vertex_to_segment, metadata):
        return encode_cbor(positions, indices, water_depths, bbox, depth_range,
                           vertex_count, triangle_count, segment_items, skeleton_data,
                           vertex_to_segment, metadata)

    def run_pipeline(self, input_tif: str, output: str | None = None):
        """Execute the full preprocessing pipeline using instance options."""
        input_path = Path(input_tif)
        if not input_path.exists():
            raise FileNotFoundError(f"Error: File not found: {input_path}")

        if output:
            output_cbor = Path(output)
        elif self.gzip_output:
            output_cbor = input_path.with_suffix(".cbor.gz")
        else:
            output_cbor = input_path.with_suffix(".cbor")

        print(f"[1/8] Parsing GeoTIFF...")
        raster = self.parse_geotiff(str(input_path))

        skeleton_segments = []
        skeleton_nodes = []
        centroids = []
        bones = []
        graph = nx.Graph()
        nodes_map = {}

        if not self.no_skeleton:
            print(f"[2/8] Computing Medial Axis Transform...")
            skeleton_segments, centroids = self.compute_raster_mat(raster)

            if skeleton_segments:
                print(f"[3/8] Processing skeleton segments...")
                processed_segs, skeleton_nodes = self.process_skeleton_segments(skeleton_segments)
                skeleton_segments = processed_segs

                print(f"[4/8] Partitioning into bones...")
                graph, nodes_map = self.build_skeleton_graph(skeleton_segments)
                graph, nodes_map = self.simplify_skeleton_graph(graph, nodes_map)
                skeleton_nodes = [{"x": v["x"], "y": v["y"], "radius": v["radius"], "type": "skeleton"}
                                  for v in nodes_map.values()]
                bones = self.partition_into_bones(graph, nodes_map, centroids)
            elif centroids:
                log.info("  No skeleton segments, using centroids only")
                bones = self.partition_into_bones(nx.Graph(), {}, centroids)
            else:
                log.warning("  No water regions found for skeleton")
        else:
            print(f"[2/8] Skipping skeleton (--no-skeleton)")
            print(f"[3/8] Skipping skeleton")
            print(f"[4/8] Skipping bones")

        print(f"[5/8] Extracting mesh boundary...")
        mesh_polys = self.extract_binary_contours(raster)

        if not mesh_polys:
            raise RuntimeError("Error: No water regions found. Try a lower --depth-threshold.")

        print(f"[6a/8] Building PSLG...")
        pslg = self.build_pslg(mesh_polys, skeleton_nodes, centroids, raster["bbox_wgs84"])
        print(f"[6b/8] Triangulating ({len(pslg['vertices'])} vertices, "
              f"{len(pslg['segments'])} segments, max_area={pslg['max_area']:.2e})...")
        vertices, triangles = self.quality_triangulate(pslg)

        print(f"[7/8] Sampling depths...")
        water_depths = self.sample_depths(vertices, raster)

        positions = vertices.flatten().astype(np.float64)
        indices_flat = triangles.flatten().astype(np.uint32)
        vertex_count = len(vertices)
        triangle_count = len(triangles)

        mesh_bbox = {
            "minLon": float(vertices[:, 0].min()),
            "minLat": float(vertices[:, 1].min()),
            "maxLon": float(vertices[:, 0].max()),
            "maxLat": float(vertices[:, 1].max()),
        }
        max_depth = float(water_depths.max()) if water_depths.max() > 0 else 1.0
        depth_range = {"min": 0.0, "max": max_depth}

        segment_items = []
        vtx_to_seg = np.zeros(vertex_count, dtype=np.int32)

        if bones:
            print(f"[8/8] Segmenting mesh...")
            segment_items, vtx_to_seg = self.segment_mesh(
                positions, indices_flat, bones)
        else:
            print(f"[8/8] Skipping segmentation (no bones)")

        skeleton_data = self.serialize_skeleton_graph(graph, nodes_map)

        metadata = {
            "version": "1.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sourceFile": input_path.name,
            "crs": raster["crs_info"],
            "originalBoundingBox": raster["original_bounds"],
            "rasterDimensions": {"width": raster["width"], "height": raster["height"]},
            "processingOptions": {
                "depthThreshold": float(self.depth_threshold),
                "minDistance": float(self.min_distance),
                "targetSegmentArea": float(self.target_area),
                "enableSkeleton": not self.no_skeleton,
            },
        }

        print("Writing CBOR output...")
        cbor_bytes = self.encode_cbor(
            positions, indices_flat, water_depths, mesh_bbox, depth_range,
            vertex_count, triangle_count, segment_items, skeleton_data,
            vtx_to_seg, metadata)

        output_cbor.parent.mkdir(parents=True, exist_ok=True)
        if self.gzip_output:
            compressed = gzip.compress(cbor_bytes, compresslevel=9)
            with open(output_cbor, "wb") as f:
                f.write(compressed)
            ratio = len(compressed) / len(cbor_bytes) * 100
            print(f"CBOR written: {output_cbor} ({len(compressed):,} bytes, "
                  f"gzip {ratio:.1f}% of {len(cbor_bytes):,})")
        else:
            with open(output_cbor, "wb") as f:
                f.write(cbor_bytes)
            print(f"CBOR written: {output_cbor} ({len(cbor_bytes):,} bytes)")

        # if self.viz:
        #     print("Creating visualization...")
        #     create_visualization(
        #         raster, skeleton_segments, bones, vertices, triangles,
        #         segment_items, vtx_to_seg, str(output_viz))
        #     print(f"Visualization saved: {output_viz}")

        print("Done.")
        
        return output_cbor if os.path.exists(output_cbor) else None


# def run(input_tif: str, output: str | None = None,
#         depth_threshold: float = WATER_THRESHOLD,
#         min_distance: float = POISSON_MIN_DISTANCE,
#         target_area: float = TARGET_SEGMENT_AREA,
#         no_skeleton: bool = False, viz: bool = False,
#         gzip_output: bool = False, verbose: bool = False):
#     """Backward-compatible run() that delegates to WD3dMeshPreprocessor."""
#     pre = WD3dMeshPreprocessor(depth_threshold=depth_threshold,
#                                min_distance=min_distance,
#                                target_area=target_area,
#                                no_skeleton=no_skeleton,
#                                viz=viz,
#                                gzip_output=gzip_output,
#                                verbose=verbose)
#     return pre.run_pipeline(input_tif, output)


# def main():
#     parser = argparse.ArgumentParser(
#         description="CesiumFlood Preprocessor: Generate CBOR mesh from GeoTIFF flood depth raster.")
#     parser.add_argument("input_tif", help="Path to input GeoTIFF flood/water depth raster")
#     parser.add_argument("--output", help="Output CBOR file path (default: <input>.cbor)")
#     parser.add_argument("--depth-threshold", type=float, default=WATER_THRESHOLD,
#                         help=f"Minimum water depth threshold in meters (default: {WATER_THRESHOLD})")
#     parser.add_argument("--min-distance", type=float, default=POISSON_MIN_DISTANCE,
#                         help=f"Minimum sample distance in meters (default: {POISSON_MIN_DISTANCE})")
#     parser.add_argument("--target-area", type=float, default=TARGET_SEGMENT_AREA,
#                         help=f"Max segment area in deg^2 (default: {TARGET_SEGMENT_AREA})")
#     parser.add_argument("--no-skeleton", action="store_true",
#                         help="Disable skeleton generation (centroids only)")
#     parser.add_argument("--viz", action="store_true",
#                         help="Export visualization PNG")
#     parser.add_argument("--gzip", action="store_true",
#                         help="Gzip-compress the CBOR output (.cbor.gz)")
#     parser.add_argument("--verbose", action="store_true",
#                         help="Enable verbose debug logging")
#     args = parser.parse_args()

#     # Delegate to run() so callers can import and call `run()` directly.
#     run(
#         args.input_tif,
#         output=args.output,
#         depth_threshold=args.depth_threshold,
#         min_distance=args.min_distance,
#         target_area=args.target_area,
#         no_skeleton=args.no_skeleton,
#         viz=args.viz,
#         gzip_output=args.gzip,
#         verbose=args.verbose,
#     )


# if __name__ == "__main__":
#     main()
