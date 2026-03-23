import os
from flask import Flask
from flask_cors import CORS

from .. import __GRAPH_REGISTRY__ 

def create_app():
    app = Flask(
        __name__,
        static_folder=os.environ.get("FLASK_STATIC_FOLDER"),
        template_folder=os.environ.get("FLASK_TEMPLATE_FOLDER")
    )

    # DOC: Chiave segreta per firmare le sessioni
    app.secret_key = "The session is unavailable because no secret key was set. Set the secret_key on the application to something unique and secret."     # DOC: ahahah 

    # DOC: Enable CORS for all routes
    CORS(app)
    
    # DOC: If there will be a DB.. here we would initialize it
    app.__GRAPH_REGISTRY__ = __GRAPH_REGISTRY__

    with app.app_context():
        from . import routes
    
    return app