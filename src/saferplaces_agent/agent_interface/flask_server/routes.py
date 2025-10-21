import os
import re
import uuid
import json

import geopandas as gpd

from markupsafe import escape

from flask import Response, request, jsonify, current_app as app

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
    return jsonify("Welcome to the SaferPlaces Agent Interface!"), 200


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
        metadata = dict()
    elif layer_type == 'raster':
        layer_render_src = utils.tif_to_cog3857(layer_src)
        metadata = utils.raster_specs(layer_render_src)
            
    else:
        return jsonify({"error": f"Layer type '{layer_type}' is not supported"}), 400
        
    return jsonify({'src': utils.s3uri_to_https(layer_render_src), 'metadata': metadata}), 200