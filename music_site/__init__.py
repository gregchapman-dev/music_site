import typing as t
import os
import sys
import re
import uuid
import base64
import zlib
from io import BytesIO

from flask import (
    Flask,
    Response,
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
fakePerSessionDB: dict[str, dict[str, bytes]] = {}

# ensure the instance folder exists
try:
    os.makedirs(app.instance_path)
except OSError:
    pass


@app.route('/')
def index() -> Response | str:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        resp = make_response(render_template('index.html', meiInitialScore=''))
        sessionUUID = str(uuid.uuid4())
        fakePerSessionDB[sessionUUID] = {}
        oneMonth: int = 31 * 24 * 3600
        resp.set_cookie(
            'sessionUUID',
            value=sessionUUID,
            max_age=oneMonth,
            secure=True,
            httponly=True
        )
        return resp

    # there is a sessionUUID; respond with the resulting score (mei for now, maybe humdrum later)
    meiStr: str = getMeiScoreForSession(sessionUUID)
    if not meiStr:
        # no mei score in session
        return render_template('index.html', meiInitialScore='')

    return render_template('index.html', meiInitialScore=meiStr)


FMT_TO_FILE_EXT: dict = {
    'musicxml': 'musicxml',
    'humdrum': 'krn',
    'mei': 'mei'
}


# "database" access routines (keyed by sessionUUID)
def getSessionData(sessionUUID: str) -> dict[str, bytes]:
    # 888 someday this will do database stuff, so we can restart the server and not lose sessions.
    if sessionUUID not in fakePerSessionDB:
        fakePerSessionDB[sessionUUID] = {}
    return fakePerSessionDB[sessionUUID]


def getM21ScoreForSession(sessionUUID: str) -> m21.stream.Score | None:
    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    m21Score: m21.stream.Score | None = None
    if 'm21Score' not in sessionData:
        print('No session score found.')
        return None

    frozenScore: str | bytes = sessionData['m21Score']
    if t.TYPE_CHECKING:
        # 'frozen' always contains bytes
        assert isinstance(frozenScore, bytes)

    m21Score = MusicEngine.thawScore(frozenScore)
    if m21Score is None or not m21Score.elements or not m21Score.isWellFormedNotation():
        print('Parsed score was not well-formed')
        return None

    return m21Score

def getTextScoreForSession(key: str, sessionUUID: str, cacheIt: bool = True) -> str:
    if key not in ('mei', 'humdrum', 'musicxml'):
        return ''

    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    output: str = ''

    if key in sessionData and sessionData[key]:
        meiBytes = sessionData[key]
        if meiBytes:
            try:
                output = zlib.decompress(meiBytes).decode('utf-8')
            except Exception:
                pass
        if output:
            return output

    # couldn't use sessionData[key] (cached format), regenerate it from 'm21Score'
    m21Score: m21.stream.Score | None = getM21ScoreForSession(sessionUUID)
    if m21Score is None:
        print('Download is invalid: no score uploaded yet.')
        abort(400, 'Download is invalid: no score uploaded yet.')

    if not m21Score.elements or not m21Score.isWellFormedNotation():
        print('Download is invalid: score is not well-formed')
        abort(422, 'Download is invalid: score is not well-formed')

    if key == 'mei':
        output = MusicEngine.toMei(m21Score)
    elif key == 'musicxml':
        output = MusicEngine.toMusicXML(m21Score)
    else:
        output = MusicEngine.toHumdrum(m21Score)

    if cacheIt:
        try:
            sessionData[key] = zlib.compress(output.encode('utf-8'))
        except Exception:
            pass

    return output


def getMeiScoreForSession(sessionUUID: str) -> str:
    return getTextScoreForSession('mei', sessionUUID)


def getHumdrumScoreForSession(sessionUUID: str) -> str:
    return getTextScoreForSession('humdrum', sessionUUID)


def getMusicXMLScoreForSession(sessionUUID: str) -> str:
    return getTextScoreForSession('musicxml', sessionUUID)


def storeM21ScoreForSession(
    m21Score: m21.stream.Score,
    sessionUUID: str,
    clearCachedFormats: bool = True
):
    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    print('freezing m21Score')
    frozenScore: bytes = MusicEngine.freezeScore(m21Score)
    print('done freezing m21Score')
    sessionData['m21Score'] = frozenScore

    if clearCachedFormats:
        # clear the cached formats of the score
        sessionData['mei'] = b''
        sessionData['humdrum'] = b''
        sessionData['musicxml'] = b''


def storeTextScoreForSession(key: str, scoreStr: str, sessionUUID: str):
    if key not in ('mei', 'humdrum', 'musicxml'):
        return
    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    sessionData[key] = zlib.compress(scoreStr.encode('utf-8'))


def storeMeiScoreForSession(meiStr: str, sessionUUID: str):
    storeTextScoreForSession('mei', meiStr, sessionUUID)


def storeHumdrumScoreForSession(humdrumStr: str, sessionUUID: str):
    storeTextScoreForSession('humdrum', humdrumStr, sessionUUID)


def storeMusicXMLScoreForSession(musicXMLStr: str, sessionUUID: str):
    storeTextScoreForSession('musicxml', musicXMLStr, sessionUUID)


def produceResultScores(m21Score: m21.stream.Score, sessionUUID: str, throughMei: bool = False):
    meiStr: str = ''
    if throughMei:
        # we need to import again from MEI, so we get nice xml:ids in
        # our music21 chordsym (etc) ids.
        print('producing MEI')
        meiStr = MusicEngine.toMei(m21Score)
        print('done producing MEI')
        print('regenerating m21Score')
        m21Score = MusicEngine.toMusic21Score(meiStr, 'file.mei')
        print('done regenerating m21Score')

    if not meiStr:
        print('producing MEI')
        meiStr = MusicEngine.toMei(m21Score)
        print('done producing MEI')

    storeM21ScoreForSession(m21Score, sessionUUID, clearCachedFormats=True)
    storeMeiScoreForSession(meiStr, sessionUUID)

    return {
        'mei': meiStr
    }


@app.route('/command', methods=['POST'])
def command() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    result: dict[str, bytes] = {}
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

        transposeScore: m21.stream.Score | None = getM21ScoreForSession(sessionUUID)
        if transposeScore is None:
            abort(400, 'No score to transpose')

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

        m21Score: m21.stream.Score | None = getM21ScoreForSession(sessionUUID)
        if m21Score is None:
            abort(400, 'No score to shop')

        try:
            shoppedScore = MusicEngine.shopPillarMelodyNotesFromLeadSheet(m21Score, arrType)
            result = produceResultScores(shoppedScore, sessionUUID, throughMei=True)
        except Exception as e:
            print('Failed to shopIt/export')
            raise e
            # abort(422, 'Failed to shopIt/export')

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        if not chordOptionId:
            abort(400, 'Invalid chooseChordOption (no chordOptionId specified)')

        m21Score = getM21ScoreForSession(sessionUUID)
        if m21Score is None:
            abort(400, 'No score to modify')

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
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    # files in formdata end up in request.files
    # all other formdata entries end up in request.form
    file = request.files['file']
    fileName: str = request.form['filename']
    fileData: str | bytes = file.read()
    print(f'PUT /score: first 100 bytes of {fileName}: {fileData[0:100]!r}')
    result: dict[str, bytes] = {}
#     try:
    # import into music21
    print(f'PUT /score: parsing {fileName}')
    m21Score = MusicEngine.toMusic21Score(fileData, fileName)
    # export to various formats
    result = produceResultScores(m21Score, sessionUUID, throughMei=True)
#     except Exception:
#         print('Exception during parse/write')
#         abort(422, 'Unprocessable music score')  # Unprocessable Content

    return result

@app.route('/musicxml', methods=['GET'])
def musicxml() -> Response:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen
    musicxmlStr: str = getMusicXMLScoreForSession(sessionUUID)
    musicxmlBytes: bytes = musicxmlStr.encode()
    return send_file(BytesIO(musicxmlBytes), download_name='Score.musicxml', as_attachment=True)

@app.route('/humdrum', methods=['GET'])
def humdrum() -> Response:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen
    humdrumStr: str = getHumdrumScoreForSession(sessionUUID)
    humdrumBytes: bytes = humdrumStr.encode()
    return send_file(BytesIO(humdrumBytes), download_name='Score.krn', as_attachment=True)

@app.route('/mei', methods=['GET'])
def mei() -> Response:
    # Almost never used; if there is an mei score for the session, the client generally
    # already has it (and knows it). The only time this API is called (at the moment) is
    # if there is no mei score for the session, and this API will fail.
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    meiStr: str = getMeiScoreForSession(sessionUUID)
    meiBytes: bytes = meiStr.encode()
    return send_file(BytesIO(meiBytes), download_name='Score.mei', as_attachment=True)
