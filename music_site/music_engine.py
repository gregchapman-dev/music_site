import typing as t
from enum import IntEnum, auto

import music21 as m21
import converter21

# Register the Humdrum and MEI readers/writers from converter21
converter21.register()

# from flaskr.db import get_db

class ArrangementType (IntEnum):
    UpperVoices = auto()
    MixedVoices = auto()
    LowerVoices = auto()

class VocalRange:
    def __init__(self, lowest: m21.pitch.Pitch, highest: m21.pitch.Pitch):
        self.lowest: m21.pitch.Pitch = lowest
        self.highest: m21.pitch.Pitch = highest

class VocalRangeInfo:
    # Contains vocal range info about a single vocal part
    # fullRange is lowest pitch seen and highest pitch seen
    # tessitura is range within which the vocal part only leaves briefly (TBD)
    # posts is a list of pitches (might be empty) that the part sings with a
    #   very long single note duration.
    def __init__(
        self,
        fullRange: VocalRange,
        tessitura: VocalRange,
        posts: list[m21.pitch.Pitch]
    ):
        self.fullRange: VocalRange = fullRange
        self.tessitura: VocalRange = tessitura
        self.posts: list[m21.pitch.Pitch]

class MusicEngine:

    @staticmethod
    def toMusicXML(score: m21.stream.Score) -> str:
        output: str | bytes = m21.converter.toData(score, fmt='musicxml')
        if t.TYPE_CHECKING:
            assert isinstance(output, str)
        return output

    @staticmethod
    def toMusic21Score(fileData: str | bytes, fileName: str) -> m21.stream.Score:
        fmt: str = m21.common.findFormatFile(fileName)

        # Some parsers do this for you, but some do not.
        if isinstance(fileData, bytes):
            try:
                print('decoding utf-8')
                fileData = fileData.decode('utf-8')
            except UnicodeDecodeError:
                print('utf-8 failed; decoding latin-1')
                fileData = fileData.decode('latin-1')

        output = m21.converter.parse(fileData, format=fmt, forceSource=True)
        if t.TYPE_CHECKING:
            assert isinstance(output, m21.stream.Score)
        return output

    @staticmethod
    def transposeInPlace(score: m21.stream.Score, intervalStr: str):
        score.transpose(intervalStr, inPlace=True)

    @staticmethod
    def convertLowerVoicesArrangementToUpperVoices(score: m21.stream.Score):
        score.transpose('P4', inPlace=True)
        MusicEngine.setClefs(score, ArrangementType.UpperVoices)

        # Walk the score looking for too-high or too-low notes, and revoice
        # to tighten it up.  e.g. flip tenor and bari (switching octaves),
        # or revoice to bring the bass up a bit, or whatever.
        MusicEngine.adjustVoicingForArrangementType(score, ArrangementType.UpperVoices)

    @staticmethod
    def convertUpperVoicesArrangementToLowerVoices(score: m21.stream.Score):
        score.transpose('P-4', inPlace=True)
        MusicEngine.setClefs(score, ArrangementType.LowerVoices)

        # Walk the score looking for opportunities to revoice for (lower) bass roots and
        # higher tenor notes (flip tenor and bari, switching octaves)
        MusicEngine.adjustVoicingForArrangementType(score, ArrangementType.LowerVoices)

    @staticmethod
    def isFourPartVocalScore(score: m21.stream.Score) -> bool:
        # Must have four parts, or two parts with two voices each.
        return True

    @staticmethod
    def canBeUsedAsLeadSheet(score: m21.stream.Score) -> bool:
        # Must have melody and chord symbols.
        return True

    @staticmethod
    def setClefs(score: m21.stream.Score, arrangementType: ArrangementType):
        # insert (or change) clefs to be appropriate for the arrangementType.
        # No fixing of notes, though; fixClefs is for that.
        return

    @staticmethod
    def adjustVoicingForArrangementType(
        score: m21.stream.Score,
        arrangementType: ArrangementType
    ):
        return

    @staticmethod
    def fixClefs(score: m21.stream.Score, arrangementType: ArrangementType):
        # This is important because there are lots of scores out there that
        # have some parts in the wrong octave, and everyone just "knows"
        # how to sing them correctly.  I need the notes to be right, so
        # I can figure out what to do.
        if arrangementType == ArrangementType.UpperVoices:
            # Fix the bass clef if it doesn't have that little 8 above it
            # (and transpose those notes up an octave so they sound right!).
            pass
        elif arrangementType == ArrangementType.LowerVoices:
            # Fix the treble clef if it doesn't have that little 8 below it
            # (and transpose those notes down an octave so they sound right!).
            pass
        # should we do anything for mixed arrangements?  I should look at some.
        # Straight SATB is simple, but...

    @staticmethod
    def scanForRangeInfo(score: m21.stream.Score) -> list[VocalRangeInfo]:
        return []
