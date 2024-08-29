import os
import sys
import re
import uuid
import base64
from io import BytesIO

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    make_response,
    session,
    url_for,
    abort,
    send_file
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

# fakePerSessionDB is keyed by sessionUUID, and the value is a session dict that
# contains some of the following:
# {
#   'm21Score': pickledAndZippedMusic21Score,
#   'mei': meiString,
#   'humdrum': humdrumString,
#   'musicxml': musicxmlString
# }
# Because it is faked with a dict, everytime we restart the server, it goes away.
# It also doesn't support multiple instances of the server (since each will have
# its own fake DB).  But good enough for now, to test out the new flow.
fakePerSessionDB: dict[str, dict[str, str]] = {}

# ensure the instance folder exists
try:
    os.makedirs(app.instance_path)
except OSError:
    pass

@app.route('/')
def index():
    nothingDataURL: str = 'data:plain/text,NothingHere'
    sessionUUID: str = request.cookies.get('sessionUUID')
    if not sessionUUID:
        resp = make_response(render_template('index.html', meiDataURL=nothingDataURL))
        sessionUUID = str(uuid.uuid4())
        fakePerSessionDB[sessionUUID] = {}
        oneMonth: int = 31 * 24 * 3600
        resp.set_cookie(
            'sessionUUID',
            value=sessionUUID,
            max_age=oneMonth,
            secure=False,
            httponly=True
        )
        return resp

    if sessionUUID not in fakePerSessionDB:
        fakePerSessionDB[sessionUUID] = {}

    # there is a sessionUUID; respond with the resulting score (mei for now, maybe humdrum later)
    sessionData: dict[str, str] = fakePerSessionDB[sessionUUID]
    if 'mei' not in sessionData:
        # no mei score in sessionData
        return render_template('index.html', meiDataURL=nothingDataURL)

    meiStr: str = sessionData['mei']
    if meiStr:
        b64Str: str = base64.urlsafe_b64encode(meiStr.encode('utf-8')).decode('utf-8')
        meiDataURL: str = 'data:plain/text;base64,' + b64Str
    return render_template('index.html', meiDataURL=meiDataURL)


FMT_TO_FILE_EXT: dict = {
    'musicxml': 'musicxml',
    'humdrum': 'krn',
    'mei': 'mei'
}


def getScore(sessionUUID: str) -> m21.stream.Score:
    if sessionUUID not in fakePerSessionDB:
        fakePerSessionDB[sessionUUID] = {}

    sessionData: dict[str, str] = fakePerSessionDB[sessionUUID]
    # 888 someday the m21 score will be in sessionData (frozen)
    m21Score: m21.stream.Score
    fmt: str = 'mei'
    if fmt not in sessionData:
        print('No score to transpose')
        abort(422, 'No score to transpose')

    scoreStr: str = sessionData[fmt]

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


def produceResultScores(m21Score: m21.stream.Score, sessionUUID: str):
    print('producing MusicXML')
    musicXML: str = MusicEngine.toMusicXML(m21Score)
    print('done producing MusicXML')
    print('producing Humdrum')
    humdrum: str = MusicEngine.toHumdrum(m21Score)
    print('done producing Humdrum')
    print('producing MEI')
    mei: str = MusicEngine.toMei(m21Score)
    print('done producing MEI')
    fakePerSessionDB[sessionUUID]['musicxml'] = musicXML
    fakePerSessionDB[sessionUUID]['mei'] = mei
    fakePerSessionDB[sessionUUID]['humdrum'] = humdrum
    return {
        'mei': mei
    }


@app.route('/command', methods=['POST'])
def command() -> dict:
    sessionUUID: str = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

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

        transposeScore: m21.stream.Score = getScore(sessionUUID)

        try:
            print('transposing music21 score')
            MusicEngine.transposeInPlace(transposeScore, semitones)
            result = produceResultScores(transposeScore, sessionUUID)
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

        m21Score: m21.stream.Score = getScore(sessionUUID)

        try:
            shoppedScore = MusicEngine.shopPillarMelodyNotesFromLeadSheet(m21Score, arrType)
            result = produceResultScores(shoppedScore, sessionUUID)
        except Exception as e:
            print('Failed to shopIt/export')
            raise e
            # abort(422, 'Failed to shopIt/export')

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        if not chordOptionId:
            abort(400, 'Invalid chooseChordOption (no chordOptionId specified)')

        m21Score = getScore(sessionUUID)
        try:
            MusicEngine.chooseChordOption(m21Score, chordOptionId)
            result = produceResultScores(m21Score, sessionUUID)
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
    sessionUUID: str = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    # files in formdata end up in request.files
    # all other formdata entries end up in request.form
    file = request.files['file']
    fileName: str = request.form['filename']
    fileData: str | bytes = file.read()
    print(f'PUT /score: first 100 bytes of {fileName}: {fileData[0:100]!r}')
    result: dict[str, str] = {}
#     try:
    # import into music21
    print(f'PUT /score: parsing {fileName}')
    m21Score = MusicEngine.toMusic21Score(fileData, fileName)
    # export to various formats
    result = produceResultScores(m21Score, sessionUUID)
#     except Exception:
#         print('Exception during parse/write')
#         abort(422, 'Unprocessable music score')  # Unprocessable Content

    return result

@app.route('/musicxml', methods=['GET'])
def musicxml() -> str:
    sessionUUID: str = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    if sessionUUID not in fakePerSessionDB:
        fakePerSessionDB[sessionUUID] = {}

    sessionData: dict[str, str] = fakePerSessionDB[sessionUUID]
    if 'musicxml' not in sessionData:
        abort(400, 'Download is invalid: no score uploaded yet.')

    musicxmlStr: str = sessionData['musicxml']
    return send_file(BytesIO(musicxmlStr), download_name='Shopped.musicxml', as_attachment=True )

@app.route('/humdrum', methods=['GET'])
def humdrum() -> str:
    sessionUUID: str = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    if sessionUUID not in fakePerSessionDB:
        fakePerSessionDB[sessionUUID] = {}

    sessionData: dict[str, str] = fakePerSessionDB[sessionUUID]
    if 'humdrum' not in sessionData:
        abort(400, 'Download is invalid: no score uploaded yet.')

    humdrumStr: str = sessionData['humdrum']
    return send_file(BytesIO(humdrumStr), download_name='Shopped.krn', as_attachment=True )
