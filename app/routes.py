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
        print('index: no uuid')
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
        print(f'index response: new uuid = {sessionUUID}, no initial score')
        return resp

    print(f'index: uuid = {sessionUUID}')
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
            print(f'index response: uuid = {sessionUUID}, initialScore[:100] = {meiStr[:100]}')
            return render_template('index.html', meiInitialScore=meiStr)

    # no score in session
    print(f'index response: uuid = {sessionUUID}, no initialScore')
    return render_template('index.html', meiInitialScore='')

@app.route('/command', methods=['POST'])
def command() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    print(f'command: uuid = {sessionUUID}')
    if not sessionUUID:
        return produceErrorResult('No sessionUUID!')  # should never happen
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        return produceErrorResult('No session!')  # should never happen

    me: MusicEngine | None = getMusicEngineForSession(session)
    if me is None or me.m21Score is None:
        print(f'command-{sessionUUID} response: No score to modify')
        return produceErrorResult('No score to modify')

    result: dict[str, str] = {}

    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form.get('command', '')
    print(f'command: uuid = {sessionUUID}, cmd = {cmd}')
    if cmd == 'transpose':
        semitonesStr: str = request.form.get('semitones', '')
        print(f'{cmd}-{sessionUUID}: semitonesStr = {semitonesStr}')
        if not semitonesStr:
            print(f'{cmd}-{sessionUUID} response: Invalid transpose (no semitones specified)')
            return produceErrorResult('Invalid transpose (no semitones specified)')

        semitones: int | None = None
        try:
            semitones = int(semitonesStr)
        except Exception:
            pass

        if semitones is None:
            print(
                f'{cmd}-{sessionUUID} response: Invalid transpose '
                f'(invalid semitones: "{semitonesStr}")'
            )
            return produceErrorResult(
                f'Invalid transpose (invalid semitones specified: "{semitonesStr}")'
            )

        try:
            print(f'{cmd}-{sessionUUID}: transposing music21 score')
            me.transposeInPlace(semitones)
            print(f'{cmd}-{sessionUUID} response: success')
            result = produceResultScores(me, session)
        except Exception as e:
            print(f'{cmd}-{sessionUUID} response: Failed to transpose/export: {e}')
            return produceErrorResult(f'Failed to transpose/export: {e}')

    elif cmd == 'shopIt':
        arrangementTypeStr: str = request.form.get('arrangementType', '')
        print(f'{cmd}-{sessionUUID}: arrangementTypeStr = "{arrangementTypeStr}"')
        if not arrangementTypeStr:
            print(f'{cmd}-{sessionUUID} response: Invalid shopIt (no arrangementType specified)')
            return produceErrorResult('Invalid shopIt (no arrangementType specified)')

        arrType: ArrangementType
        if arrangementTypeStr == 'UpperVoices':
            arrType = ArrangementType.UpperVoices
        elif arrangementTypeStr == 'LowerVoices':
            arrType = ArrangementType.LowerVoices
        else:
            print(
                f'{cmd}-{sessionUUID} response: Invalid shopIt (invalid arrangementType '
                f'specified: "{arrangementTypeStr}")'
            )
            return produceErrorResult(
                f'Invalid shopIt (invalid arrangementType specified: "{arrangementTypeStr}")'
            )

        try:
            print(f'{cmd}-{sessionUUID} response: Shopping score')
            me.shopIt(arrType)
            result = produceResultScores(me, session)
            print(f'{cmd}-{sessionUUID} response: Success')
        except Exception as e:
            print(f'{cmd}-{sessionUUID} response: Failed to shop score: {e}')
            return produceErrorResult(f'Failed to shop score: {e}')

    elif cmd == 'chooseChordOption':
        chordOptionId: str | None = request.form.get('chordOptionId')
        print(f'{cmd}-{sessionUUID}: chordOptionId = "{chordOptionId}"')
        if not chordOptionId:
            print(
                f'{cmd}-{sessionUUID} response: Invalid chooseChordOption '
                '(no chordOptionId specified)'
            )
            return produceErrorResult('Invalid chooseChordOption (no chordOptionId specified)')

        # for logging only
        obj = me.m21Score.getElementById(chordOptionId)
        if obj is not None and hasattr(obj, 'content'):
            print(f'{cmd}-{sessionUUID}: chordOption content = "{obj.content}"')
        else:
            print(f'{cmd}-{sessionUUID}: chordOption has no content')
        # end for logging only

        try:
            me.chooseChordOption(chordOptionId)
            print(f'{cmd}-{sessionUUID} response: success')
            result = produceResultScores(me, session)
        except Exception as e:
            print(f'{cmd}-{sessionUUID} response: Failed to chooseChordOption: {e}')
            return produceErrorResult(f'Failed to chooseChordOption: {e}')

    elif cmd == 'hideChordOptions':
        try:
            print(f'hideChordOptions-{sessionUUID} response: Hiding chord options')
            me.hideChordOptions()
            result = produceResultScores(me, session)
            print(f'hideChordOptions-{sessionUUID} response: Success')
        except Exception as e:
            print(f'hideChordOptions-{sessionUUID} response: Failed to hide chord options: {e}')
            return produceErrorResult(f'Failed to hide chord options: {e}')

    elif cmd == 'undo':
        try:
            print(f'undo-{sessionUUID} response: Undoing')
            me.undo()
            result = produceResultScores(me, session)
            print(f'undo-{sessionUUID} response: Success')
        except Exception as e:
            print(f'undo-{sessionUUID} response: Failed to undo: {e}')
            return produceErrorResult(f'Failed to undo: {e}')

    elif cmd == 'redo':
        try:
            print(f'redo-{sessionUUID} response: Redoing')
            me.redo()
            result = produceResultScores(me, session)
            print(f'redo-{sessionUUID} response: Success')
        except Exception as e:
            print(f'redo-{sessionUUID} response: Failed to redo: {e}')
            return produceErrorResult(f'Failed to redo: {e}')

    else:
        print(f'command-{sessionUUID} response: Invalid music engine command: {cmd}')
        result = produceErrorResult(f'Invalid music engine command: {cmd}')

    return result

@app.route('/score', methods=['POST'])
def score() -> dict:
    sessionUUID: str | None = request.cookies.get('sessionUUID')
    if not sessionUUID:
        print('POST /score: no uuid')
        return produceErrorResult('No sessionUUID!')  # should never happen
    print(f'POST /score: uuid = {sessionUUID}')
    session: AnonymousSession | None = getSession(sessionUUID)
    if session is None:
        print(f'POST /score-{sessionUUID}: No session!')
        return produceErrorResult('No session!')  # should never happen

    # files in formdata end up in request.files
    # all other formdata entries end up in request.form
    file = request.files['file']
    fileName: str = request.form['filename']
    fileData: str | bytes = file.read()
    print(f'POST /score-{sessionUUID}: first 100 bytes of {fileName}: {fileData[0:100]!r}')
    result: dict[str, str] = {}
    try:
        # import into music21
        print(f'POST /score-{sessionUUID}: parsing {fileName}')
        me: MusicEngine = MusicEngine.fromFileData(fileData, fileName)
        print(f'POST /score-{sessionUUID}: parsing successful')
        result = produceResultScores(me, session)
    except Exception as e:
        print(f'POST /score-{sessionUUID}: Exception during parse/write: {e}')
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

# Support functions
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
    sessionUUID: str = session.sessionUUID
    if session.musicEngine is not None:
        frozenEngine: bytes = session.musicEngine
        if frozenEngine:
            print(f'getMusicEngineForSession-{sessionUUID} thawing musicEngine')
            me = MusicEngine.thaw(frozenEngine)
            if me is not None:
                print(f'getMusicEngineForSession-{sessionUUID} success')
            else:
                print(f'getMusicEngineForSession-{sessionUUID} failed to thaw musicEngine')

    if create and me is None:
        # nothing in session, make one (and update the database)
        print(f'getMusicEngineForSession-{sessionUUID} creating an empty musicEngine')
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
    if session.mei:
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
    if session.humdrum:
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
    if session.musicxml:
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
