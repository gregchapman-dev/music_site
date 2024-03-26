import os
import sys
import re

from flask import (
    Flask, redirect, render_template, request, session, url_for, abort
)

import music21 as m21

from .music_engine import MusicEngine
from .music_engine import MusicEngineException
from .music_engine import FourNotes
from .music_engine import ArrangementType
from .music_engine import VocalRange
from .music_engine import VocalRangeInfo

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
# TODO: and we don't have to receive/reparse the MusicXML (or Humdrum) for each command.
# TODO: gM21Score: m21.stream.Score | None = None
# TODO: gMusicXmlScore: str = ''

FMT_TO_FILE_EXT: dict = {
    'musicxml': 'musicxml',
    'humdrum': 'krn'
}

@app.route('/command', methods=['POST'])
def command() -> dict:
    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form.get('command', '')
    print(f'command: cmd = "{cmd}"')
    if cmd == 'transpose':
        semitonesStr: str = request.form.get('semitones', '')
        print(f'command: semitonesStr = {semitonesStr}')
        if not semitonesStr:
            print('Invalid transpose (no semitones specified)')
            abort(400, 'Invalid transpose (no semitones specified)')
        semitones: int | None = None
        try:
            semitones = int(semitonesStr)
        except Exception:
            pass

        if semitones is None:
            print(f'Invalid transpose (invalid semitones specified: "{semitonesStr}")')
            abort(400, 'Invalid transpose (invalid semitones specified)')

        fmt: str = request.form.get('format', '')
        scoreStr: str = request.form.get('score', '')
        if not scoreStr:
            print('No score to transpose')
            abort(422, 'No score to transpose')
        if not fmt or fmt not in FMT_TO_FILE_EXT:
            print('Unknown format score')
            abort(422, 'Unknown format score')

        try:
            print(f'first 100 bytes of scoreStr: {scoreStr[0:100]!r}')
            if '\r\n' in scoreStr:
                # somebody messed with my Humdrum line ends
                print('gotta replace CRLF with LF in scoreStr')
                scoreStr = re.sub('\r\n', '\n', scoreStr)
                print(f'first 100 bytes of munged scoreStr: {scoreStr[0:100]!r}')

            print(f'importing scoreStr, fmt={fmt}')
            m21Score: m21.stream.Score = (
                MusicEngine.toMusic21Score(scoreStr, 'upload.' + FMT_TO_FILE_EXT[fmt])
            )
            if not m21Score.isWellFormedNotation():
                print('Humdrum failed to parse well-formed score')
            print('transposing music21 score')
            MusicEngine.transposeInPlace(m21Score, semitones)
            print('producing MusicXML')
            transposedMusicXML = MusicEngine.toMusicXML(m21Score)
            print('done producing MusicXML')
            print('producing Humdrum')
            transposedHumdrum: str = MusicEngine.toHumdrum(m21Score)
            print('done producing Humdrum')
        except Exception:
            print('Failed to transpose/export')
            abort(422, 'Failed to transpose/export')  # Unprocessable Content
    else:
        print('Invalid music engine command: {cmd}')
        abort(400, 'Invalid music engine command')

    print('returning MusicXML+Humdrum in response JSON')
    print(f'first 100 bytes of transposedHumdrum: {transposedHumdrum[0:100]!r}')
    return {
        'musicxml': transposedMusicXML,
        'humdrum': transposedHumdrum
    }

@app.route('/score', methods=['GET', 'POST'])
def score() -> dict:
    if request.method == 'POST':
        # files in formdata end up in request.files
        # all other formdata entries end up in request.form
        file = request.files['file']
        fileName: str = request.form['filename']
        fileData: str | bytes = file.read()
        print(f'PUT /score: first 100 bytes of {fileName}: {fileData[0:100]!r}')
        musicXMLStr: str = ''
        try:
            # import into music21 (saving the m21 score in gM21Score)
            print(f'PUT /score: parsing {fileName}')
            m21Score = MusicEngine.toMusic21Score(fileData, fileName)
            # export to MusicXML (to a string)
            print('PUT /score: writing MusicXML string')
            musicXMLStr = MusicEngine.toMusicXML(m21Score)
            print('PUT /score: writing Humdrum string')
            humdrumStr = MusicEngine.toHumdrum(m21Score)
        except Exception:
            print('Exception during parse/write')
            abort(422, 'Unprocessable music score')  # Unprocessable Content

    # return MusicXML no matter whether GET or POST (so client can display it)
    if not musicXMLStr:
        # no exception, but musicXMLStr is empty
        print('No MusicXML generated')
        abort(422, 'No MusicXML generated')  # Unprocessable Content

    print('PUT/GET /score returning MusicXML+Humdrum strings')
    print(f'first 100 bytes of humdrumStr: {humdrumStr[0:100]!r}')
    return {
        'musicxml': musicXMLStr,  # for display by client (music21j only displays MusicXML)
        'humdrum': humdrumStr     # for upload by client with commands (much smaller)
    }
