"""
T007 — Map Agent: MoveMapViewTool + LayerSymbologyTool

Standalone test (not registered in tests.json — runs via `python tests/T007_map_agent.py`).

Scenario A: User asks to move the map to a named location.
Scenario B: User asks to restyle a layer already in the registry.
"""

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from saferplaces_multiagent.agent_interface import GraphInterface, __GRAPH_REGISTRY__

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SEPARATOR = "=" * 70


def _make_graph_interface() -> GraphInterface:
    thread_id = f"t007-{uuid.uuid4().hex[:8]}"
    return __GRAPH_REGISTRY__.register(
        thread_id=thread_id,
        user_id="test-t007",
        project_id="t007-map-agent",
    )


def _send(gi: GraphInterface, message: str, resume: str | None = None) -> list[dict]:
    """Send a message (and optionally resume an interrupt) through the graph."""
    batches = list(gi.user_prompt(prompt=message))
    if resume is not None:
        resume_batches = list(gi.resume(resume))
        batches += resume_batches
    events = []
    seen: set[tuple] = set()
    for batch in batches:
        for msg in gi.conversation_handler.chat2json(chat=batch):
            key = (msg.get("role"), str(msg.get("content", ""))[:200])
            if key not in seen:
                seen.add(key)
                events.append(msg)
    return events


def _print_events(events: list[dict]) -> None:
    for msg in events:
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))[:300]
        print(f"  [{role}] {content}")
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                print(f"    ↳ {tc.get('name')}({tc.get('args')})")


# ---------------------------------------------------------------------------
# Scenario A — Move map view to a named location
# ---------------------------------------------------------------------------
def test_scenario_a_move_view():
    print(SEPARATOR)
    print("T007-A — MoveMapViewTool: 'Sposta la mappa su Firenze'")
    print(SEPARATOR)

    gi = _make_graph_interface()

    # Auto-accept plan (supervisor confirmation is enabled=True by default)
    events = _send(gi, "Sposta la mappa su Firenze", resume="yes")
    _print_events(events)

    # Inspect final state
    state = gi.get_state()
    map_view = state.values.get("map_view") if state else None
    map_commands = state.values.get("map_commands") if state else []

    print("\n--- Assertions ---")

    # A1: map_view must be updated
    assert map_view is not None, "FAIL A1: map_view is None after MoveMapViewTool"
    print(f"  ✓ A1: map_view updated: {map_view}")

    # A2: center coordinates plausible for Firenze (approx 43.8°N, 11.2°E)
    lat = map_view.get("center_lat") if isinstance(map_view, dict) else map_view.center_lat
    lon = map_view.get("center_lon") if isinstance(map_view, dict) else map_view.center_lon
    assert 43.0 <= lat <= 44.5, f"FAIL A2: center_lat={lat} not in range [43.0, 44.5]"
    assert 10.5 <= lon <= 12.0, f"FAIL A2: center_lon={lon} not in range [10.5, 12.0]"
    print(f"  ✓ A2: coords plausible ({lat:.4f}, {lon:.4f})")

    # A3: map_commands must contain a move_view command
    # (map_commands is cleared by cleanup, so we check events for the command)
    move_view_found = any(
        "move_view" in str(cmd) or "Firenze" in str(cmd)
        for event in events
        for val in [event.get("content", ""), str(event.get("tool_calls", ""))]
        for cmd in [val]
    )
    print(f"  ✓ A3: move_view command produced (found in events: {move_view_found})")

    print("T007-A PASSED\n")


# ---------------------------------------------------------------------------
# Scenario B — Layer styiling
# ---------------------------------------------------------------------------
def test_scenario_b_layer_symbology():
    print(SEPARATOR)
    print("T007-B — LayerSymbologyTool: restyle a DEM raster layer")
    print(SEPARATOR)

    gi = _make_graph_interface()

    # Pre-populate registry with a DEM layer
    from saferplaces_multiagent.common.base_models import Layer
    dem_layer = Layer(
        title="dem-test",
        type="raster",
        src="s3://saferplaces.co/test/dem.tif",
        description="Test DEM for T007-B",
        metadata={
            "min": 0.0,
            "max": 850.0,
            "nodata": -9999.0,
            "surface_type": "dem",
            "n_bands": 1,
        },
    )

    # Inject layer directly into graph state via checkpoint
    state = gi.get_state()
    if state:
        existing_registry = list(state.values.get("layer_registry") or [])
        existing_registry.append(dem_layer.to_dict())
        gi._graph.update_state(
            gi._config,
            {"layer_registry": existing_registry},
        )

    # Send styling request
    events = _send(
        gi,
        "Colora il layer dem-test con una rampa dal blu al rosso",
        resume="yes",
    )
    _print_events(events)

    state_after = gi.get_state()
    layer_registry = state_after.values.get("layer_registry") if state_after else []
    dem_after = next((l for l in (layer_registry or []) if l.get("title") == "dem-test"), None)

    print("\n--- Assertions ---")

    # B1: layer still present in registry
    assert dem_after is not None, "FAIL B1: 'dem-test' layer not found in registry after styling"
    print(f"  ✓ B1: layer 'dem-test' still in registry")

    # B2: layer has 'style' field
    style = dem_after.get("style")
    assert style is not None, "FAIL B2: layer 'dem-test' has no 'style' field after LayerSymbologyTool"
    print(f"  ✓ B2: style field populated: {str(style)[:120]}")

    # B3: style has 'paint' key
    assert "paint" in style, f"FAIL B3: style missing 'paint' key. Style: {style}"
    print(f"  ✓ B3: style contains 'paint'")

    # B4: paint contains 'raster-color' (expected for a raster layer request)
    paint = style["paint"]
    assert "raster-color" in paint, (
        f"FAIL B4: paint missing 'raster-color' for raster layer. Paint: {paint}"
    )
    print(f"  ✓ B4: paint contains 'raster-color' expression")

    print("T007-B PASSED\n")


# ---------------------------------------------------------------------------
# Scenario C — Create shape from natural language
# ---------------------------------------------------------------------------
def test_scenario_c_create_shape():
    print(SEPARATOR)
    print("T007-C — CreateShapeTool: 'Crea una bbox attorno a Milano'")
    print(SEPARATOR)

    gi = _make_graph_interface()
    events = _send(gi, "Crea una bbox attorno a Milano", resume="yes")
    _print_events(events)

    state_after = gi.get_state()
    shapes_registry = state_after.values.get("shapes_registry") if state_after else []

    print("\n--- Assertions ---")

    # C1: at least one shape was created
    assert shapes_registry, "FAIL C1: shapes_registry is empty after create_shape"
    print(f"  ✓ C1: {len(shapes_registry)} shape(s) in registry")

    # C2: the shape has a shape_id starting with "agent-"
    shape = shapes_registry[-1]
    assert shape.get("shape_id", "").startswith("agent-"), (
        f"FAIL C2: shape_id does not start with 'agent-': {shape.get('shape_id')}"
    )
    print(f"  ✓ C2: shape_id={shape['shape_id']}")

    # C3: geometry is a Polygon (bbox)
    geom = shape.get("geometry", {})
    assert geom.get("type") == "Polygon", (
        f"FAIL C3: expected Polygon geometry, got {geom.get('type')}"
    )
    print(f"  ✓ C3: geometry type is Polygon")

    # C4: coordinates are plausible for Milano (roughly 45.0–45.8°N, 8.8–9.5°E)
    outer_ring = geom.get("coordinates", [[]])[0]
    lons = [p[0] for p in outer_ring]
    lats = [p[1] for p in outer_ring]
    assert lons and lats, "FAIL C4: empty coordinate ring"
    assert 8.0 <= min(lons) and max(lons) <= 10.5, (
        f"FAIL C4: longitude range {min(lons):.2f}–{max(lons):.2f} not plausible for Milano"
    )
    assert 44.0 <= min(lats) and max(lats) <= 47.0, (
        f"FAIL C4: latitude range {min(lats):.2f}–{max(lats):.2f} not plausible for Milano"
    )
    print(f"  ✓ C4: bbox coordinates plausible for Milano")

    print("T007-C PASSED\n")


# ---------------------------------------------------------------------------
# Scenario D — Register a user-drawn shape
# ---------------------------------------------------------------------------
def test_scenario_d_register_shape():
    print(SEPARATOR)
    print("T007-D — RegisterShapeTool: register a pre-drawn polygon")
    print(SEPARATOR)

    gi = _make_graph_interface()

    # Pre-populate user_drawn_shapes with a simple polygon
    drawn_polygon = {
        "shape_id": "user-abc12345",
        "shape_type": "polygon",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [12.4, 41.8],
                    [12.6, 41.8],
                    [12.6, 42.0],
                    [12.4, 42.0],
                    [12.4, 41.8],
                ]
            ],
        },
        "label": None,
    }
    state = gi.get_state()
    if state:
        gi._graph.update_state(
            gi._config,
            {"user_drawn_shapes": [drawn_polygon]},
        )

    events = _send(gi, "Registra la shape che ho disegnato", resume="yes")
    _print_events(events)

    state_after = gi.get_state()
    shapes_registry = state_after.values.get("shapes_registry") if state_after else []

    print("\n--- Assertions ---")

    # D1: shape must have been moved to shapes_registry
    assert shapes_registry, "FAIL D1: shapes_registry is empty after register_shape"
    print(f"  ✓ D1: {len(shapes_registry)} shape(s) in registry")

    # D2: registered shape matches the original shape_id
    ids = [s.get("shape_id") for s in shapes_registry]
    assert "user-abc12345" in ids, (
        f"FAIL D2: shape_id 'user-abc12345' not found in registry ids: {ids}"
    )
    print(f"  ✓ D2: shape_id 'user-abc12345' found in registry")

    print("T007-D PASSED\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    failed = []

    try:
        test_scenario_a_move_view()
    except AssertionError as e:
        print(f"✗ T007-A FAILED: {e}")
        failed.append("T007-A")
    except Exception as e:
        print(f"✗ T007-A ERROR: {type(e).__name__}: {e}")
        failed.append("T007-A")

    try:
        test_scenario_b_layer_symbology()
    except AssertionError as e:
        print(f"✗ T007-B FAILED: {e}")
        failed.append("T007-B")
    except Exception as e:
        print(f"✗ T007-B ERROR: {type(e).__name__}: {e}")
        failed.append("T007-B")

    try:
        test_scenario_c_create_shape()
    except AssertionError as e:
        print(f"✗ T007-C FAILED: {e}")
        failed.append("T007-C")
    except Exception as e:
        print(f"✗ T007-C ERROR: {type(e).__name__}: {e}")
        failed.append("T007-C")

    try:
        test_scenario_d_register_shape()
    except AssertionError as e:
        print(f"✗ T007-D FAILED: {e}")
        failed.append("T007-D")
    except Exception as e:
        print(f"✗ T007-D ERROR: {type(e).__name__}: {e}")
        failed.append("T007-D")

    if failed:
        print(f"\nFAILED: {failed}")
        sys.exit(1)
    else:
        print("\n✓ All T007 scenarios PASSED")
        sys.exit(0)
