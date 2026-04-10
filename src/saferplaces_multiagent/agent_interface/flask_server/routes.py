import os
import re
import uuid
import json
import base64
import tempfile

from markupsafe import escape

from flask import (
    Response, 
    request,
    jsonify,
    current_app as app,
    render_template,
    send_from_directory,
    stream_with_context
)

from .. import GraphInterface
from ... import utils, s3_utils


# @app.before_request
# def assegna_session_id():
#     print(request.endpoint)
#     # DOC: Handle new GraphInterface session
#     if request.endpoint == 'start':
#         print('Create new graph interface session')
#         gi: GraphInterface = app.__GRAPH_REGISTRY__.register(
#             thread_id=str(uuid.uuid4()),
#             user_id='flask_usr_000',
#             project_id='project_000',
#             map_handler=None  # DOC: Default map handler, can be changed later
#         )
#         session["session_id"] = gi.thread_id
    
    
        

@app.route('/')
def index():
    # return jsonify("Welcome to the SaferPlaces Agent Interface!"), 200
    print("Rendering index.html")
    return render_template('index.html')


@app.route('/user', methods=['POST'])
def user():
    data = request.get_json(silent=True) or {}
    print(data)
    user_id = data.get('user_id', None)
    if not user_id:
        return jsonify({"error": "User ID is required"}), 400
    
    user_bucket_files = s3_utils.list_s3_files(f's3://{os.getenv("BUCKET_NAME", "saferplaces.co")}/{os.getenv("BUCKET_OUT_DIR", "SaferPlaces-Agent/dev")}/user={user_id}')
    user_project = sorted(list(set([re.search(r'project=([\w-]+)', p).group(1) for p in user_bucket_files if 'project=' in p])))
    
    return jsonify({
        "user_id": user_id,
        "projects": user_project
    }), 200
    

@app.route('/t', methods=['GET', 'POST'])
def start():
    if request.method == 'GET':
        gi: GraphInterface = app.__GRAPH_REGISTRY__.register(
            thread_id = str(uuid.uuid4()),
            user_id = 'flask_usr_000',
            project_id = 'project_000',
            map_handler = None
        )
    
    elif request.method == 'POST':
        data = request.get_json(silent=True) or {}
        thread_id = data.get('thread_id', None) or str(uuid.uuid4())
        
        gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
        if not gi:
            gi = app.__GRAPH_REGISTRY__.register(
                thread_id = thread_id,
                user_id = data.get('user_id', 'flask_usr_000'),
                project_id = data.get('project_id', 'project_000'),
                map_handler = None
            )
        
    else:
        return jsonify({"error": f"Method {request.method} not allowed"}), 405
    
    print(f"Started with GraphInterface ID: {gi.thread_id}")
    
    return jsonify({
        "thread_id": gi.thread_id,
        "user_id": gi.user_id,
        "project_id": gi.project_id
    }), 200


@app.route('/t/<thread_id>/state', methods=['POST'])
def state(thread_id):
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404
    
    def ensure_json_state(state: dict) -> dict:
        """Fix the state to be JSON serializable."""
        if 'messages' in state:
            state['messages'] = gi.conversation_handler.chat2json(chat=state['messages'])
        return state
    
    # DOC: if no updates are provided, route is used to retrieve the current state
    data = request.get_json(silent=True) or dict()
    state_updates = data.get('state_updates', dict())
    if not isinstance(state_updates, dict) or len(state_updates) == 0:
        return ensure_json_state(gi.get_state()), 200
    
    # DOC: Filter the updated state to only include keys that were requested.
    # Always add shape-related state so the frontend can sync the draw toolbar.
    updated_state = gi.set_state(state_updates)
    response_keys = set(state_updates.keys()) | {'shapes_registry', 'user_drawn_shapes', 'map_commands'}
    filtered = {k: v for k, v in updated_state.items() if k in response_keys}
    
    return jsonify(ensure_json_state(filtered)), 200


@app.route('/t/<thread_id>', methods=['POST'])
def prompt(thread_id):
    
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404
    
    data = request.get_json()
    if not data or 'prompt' not in data:
        return jsonify({'error': 'Invalid input'}), 400
    
    prompt = escape(data['prompt'])
    
    stream_mode = data.get('stream', False)

    state_updates = {
        'available_tools': [],
        **data.get('state_updates', dict())
    }
    
    if stream_mode:
        def generate():
            for e in gi.user_prompt(prompt=prompt, state_updates=state_updates):
                yield json.dumps(gi.conversation_handler.chat2json(chat=e)) + "\n"
            yield json.dumps(dict(stop=True)) + "\n"
        
        # return Response(generate(), mimetype='text/plain')
        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",   # importante se usi nginx
            }
        )
    
    else:
        gen = (
            gi.conversation_handler.chat2json(chat=e)
            for e in gi.user_prompt(prompt=prompt, state_updates=state_updates)
        )
    
        return jsonify(list(gen)), 200
    
    
@app.route('/t/<thread_id>/layers', methods=['POST'])
def layers(thread_id):
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404
    
    layers = gi.get_state('layer_registry', [])
    
    return jsonify(layers), 200


@app.route('/t/<thread_id>/shapes', methods=['POST'])
def shapes(thread_id):
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404
    
    shapes = gi.get_state('user_drawn_shapes', [])
    
    return jsonify(shapes), 200


@app.route('/t/<thread_id>/render', methods=['POST'])
def render_layer(thread_id):
    data = request.get_json(silent=True) or dict()

    layer_data = data.get('layer_data', None)
    if not layer_data:
        return jsonify({"error": "Layer data is required"}), 400

    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404

    to_be_registered = layer_data.get('register', False)
    if to_be_registered:
        if not gi:
            return jsonify({"error": "GraphInterface not found"}), 404
        gi.register_layer(
            src = layer_data['src'],
            title = layer_data.get('title', None),
            description = layer_data.get('description', None),
            layer_type = layer_data.get('type', None),
            metadata = layer_data.get('metadata', dict()),
        )

    layer_src = layer_data.get('src', None)
    if not layer_src:
        return jsonify({"error": "Layer source is required"}), 400
    
    layer_type = layer_data.get('type', None)
    if not layer_type:
        return jsonify({"error": "Layer type is required"}), 400
    
    og_metadata = layer_data.get('metadata', dict())
    
    if layer_type == 'vector':
        layer_render_src = utils.vector_to_geojson4326(layer_src)
        metadata = utils.vector_specs(layer_render_src)
    elif layer_type == 'raster':
        layer_render_src = utils.tif_to_cog3857(layer_src)
        is_timeseries = 'timeseries' in og_metadata.get('surface_type', '')
        if is_timeseries:
            metadata = utils.raster_ts_specs(layer_render_src)
        else:
            metadata = utils.raster_specs(layer_render_src)
    else:
        return jsonify({"error": f"Layer type '{layer_type}' is not supported"}), 400
        
    if any(m not in og_metadata for m in metadata):
        metadata = { **og_metadata, **metadata }
        gi.register_layer(
            src = layer_data['src'],
            title = layer_data.get('title', None),
            description = layer_data.get('description', None),
            layer_type = layer_data.get('type', None),
            metadata = metadata,
        )
        
    return jsonify({'src': utils.s3uri_to_https(layer_render_src), 'metadata': metadata}), 200


# DOC: === Cesium routes ====

# CESIUM_DIST = "../../../../../safer-3d-cesium/demo/dist"
CESIUM_DIST = f"{app.static_folder}/ext/safer-3d-cesium/demo/dist"

@app.route("/cesium-viewer", methods=["POST"])
def cesium_index():
    thread_id = request.form.get('thread_id')

    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404

    return send_from_directory(CESIUM_DIST, "index.html")

@app.route("/cesium-viewer/assets/<path:filename>")
def cesium_assets(filename):
    return send_from_directory(
        os.path.join(CESIUM_DIST, "assets"),
        filename
    )

@app.route("/cesium-viewer/cesium/<path:filename>")
def cesium_cesium(filename):
    return send_from_directory(
        os.path.join(CESIUM_DIST, "cesium-viewer", "cesium"),
        filename
    )

@app.route("/cesium-viewer/api/load-wds", methods=["POST"])
def cesium_load_wds():
    data = request.get_json(silent=True) or dict()

    thread_id = data.get('thread_id')

    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404

    # If a specific layer src is provided (e.g. from "3D View" action), use it directly
    src = data.get('src')
    if not src:
        layer_registry = gi.get_state('layer_registry', [])
        is_wd_layer = lambda layer_data: layer_data.get('metadata', dict()).get('surface_type') == 'water-depth'
        wd_layers = [l for l in layer_registry if is_wd_layer(l)]
        if len(wd_layers) == 0:
            return jsonify({"skipping": "No water-depth layers found"}), 200
        src = wd_layers[0]['src']

    wd3d_uri = gi.cesium_handler.preprocess_wd(src)
    if not wd3d_uri:
        return jsonify({"error": "Failed to preprocess water-depth layer"}), 500

    wd3d_url = utils.s3uri_to_https(wd3d_uri)

    return jsonify({"wd3d_url": wd3d_url}), 200



# DOC: === Generic routes [user-independent] ===

@app.route('/get-layer-url', methods=['POST'])
def get_layer_url():
    data = request.get_json(silent=True) or {}
    layer_src = data.get('src', None)
    if not layer_src:
        return jsonify({"error": "Layer source is required"}), 400
    
    return jsonify({'download_url': utils.download_url(layer_src)}), 200


@app.route('/t/<thread_id>/register-vector', methods=['POST'])
def register_vector(thread_id):
    """Upload a GeoJSON payload to S3, register it as a layer, return HTTPS URL."""
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404

    data = request.get_json(silent=True) or {}
    geojson_data = data.get('geojson')
    if not geojson_data:
        return jsonify({"error": "GeoJSON data is required"}), 400

    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip() or None

    # Build a safe filename from title or a random token
    base_name = re.sub(r'[^\w\-]', '_', title) if title else utils.random_id8()
    filename = f"{base_name}.geojson"

    # Write to a temporary file then upload to S3
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.geojson', delete=False, encoding='utf-8') as fh:
            json.dump(geojson_data, fh, ensure_ascii=False)
            tmp_path = fh.name

        # Ensure WGS84 - reproject locally before upload
        utils.vector_to_geojson4326_local(tmp_path)

        state_bucket = s3_utils._STATE_BUCKET_(dict(user_id=gi.user_id, project_id=gi.project_id))
        s3_uri = f"{state_bucket}/vectors/{filename}"
        s3_utils.s3_upload(filename=tmp_path, uri=s3_uri, remove_src=True)
        tmp_path = None  # upload moved / deleted the file

    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return jsonify({"error": f"S3 upload failed: {exc}"}), 500

    src_url = utils.s3uri_to_https(s3_uri)
    layer_title = title if title else utils.juststem(filename)

    gi.register_layer(
        src=s3_uri,
        title=layer_title,
        description=description,
        layer_type='vector',
        metadata=utils.vector_specs(s3_uri)
    )

    return jsonify({'src': src_url, 'title': layer_title}), 200


@app.route('/t/<thread_id>/register-raster', methods=['POST'])
def register_raster(thread_id):
    """Align, convert to COG3857, upload to S3 and register a sculpted DEM."""
    gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
    if not gi:
        return jsonify({"error": "GraphInterface not found"}), 404

    data = request.get_json(silent=True) or {}
    tif_b64 = data.get('tif_base64')
    if not tif_b64:
        return jsonify({"error": "TIF data is required"}), 400

    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip() or None
    source_dem_url = (data.get('source_dem_url') or '').strip() or None

    base_name = re.sub(r'[^\w\-]', '_', title) if title else utils.random_id8()
    filename = f"{base_name}.tif"

    tmp_raw = None
    tmp_aligned = None
    try:
        tif_bytes = base64.b64decode(tif_b64)
        with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as fh:
            fh.write(tif_bytes)
            tmp_raw = fh.name

        # Align to source DEM if available
        if source_dem_url:
            aligned = utils.raster_like_lazy(tmp_raw, source_dem_url)
            with tempfile.NamedTemporaryFile(suffix='_aligned.tif', delete=False) as fh2:
                tmp_aligned = fh2.name
            aligned.rio.to_raster(tmp_aligned)
            # os.remove(tmp_raw)
            # tmp_raw = None
            work_path = tmp_aligned
        else:
            work_path = tmp_raw

        state_bucket = s3_utils._STATE_BUCKET_(dict(user_id=gi.user_id, project_id=gi.project_id))
        s3_uri = f"{state_bucket}/rasters/{filename}"
        final_uri = utils.tif_to_cog3857(work_path, dst=s3_uri)
        # tif_to_cog3857 may return local path if already COG+3857 — upload manually in that case
        if not final_uri.startswith('s3://'):
            s3_utils.s3_upload(filename=final_uri, uri=s3_uri, remove_src=False)
            final_uri = s3_uri

    except Exception as exc:
        return jsonify({"error": f"Processing failed: {exc}"}), 500
    finally:
        for p in [tmp_raw, tmp_aligned]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    src_url = utils.s3uri_to_https(final_uri)
    layer_title = title if title else utils.juststem(filename)

    gi.register_layer(
        src=final_uri,
        title=layer_title,
        description=description,
        layer_type='raster',
        metadata=utils.raster_specs(final_uri),
    )

    return jsonify({'src': src_url, 'title': layer_title}), 200