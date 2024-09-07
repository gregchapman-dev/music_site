import typing as t
import os
import sys
import re
import uuid
import base64
import zlib
from io import BytesIO
from copy import deepcopy

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

from converter21 import M21Utilities

from .music_engine_utilities import MusicEngineException
from .music_engine_utilities import ArrangementType
from .music_engine_utilities import PartName
from .music_engine_utilities import FourNotes
from .music_engine_utilities import VocalRange
from .music_engine_utilities import VocalRangeInfo
from .music_engine_utilities import MusicEngineUtilities

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


def getMusicEngineForSession(sessionUUID: str) -> MusicEngine | None:
    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    me: MusicEngine | None = None
    if 'musicEngine' not in sessionData:
        print('No session score found.')
        return None

    frozenEngine: bytes = sessionData['musicEngine']
    me = MusicEngine.thaw(frozenEngine)
    return me

def getTextScoreForSession(key: str, sessionUUID: str, cacheIt: bool = True) -> str:
    if key not in ('mei', 'humdrum', 'musicxml'):
        return ''

    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    output: str = ''

    if key in sessionData:
        zBytes: bytes = sessionData[key]
        if zBytes:
            try:
                output = zlib.decompress(zBytes).decode('utf-8')
            except Exception:
                pass
        if output:
            return output

    # couldn't use sessionData[key] (cached format), regenerate it from 'musicEngine'
    me: MusicEngine | None = getMusicEngineForSession(sessionUUID)
    if me is None or me.m21Score is None:
        print('Download is invalid: there is no current score.')
        abort(422, 'Download is invalid: there is no current score.')

    if not me.m21Score.elements or not me.m21Score.isWellFormedNotation():
        print('Download is invalid: score is not well-formed')
        abort(422, 'Download is invalid: score is not well-formed')

    if key == 'mei':
        output = me.toMei()
    elif key == 'musicxml':
        output = me.toMusicXML()
    else:
        output = me.toHumdrum()

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


def storeMusicEngineForSession(
    me: MusicEngine,
    sessionUUID: str,
    clearCachedFormats: bool = True
):
    sessionData: dict[str, bytes] = getSessionData(sessionUUID)
    print('freezing m21Score')
    frozenEngine: bytes = me.freeze()
    print('done freezing music engine')
    sessionData['musicEngine'] = frozenEngine

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


def produceResultScores(me: MusicEngine, sessionUUID: str):
    # fill out all the xml:ids that are missing,
    # and copy _all_ xml_id to id (except for voice.id).
    # This is so the m21Score and the MEI score have
    # the same ids no matter what (so clicks on the
    # website will map correctly to m21Score objects).
    if me.m21Score is not None:
        M21Utilities.assureAllXmlIdsAndIds(me.m21Score)

        print('producing MEI')
        meiStr = me.toMei()
        print('done producing MEI')

    storeMusicEngineForSession(me, sessionUUID, clearCachedFormats=True)
    if meiStr:
        storeMeiScoreForSession(meiStr, sessionUUID)

    return {
        'mei': meiStr
    }


@app.route('/command', methods=['POST'])
def command() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    me: MusicEngine | None = None
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

        me = getMusicEngineForSession(sessionUUID)
        if me is None or me.m21Score is None:
            abort(400, 'No score to transpose')

        try:
            print('transposing music21 score')
            me.transposeInPlace(semitones)
            result = produceResultScores(me, sessionUUID)
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

        me = getMusicEngineForSession(sessionUUID)
        if me is None or me.m21Score is None:
            abort(400, 'No score to shop')

        try:
            me.shopIt(arrType)
            result = produceResultScores(me, sessionUUID)
        except Exception as e:
            # print('Failed to shop; perhaps leadsheet doesn\'t have an obvious melody or chords')
            # abort(
            #     422,
            #     'Failed to shop; perhaps leadsheet doesn\'t have an obvious melody or chords'
            # )
            raise e

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        if not chordOptionId:
            abort(400, 'Invalid chooseChordOption (no chordOptionId specified)')

        me = getMusicEngineForSession(sessionUUID)
        if me is None or me.m21Score is None:
            abort(400, 'No score to modify')

        try:
            me.chooseChordOption(chordOptionId)
            result = produceResultScores(me, sessionUUID)
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
    me: MusicEngine = MusicEngine.fromFileData(fileData, fileName)
    # export to various formats
    result = produceResultScores(me, sessionUUID)
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
    musicxmlBytes: bytes = musicxmlStr.encode('utf-8')
    return send_file(BytesIO(musicxmlBytes), download_name='Score.musicxml', as_attachment=True)

@app.route('/humdrum', methods=['GET'])
def humdrum() -> Response:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen
    humdrumStr: str = getHumdrumScoreForSession(sessionUUID)
    humdrumBytes: bytes = humdrumStr.encode('utf-8')
    return send_file(BytesIO(humdrumBytes), download_name='Score.krn', as_attachment=True)

@app.route('/mei', methods=['GET'])
def mei() -> Response:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        abort(400, 'No sessionUUID!')  # should never happen

    meiStr: str = getMeiScoreForSession(sessionUUID)
    meiBytes: bytes = meiStr.encode('utf-8')
    return send_file(BytesIO(meiBytes), download_name='Score.mei', as_attachment=True)
