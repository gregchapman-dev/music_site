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
    'humdrum': 'krn',
    'mei': 'mei'
}


def getScore(req) -> m21.stream.Score:
    m21Score: m21.stream.Score
    fmt: str = req.form.get('format', '')
    scoreStr: str = req.form.get('score', '')
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
        m21Score = MusicEngine.toMusic21Score(scoreStr, 'upload.' + FMT_TO_FILE_EXT[fmt])
        if not m21Score.elements or not m21Score.isWellFormedNotation():
            print('Parsed score was not well-formed')
            abort(422, 'Parsed score was not well-formed')

        return m21Score

    except Exception:
        print('Score failed to parse')
        abort(422, 'Score failed to parse')


def produceResultScores(m21Score: m21.stream.Score) -> dict[str, str]:
    print('producing MusicXML')
    musicXML: str = MusicEngine.toMusicXML(m21Score)
    print('done producing MusicXML')
    print('producing Humdrum')
    humdrum: str = MusicEngine.toHumdrum(m21Score)
    print('done producing Humdrum')
    print('producing MEI')
    mei: str = MusicEngine.toMei(m21Score)
    print('done producing MEI')
    return {
        'musicxml': musicXML,
        'humdrum': humdrum,
        'mei': mei
    }


@app.route('/command', methods=['POST'])
def command() -> dict:
    result: dict[str, str] = {}
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

        transposeScore: m21.stream.Score = getScore(request)

        try:
            print('transposing music21 score')
            MusicEngine.transposeInPlace(transposeScore, semitones)
            result = produceResultScores(transposeScore)
        except Exception:
            print('Failed to transpose/export')
            abort(422, 'Failed to transpose/export')  # Unprocessable Content

    elif cmd == 'shopIt':
        arrangementTypeStr: str = request.form.get('arrangementType', '')
        print(f'command: arrangementTypeStr = {arrangementTypeStr}')
        if not arrangementTypeStr:
            print('Invalid shopIt (no arrangementType specified)')
            abort(400, 'Invalid shopIt (no arrangementType specified)')

        arrType: ArrangementType
        if arrangementTypeStr == 'UpperVoices':
            arrType = ArrangementType.UpperVoices
        elif arrangementTypeStr == 'LowerVoices':
            arrType = ArrangementType.LowerVoices
        else:
            print(f'Invalid shopIt (invalid arrangementType specified: "{arrangementTypeStr}")')
            abort(400,
                f'Invalid shopIt (invalid arrangementType specified: "{arrangementTypeStr}")')

        m21Score: m21.stream.Score = getScore(request)

        try:
            shoppedScore = MusicEngine.shopPillarMelodyNotesFromLeadSheet(m21Score, arrType)
            result = produceResultScores(shoppedScore)
        except Exception as e:
            print('Failed to shopIt/export')
            raise e
            # abort(422, 'Failed to shopIt/export')

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        if not chordOptionId:
            abort(400, 'Invalid chooseChordOption (no chordOptionId specified)')

        m21Score = getScore(request)
        try:
            MusicEngine.chooseChordOption(m21Score, chordOptionId)
            result = produceResultScores(m21Score)
        except Exception as e:
            print('Failed to chooseChordOption')
            raise e
            # abort(422, 'Failed to chooseChordOption)

    else:
        print('Invalid music engine command: {cmd}')
        abort(400, 'Invalid music engine command')

    # print(f'first 100 bytes of humdrum: {result["humdrum"][0:100]!r}')
    return result


@app.route('/score', methods=['POST'])
def score() -> dict:
    # files in formdata end up in request.files
    # all other formdata entries end up in request.form
    file = request.files['file']
    fileName: str = request.form['filename']
    fileData: str | bytes = file.read()
    print(f'PUT /score: first 100 bytes of {fileName}: {fileData[0:100]!r}')
    result: dict[str, str] = {}
    try:
        # import into music21
        print(f'PUT /score: parsing {fileName}')
        m21Score = MusicEngine.toMusic21Score(fileData, fileName)
        # export to various formats
        result = produceResultScores(m21Score)
    except Exception:
        print('Exception during parse/write')
        abort(422, 'Unprocessable music score')  # Unprocessable Content

    return result
