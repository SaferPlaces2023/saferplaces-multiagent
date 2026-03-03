import os
import re
import uuid
import json

import geopandas as gpd

from markupsafe import escape

from flask import Response, request, jsonify, current_app as app, render_template, send_from_directory

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
    
    # DOC: Filter the updated state to only include keys that were requested
    updated_state = gi.set_state(state_updates)
    updated_state = { k: v for k, v in updated_state.items() if k in state_updates }
    
    return jsonify(ensure_json_state(updated_state)), 200


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
    
    if stream_mode:
        def generate():
            for e in gi.user_prompt(prompt=prompt, state_updates={'avaliable_tools': []}):
                yield json.dumps(gi.conversation_handler.chat2json(chat=e)) + "\n"
        
        return Response(generate(), mimetype='text/plain')
    
    else:
        gen = (
            gi.conversation_handler.chat2json(chat=e)
            for e in gi.user_prompt(prompt=prompt, state_updates={'avaliable_tools': []})
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

    to_be_registered = layer_data.get('register', False)
    if to_be_registered:
        gi: GraphInterface = app.__GRAPH_REGISTRY__.get(thread_id)
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
    
    if layer_type == 'vector':
        layer_render_src = utils.vector_to_geojson4326(layer_src)
        metadata = utils.vector_specs(layer_render_src)
    elif layer_type == 'raster':
        layer_render_src = utils.tif_to_cog3857(layer_src)
        metadata = utils.raster_specs(layer_render_src)
            
    else:
        return jsonify({"error": f"Layer type '{layer_type}' is not supported"}), 400
        
    return jsonify({'src': utils.s3uri_to_https(layer_render_src), 'metadata': metadata}), 200


# DOC: === Cesium routes ====

CESIUM_DIST = "../../../../../safer-3d-cesium/demo/dist"

@app.route("/cesium-viewer", methods=["POST"])
def cesium_index():
    user_id = request.form.get("user_id")
    project_id = request.form.get("project_id")
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
    
    layer_registry = gi.get_state('layer_registry', [])

    is_wd_layer = lambda layer_data: layer_data.get('metadata', dict()).get('surface_type') == 'water-depth'
    wd_layers = [l for l in layer_registry if is_wd_layer(l)]
    
    if len(wd_layers) == 0:
        return jsonify({"skipping": "No water-depth layers found"}), 200
    
    wd3d_uri = gi.cesium_handler.preprocess_wd(wd_layers[0]['src'])
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