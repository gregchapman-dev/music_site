from flask import (
    Flask,
    Response,
    redirect,
    render_template,
    request,
    make_response,
    session,
    url_for,
    send_file
)

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from config import Config

# These are "exports" from app module
from .music_engine_utilities import MusicEngineException
from .music_engine_utilities import ArrangementType
from .music_engine_utilities import PartName
from .music_engine_utilities import VocalRange
from .music_engine_utilities import MusicEngineUtilities
from .music_engine import MusicEngine
from .music_engine import ScoreState

# Factory function.  flask knows how to find this (it has a standard
# name) when passed music_site on the flask command line, e.g.
#       flask --app music_site run --debug

# create and configure the app
app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

from app import routes, models, commands  # pylint: disable=wrong-import-order
