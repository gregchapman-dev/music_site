import typing as t
import uuid
import zlib
from io import BytesIO

from flask import (
    Response,
    render_template,
    request,
    make_response,
    send_file
)

from converter21 import M21Utilities

from app import app, db
from app.models import AnonymousSession

from .music_engine_utilities import ArrangementType

from .music_engine import MusicEngine


# from app.forms import LoginForm

@app.route('/')
def index() -> Response | str:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        resp = make_response(render_template('index.html', meiInitialScore=''))
        # create a new database entry for a new anonymous session
        sessionUUID = str(uuid.uuid4())
        createNewAnonymousSession(sessionUUID)
        # return the sessionUUID as a cookie in the response
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
    session: AnonymousSession | None = getSession(sessionUUID, create=True)
    if t.TYPE_CHECKING:
        # because create=True will create one (and add to database)
        assert session is not None

    me: MusicEngine | None = getMusicEngineForSession(session, create=True)
    if t.TYPE_CHECKING:
        # because create=True will create one (and add to session in database)
        assert me is not None
    if me.m21Score is not None:
        meiStr: str = getMeiScoreForSession(session, me)
        if meiStr:
            return render_template('index.html', meiInitialScore=meiStr)

    # no score in session
    return render_template('index.html', meiInitialScore='')

@app.route('/command', methods=['POST'])
def command() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen

    me: MusicEngine | None = None
    result: dict[str, str] = {}

    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form.get('command', '')
    print(f'command: cmd = "{cmd}"')
    if cmd == 'transpose':
        semitonesStr: str = request.form.get('semitones', '')
        print(f'command: semitonesStr = {semitonesStr}')
        if not semitonesStr:
            return produceErrorResult('Invalid transpose (no semitones specified)')

        semitones: int | None = None
        try:
            semitones = int(semitonesStr)
        except Exception:
            pass

        if semitones is None:
            return produceErrorResult(
                f'Invalid transpose (invalid semitones specified: "{semitonesStr}")'
            )

        me = getMusicEngineForSession(session)
        if me is None or me.m21Score is None:
            return produceErrorResult('No score to transpose')

        try:
            print('transposing music21 score')
            me.transposeInPlace(semitones)
            result = produceResultScores(me, session)
        except Exception as e:
            return produceErrorResult(f'Failed to transpose/export: {e}')

    elif cmd == 'shopIt':
        arrangementTypeStr: str = request.form.get('arrangementType', '')
        print(f'command: arrangementTypeStr = {arrangementTypeStr}')
        if not arrangementTypeStr:
            return produceErrorResult('Invalid shopIt (no arrangementType specified)')

        arrType: ArrangementType
        if arrangementTypeStr == 'UpperVoices':
            arrType = ArrangementType.UpperVoices
        elif arrangementTypeStr == 'LowerVoices':
            arrType = ArrangementType.LowerVoices
        else:
            return produceErrorResult(
                f'Invalid shopIt (invalid arrangementType specified: "{arrangementTypeStr}")'
            )

        me = getMusicEngineForSession(session)
        if me is None or me.m21Score is None:
            return produceErrorResult('No score to shop')

        try:
            me.shopIt(arrType)
            result = produceResultScores(me, session)
        except Exception as e:
            return produceErrorResult(f'Failed to shopIt: {e}')

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        if not chordOptionId:
            return produceErrorResult('Invalid chooseChordOption (no chordOptionId specified)')

        me = getMusicEngineForSession(session)
        if me is None or me.m21Score is None:
            return produceErrorResult('No score to modify')

        try:
            me.chooseChordOption(chordOptionId)
            result = produceResultScores(me, session)
        except Exception as e:
            return produceErrorResult(f'Failed to chooseChordOption: {e}')

    else:
        result = produceErrorResult(f'Invalid music engine command: {cmd}')

    # print(f'first 100 bytes of humdrum: {result["humdrum"][0:100]!r}')
    return result

@app.route('/score', methods=['POST'])
def score() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen

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
        me: MusicEngine = MusicEngine.fromFileData(fileData, fileName)
        result = produceResultScores(me, session)
    except Exception as e:
        return produceErrorResult(f'Exception during parse/write: {e}')

    return result

@app.route('/musicxml', methods=['GET'])
def musicxml() -> Response | dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen
    musicxmlStr: str = getMusicXMLScoreForSession(session)
    musicxmlBytes: bytes = musicxmlStr.encode('utf-8')
    return send_file(BytesIO(musicxmlBytes), download_name='Score.musicxml', as_attachment=True)

@app.route('/humdrum', methods=['GET'])
def humdrum() -> Response | dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen
    humdrumStr: str = getHumdrumScoreForSession(session)
    humdrumBytes: bytes = humdrumStr.encode('utf-8')
    return send_file(BytesIO(humdrumBytes), download_name='Score.krn', as_attachment=True)

@app.route('/mei', methods=['GET'])
def mei() -> Response | dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen

    meiStr: str = getMeiScoreForSession(session)
    meiBytes: bytes = meiStr.encode('utf-8')
    return send_file(BytesIO(meiBytes), download_name='Score.mei', as_attachment=True)


def createNewAnonymousSession(sessionUUID: str) -> AnonymousSession:
    session = AnonymousSession(sessionUUID=sessionUUID)
    db.session.add(session)
    db.session.commit()
    return session

def getSession(sessionUUID: str, create: bool = False) -> AnonymousSession | None:
    data: AnonymousSession | None = db.session.get(AnonymousSession, sessionUUID)
    if create and data is None:
        data = createNewAnonymousSession(sessionUUID)

    return data

def getMusicEngineForSession(
    session: AnonymousSession,
    create: bool = False
) -> MusicEngine | None:
    me: MusicEngine | None = None
    if session.musicEngine is not None:
        frozenEngine: bytes = session.musicEngine
        if frozenEngine:
            me = MusicEngine.thaw(frozenEngine)
    if create and me is None:
        # nothing in session, make one (and update the database)
        me = MusicEngine()
        session.musicEngine = me.freeze()
        db.session.commit()
    return me

def getStringFromCompressedBytes(zBytes: bytes) -> str:
    output: str = ''
    if zBytes:
        try:
            output = zlib.decompress(zBytes).decode('utf-8')
        except Exception:
            pass
    return output

def getCompressedBytesFromString(string: str) -> bytes:
    return zlib.compress(string.encode('utf-8'))

def getMeiScoreForSession(session: AnonymousSession, me: MusicEngine | None = None) -> str:
    output: str = ''
    if session.mei is not None:
        output = getStringFromCompressedBytes(session.mei)
    else:
        if me is None:
            me = getMusicEngineForSession(session)
            if me is None:
                return output
        output = me.toMei()
        session.mei = getCompressedBytesFromString(output)
        db.session.commit()

    return output


def getHumdrumScoreForSession(session: AnonymousSession, me: MusicEngine | None = None) -> str:
    output: str = ''
    if session.humdrum is not None:
        output = getStringFromCompressedBytes(session.humdrum)
    else:
        if me is None:
            me = getMusicEngineForSession(session)
            if me is None:
                return output
        output = me.toHumdrum()
        session.humdrum = getCompressedBytesFromString(output)
        db.session.commit()
    return output


def getMusicXMLScoreForSession(session: AnonymousSession, me: MusicEngine | None = None) -> str:
    output: str = ''
    if session.musicxml is not None:
        output = getStringFromCompressedBytes(session.musicxml)
    else:
        if me is None:
            me = getMusicEngineForSession(session)
            if me is None:
                return output
        output = me.toMusicXML()
        session.musicxml = getCompressedBytesFromString(output)
        db.session.commit()
    return output


def storeMusicEngineForSession(
    me: MusicEngine,
    session: AnonymousSession,
    clearCachedFormats: bool = True
):
    print('freezing m21Score')
    frozenEngine: bytes = me.freeze()
    print('done freezing music engine')
    session.musicEngine = frozenEngine

    if clearCachedFormats:
        # clear the cached formats of the score
        session.mei = b''
        session.humdrum = b''
        session.musicxml = b''

    db.session.commit()


def storeMeiScoreForSession(meiStr: str, session: AnonymousSession):
    session.mei = getCompressedBytesFromString(meiStr)
    db.session.commit()


def storeHumdrumScoreForSession(humdrumStr: str, session: AnonymousSession):
    session.humdrum = getCompressedBytesFromString(humdrumStr)
    db.session.commit()

def storeMusicXMLScoreForSession(musicXMLStr: str, session: AnonymousSession):
    session.musicxml = getCompressedBytesFromString(musicXMLStr)
    db.session.commit()

def produceResultScores(me: MusicEngine, session: AnonymousSession) -> dict[str, str]:
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

    storeMusicEngineForSession(me, session, clearCachedFormats=True)
    if meiStr:
        storeMeiScoreForSession(meiStr, session)

    return {
        'mei': meiStr
    }

def produceErrorResult(error: str) -> dict[str, str]:
    return {
        'appendToConsole': error
    }
