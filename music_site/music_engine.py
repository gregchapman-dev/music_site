import typing as t
from enum import IntEnum, auto

from flask import (
    Blueprint, redirect, render_template, request, session, url_for, abort
)

import music21 as m21

# from flaskr.db import get_db

bp = Blueprint('music_engine', __name__, url_prefix='/music_engine')


m21Score: m21.stream.Score | None = None
musicxmlScore: str = ''


@bp.route('/command', methods=['POST'])
def music_engine() -> dict:
    # it's a command (like 'transpose'), maybe with some command-defined parameters
    cmd: str = request.form['command']
    if cmd == 'transpose':
        intervalName: str = request.form.get('interval', '')  # e.g. 'P4', 'P-5', etc
        if not intervalName:
            abort(400, 'Invalid transpose (no interval specified)')
        if m21Score is None:
            abort(422, 'No score to transpose')  # Unprocessable Content

        try:
            transposeInPlace(m21Score, intervalName)
            musicxmlScore = toMusicXML(m21Score)
        except:
            abort(422, 'Failed to transpose')  # Unprocessable Content
    else:
        abort(400, 'Invalid music engine command')

    return {
        'musicxml': musicxmlScore
    }

@bp.route('/music_engine/score', methods=['GET', 'POST'])
def music_engine_score() -> dict:
    if request.method == 'POST':
        scoreData: str | bytes = request.form['score']
        fileName: str = request.form['filename']

        try:
            # import into music21 (saving the m21 score in m21Score)
            m21Score = m21.converter.parse(scoreData, format=format, forceSource=True)
            if t.TYPE_CHECKING:
                assert isinstance(m21Score, m21.stream.Score)

            # export to MusicXML (to a string) and save in musicxmlScore
            musicxmlScore = toMusicXML(m21Score)
        except Exception:
            abort(422, 'Unprocessable music score')  # Unprocessable Content

    # return MusicXML score no matter whether GET or POST (so client can display it)
    return {
        'musicxml': musicxmlScore
    }

# -----  engine routines below this line do not reference file globals -----

# class ArrangementType (IntEnum):
#     UpperVoices = auto()
#     MixedVoices = auto()
#     LowerVoices = auto()
#
# class VocalRange:
#     def __init__(self, lowest: m21.pitch.Pitch, highest: m21.pitch.Pitch):
#         self.lowest: m21.pitch.Pitch = lowest
#         self.highest: m21.pitch.Pitch = highest
#
# class VocalRangeInfo:
#     # Contains vocal range info about a single vocal part
#     # fullRange is lowest pitch seen and highest pitch seen
#     # tessitura is range within which the vocal part only leaves briefly (TBD)
#     # posts is a list of pitches (might be empty) that the part sings with a
#     #   very long single note duration.
#     def __init__(
#         self,
#         fullRange: VocalRange,
#         tessitura: VocalRange,
#         posts: list[m21.pitch.Pitch]
#     ):
#         self.fullRange: VocalRange = fullRange
#         self.tessitura: VocalRange = tessitura
#         self.posts: list[m21.pitch.Pitch]

def toMusicXML(score: m21.stream.Score) -> str:
    output: str | bytes = m21.converter.toData(score, fmt='musicxml')
    if t.TYPE_CHECKING:
        assert isinstance(output, str)
    return output

def transposeInPlace(score: m21.stream.Score, intervalStr: str):
    score.transpose(intervalStr, inPlace=True)

# def convertLowerVoicesArrangementToUpperVoices(score: m21.stream.Score):
#     score.transpose('P4', inPlace=True)
#     setClefs(score, ArrangementType.UpperVoices)
#
#     # Walk the score looking for too-high or too-low notes, and revoice
#     # to tighten it up.  e.g. flip tenor and bari (switching octaves),
#     # or revoice to bring the bass up a bit, or whatever.
#     adjustVoicingForArrangementType(score, ArrangementType.UpperVoices)
#
# def convertUpperVoicesArrangementToLowerVoices(score: m21.stream.Score):
#     score.transpose('P-4', inPlace=True)
#     setClefs(score, ArrangementType.LowerVoices)
#
#     # Walk the score looking for opportunities to revoice for (lower) bass roots and
#     # higher tenor notes (flip tenor and bari, switching octaves)
#     adjustVoicingForArrangementType(score, ArrangementType.LowerVoices)
#
# def isFourPartVocalScore(score: m21.stream.Score) -> bool:
#     # Must have four parts, or two parts with two voices each.
#     pass
#
# def canBeUsedAsLeadSheet(score: m21.stream.Score) -> bool:
#     # Must have melody and chord symbols.
#     pass
#
# def fixClefs(score: m21.stream.Score, arrangementType: ArrangementType):
#     # This is important because there are lots of scores out there that
#     # have some parts in the wrong octave, and everyone just "knows"
#     # how to sing them correctly.  I need the notes to be right, so
#     # I can figure out what to do.
#     if arrangementType == ArrangementType.Upper:
#         # Fix the bass clef if it doesn't have that little 8 above it
#         # (and transpose those notes up an octave so they sound right!).
#         pass
#     elif arrangementType == ArrangementType.Lower:
#         # Fix the treble clef if it doesn't have that little 8 below it
#         # (and transpose those notes down an octave so they sound right!).
#         pass
#     # should we do anything for mixed arrangements?  I should look at some.
#     # Straight SATB is simple, but...
#
# def scanForRangeInfo(score: m21.stream.Score) -> list[VocalRangeInfo]:
#     pass
