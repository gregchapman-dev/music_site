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

# TODO: Put these in some sort of session storage so each user can have their own data,
# TODO: and we don't have to receive/reparse the MusicXML for each command.
# TODO: gM21Score: m21.stream.Score | None = None
# TODO: gMusicXmlScore: str = ''


@app.route('/command', methods=['POST'])
def command() -> dict:
    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form.get('command', '')
    print(f'command: cmd = "{cmd}"')
    if cmd == 'transpose':
        intervalName: str = request.form.get('interval', '')  # e.g. 'P4', 'P-5', etc
        print(f'command: interval = {intervalName}')
        if not intervalName:
            abort(400, 'Invalid transpose (no interval specified)')

        musicXML: str = request.form.get('musicxml', '')
        transposedMusicXML: str = ''
        if not musicXML:
            abort(422, 'No score to transpose')

        try:
            print('importing MusicXML')
            m21Score: m21.stream.Score = MusicEngine.toMusic21Score(musicXML, 'upload.musicxml')
            print('transposing music21 score')
            MusicEngine.transposeInPlace(m21Score, intervalName)
            print('producing MusicXML')
            transposedMusicXML = MusicEngine.toMusicXML(m21Score)
            print('done producing MusicXML')
        except Exception:
            abort(422, 'Failed to transpose')  # Unprocessable Content
    else:
        abort(400, 'Invalid music engine command')

    print('returning MusicXML in response JSON')
    return {
        'musicxml': transposedMusicXML
    }

@app.route('/score', methods=['GET', 'POST'])
def score() -> dict:
    if request.method == 'POST':
        # files in formdata end up in request.files
        # all other formdata entries end up in request.form
        file = request.files['file']
        fileName: str = request.form['filename']
        fileData: str | bytes = file.read()
        print(f'PUT /score: first 40 bytes of {fileName}: {fileData[0:40]!r}')
        musicXMLStr: str = ''
        try:
            # import into music21 (saving the m21 score in gM21Score)
            print(f'PUT /score: parsing {fileName}')
            m21Score = MusicEngine.toMusic21Score(fileData, fileName)
            # export to MusicXML (to a string)
            print('PUT /score: writing MusicXML string')
            musicXMLStr = MusicEngine.toMusicXML(m21Score)
        except Exception:
            print('Exception during parse/write')
            abort(422, 'Unprocessable music score')  # Unprocessable Content

    # return MusicXML no matter whether GET or POST (so client can display it)
    if not musicXMLStr:
        # no exception, but musicXMLStr is empty
        print('No MusicXML generated')
        abort(422, 'No MusicXML generated')  # Unprocessable Content

    print('PUT/GET /score returning MusicXML string in JSON[\'musicxml\']')
    return {
        'musicxml': musicXMLStr
    }
