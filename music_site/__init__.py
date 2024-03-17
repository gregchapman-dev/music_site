import os
import sys

from flask import (
    Flask, redirect, render_template, request, session, url_for, abort
)

import music21 as m21

from .music_engine import MusicEngine

# Factory function.  flask knows how to find this (it has a standard
# name) when passed music_site on the flask command line, e.g.
#       flask --app music_site run --debug

# create and configure the app
app = Flask(__name__, instance_relative_config=True)
app.config.from_mapping(
    SECRET_KEY='dev',
    # DATABASE=os.path.join(app.instance_path, 'flaskr.sqlite'),
)

app.config.from_pyfile('config.py', silent=True)

# ensure the instance folder exists
try:
    os.makedirs(app.instance_path)
except OSError:
    pass

@app.route('/')
def index():
    return render_template('index.html')

# TODO: Put these in some sort of session storage so each user can have their own data.
gM21Score: m21.stream.Score | None = None
gMusicXmlScore: str = ''


@app.route('/command', methods=['POST'])
def command() -> dict:
    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form['command']
    if cmd == 'transpose':
        intervalName: str = request.form.get('interval', '')  # e.g. 'P4', 'P-5', etc
        if not intervalName:
            abort(400, 'Invalid transpose (no interval specified)')
        if gM21Score is None:
            abort(422, 'No score to transpose')  # Unprocessable Content

        try:
            MusicEngine.transposeInPlace(gM21Score, intervalName)
            gMusicXmlScore = MusicEngine.toMusicXML(gM21Score)
        except Exception:
            abort(422, 'Failed to transpose')  # Unprocessable Content
    else:
        abort(400, 'Invalid music engine command')

    return {
        'musicxml': gMusicXmlScore
    }

@app.route('/score', methods=['GET', 'POST'])
def score() -> dict:
    if request.method == 'POST':
        # files in formdata end up in request.files
        # all other formdata entries end up in request.form
        file = request.files['file']
        fileName: str = request.form['filename']
        fileData: str | bytes = file.read()
        print(f'PUT /score: first 40 bytes of {fileName}: {fileData[0:40]}')
        try:
            # import into music21 (saving the m21 score in gM21Score)
            print(f'PUT /score: parsing {fileName}')
            gM21Score = MusicEngine.toMusic21Score(fileData, fileName)
            # export to MusicXML (to a string) and save in gMusicXmlScore
            print('PUT /score: writing MusicXML string')
            gMusicXmlScore = MusicEngine.toMusicXML(gM21Score)
        except Exception:
            abort(422, 'Unprocessable music score')  # Unprocessable Content

    # return MusicXML score no matter whether GET or POST (so client can display it)
    print('PUT/GET /score returning MusicXML string in JSON[\'musicxml\']')
    return {
        'musicxml': gMusicXmlScore
    }
