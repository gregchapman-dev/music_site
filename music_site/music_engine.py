import typing as t
# import sys
import pathlib
import re
import zipfile
from enum import Enum, IntEnum, auto
from io import BytesIO
from copy import copy, deepcopy
from collections.abc import Sequence

import music21 as m21
from music21.common.numberTools import OffsetQL, opFrac

import converter21
from converter21 import M21Utilities

# Register the Humdrum and MEI readers/writers from converter21
converter21.register()

# from flaskr.db import get_db

class MyStrEnum(str, Enum):
    def __new__(cls, value: str):
        return str.__new__(cls, value)


class MusicEngineException(Exception):
    pass


MAX_INT: int = 9223372036854775807
MAX_OFFSETQL: OffsetQL = opFrac(MAX_INT)


class PitchName:
    # used instead of pitch.name (str), so we can compare using enharmonic
    # equality with no octave (pitchClass)
    def __init__(self, name: str):
        self.name: str = name
        self.pitch: m21.pitch.Pitch = m21.pitch.Pitch(name)

    def __eq__(self, other) -> bool:
        if not isinstance(other, PitchName):
            return False
        if self.pitch.pitchClass == other.pitch.pitchClass:
            # ignores octave, because pitch.name ignores octave
            return True
        return False

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return self.name


class ArrangementType (IntEnum):
    UpperVoices = auto()
    # MixedVoices = auto()  # not yet supported; user might need to specify part ranges?
    LowerVoices = auto()


class PartName (MyStrEnum):
    Tenor = 'tenor'
    Lead = 'lead'
    Bari = 'bari'
    Bass = 'bass'


class Chord(Sequence):
    # The group of pitches from a named chord, ordered/keyed by role in the chord.
    _ROLES: dict[str, int] = {
        'root': 1,
        'second': 2,
        'third': 3,
        'fourth': 4,
        'fifth': 5,
        'sixth': 6,
        'seventh': 7,
        'ninth': 9,
        'eleventh': 11,
        'thirteenth': 13
    }

    def __init__(
        self,
        cs: m21.harmony.ChordSymbol,
    ):
        self.sym: m21.harmony.ChordSymbol = deepcopy(cs)
        self.pitches: list[m21.pitch.Pitch] = []
        self.roleToPitchNames: dict[int, PitchName] = {}
        self.preferredBassPitchName: PitchName | None = None

        if isinstance(self.sym, m21.harmony.NoChord):
            return

        bass: m21.pitch.Pitch = self.sym.bass()
        if bass is not None and bass.name != self.sym.root().name:
            # we have a specified bass note, perhaps not in the main chord
            # Stash off it's name as the preferred bass pitchName, and
            # recompute the chord pitches as if there was no bass specified
            # (by setting the bass to the root).
            self.preferredBassPitchName = PitchName(bass.name)
            self.sym.bass(self.sym.root())
            M21Utilities._updatePitches(self.sym)

        # tuple[role=1..13, pitch]

        # self.sym.pitches have octaves, and are ordered diatonically, so they will
        # help us figure out the difference between role=4 and role=11 (.getChordStep
        # won't help us at all).
        pitchesForRole: list[m21.pitch.Pitch | None] = [None] * 14
        for i in range(1, 8):
            try:
                pitchesForRole[i] = self.sym.getChordStep(i)
            except m21.chord.ChordException:
                pass

        # Note that self.pitches may not contain all of self.sym.pitches, since
        # if self.sym.pitches includes, say, a flat 9th and a sharp 9th, we'll
        # only pick up one of them via getChordStep().  That's OK for our
        # purposes.
        for p in self.sym.pitches:
            if p in pitchesForRole:
                self.pitches.append(p)

        pitchNames: list[PitchName] = [PitchName(p.name) for p in self.pitches]

        # loop over pitches and pitchesForRole, moving pitchesForRole elements to match pitches
        role: int = 1
        pitchIdx: int = 0
        while pitchIdx < len(pitchNames):
            pitchName: PitchName = pitchNames[pitchIdx]
            pitchForRole: m21.pitch.Pitch | None = pitchesForRole[role]
            while pitchForRole is None:
                role += 1
                pitchForRole = pitchesForRole[role]
            if PitchName(pitchForRole.name) == pitchName:
                role += 1
                pitchIdx += 1
                continue

            # pitchForRole.name != pitchName, so we assume that we need to
            # move this pitchForRole entry up to a higher role (e.g. 4 -> 11)
            higherRole: int = role + 7
            if higherRole >= len(pitchesForRole):
                # shouldn't happen, but sometimes music21 does this (I've seen it
                # add 9 and then add 7 nearly an octave higher).  Just leave it
                # where it is, down an octave from where music21 said.
                role += 1
                pitchIdx += 1
                continue

            pitchesForRole[higherRole] = pitchesForRole[role]
            pitchesForRole[role] = None
            role += 1  # don't increment pitchIdx, we need to process it again with next role

        for role, pitchForRole in enumerate(pitchesForRole):
            if pitchForRole is not None:
                self.roleToPitchNames[role] = PitchName(pitchForRole.name)

    def __len__(self) -> int:
        return len(self.roleToPitchNames)

    def __getitem__(self, idx: int | str | slice) -> t.Any:  # -> PitchName | None (pitchName)
        if isinstance(idx, str):
            if idx not in self._ROLES:
                keysStr = ', '.join(self._ROLES.keys())
                raise IndexError(f'Chord role must be int (1-13) or str ({keysStr}).')
            idx = self._ROLES[idx]

        if not isinstance(idx, int) or idx < 1 or idx > 13:
            raise IndexError(f'Chord role must be int (1-13) or str ({keysStr}).')

        return self.roleToPitchNames.get(idx, None)

class FourNotes(Sequence):
    # intended to be read-only snapshot of a (possibly in-progress) chord
    def __init__(
        self,
        tenor: m21.note.Note | m21.note.Rest | None = None,
        lead: m21.note.Note | m21.note.Rest | None = None,
        bari: m21.note.Note | m21.note.Rest | None = None,
        bass: m21.note.Note | m21.note.Rest | None = None
    ):
        self._tenor: m21.note.Note | m21.note.Rest | None = tenor
        self._lead: m21.note.Note | m21.note.Rest | None = lead
        self._bari: m21.note.Note | m21.note.Rest | None = bari
        self._bass: m21.note.Note | m21.note.Rest | None = bass

    @property
    def tenor(self) -> m21.note.Note | m21.note.Rest | None:
        return self._tenor

    @property
    def lead(self) -> m21.note.Note | m21.note.Rest | None:
        return self._lead

    @property
    def bari(self) -> m21.note.Note | m21.note.Rest | None:
        return self._bari

    @property
    def bass(self) -> m21.note.Note | m21.note.Rest | None:
        return self._bass

    def __len__(self) -> int:
        return 4

    def __getitem__(self, idx: int | str | slice) -> t.Any:  # m21.note.Note|m21.note.Rest|None:
        if idx in (0, PartName.Tenor):
            return self.tenor
        if idx in (1, PartName.Lead):
            return self.lead
        if idx in (2, PartName.Bari):
            return self.bari
        if idx in (3, PartName.Bass):
            return self.bass

        # we don't support slicing (or out-of-range idx)
        raise IndexError(idx)

    def getAvailablePitchNames(self, chord: Chord) -> list[PitchName]:
        # We assume that bass harmonization doesn't call this, and (also) will have
        # already used the /bass note if specified.
        availableRoleToPitchNames: dict[int, PitchName] = (
            MusicEngine.getChordVocalParts(chord, PitchName(self[PartName.Lead].pitch.name))
        )
        bass: PitchName | None = None
        roleToPitchNamesWithoutBass: dict[int, PitchName] = copy(availableRoleToPitchNames)
        if 0 in roleToPitchNamesWithoutBass:
            bass = roleToPitchNamesWithoutBass[0]
            del roleToPitchNamesWithoutBass[0]

        doubleTheRoot: bool = False
        if len(availableRoleToPitchNames) == 3:
            doubleTheRoot = True
        elif len(roleToPitchNamesWithoutBass) == 3:
            if bass in roleToPitchNamesWithoutBass.values():
                # there's really only 3 notes (in an inversion)
                doubleTheRoot = True

        for n in self:
            if isinstance(n, m21.note.Note):
                nPitchName: PitchName = PitchName(n.pitch.name)
                if nPitchName in roleToPitchNamesWithoutBass.values():
                    if doubleTheRoot:
                        if nPitchName == roleToPitchNamesWithoutBass.get(1, None):
                            # don't remove the root until you see the root a second time
                            doubleTheRoot = False
                            continue

                    removeRole: int = 0  # there is no role 0
                    for k, v in roleToPitchNamesWithoutBass.items():
                        if v == nPitchName:
                            removeRole = k
                            break
                    if removeRole != 0:
                        roleToPitchNamesWithoutBass.pop(removeRole, None)
                elif nPitchName == bass:
                    pass
                else:
                    print('n.pitch.name not in availableRoleToPitchNames, why did we use it then?')

        return list(roleToPitchNamesWithoutBass.values())


class FourVoices(Sequence):
    def __init__(
        self,
        tenor: m21.stream.Voice,
        lead: m21.stream.Voice,
        bari: m21.stream.Voice,
        bass: m21.stream.Voice
    ):
        self._tenor: m21.stream.Voice = tenor
        self._lead: m21.stream.Voice = lead
        self._bari: m21.stream.Voice = bari
        self._bass: m21.stream.Voice = bass

    @property
    def tenor(self) -> m21.stream.Voice:
        return self._tenor

    @property
    def lead(self) -> m21.stream.Voice:
        return self._lead

    @property
    def bari(self) -> m21.stream.Voice:
        return self._bari

    @property
    def bass(self) -> m21.stream.Voice:
        return self._bass

    def __len__(self) -> int:
        return 4

    def __getitem__(self, idx: int | str | slice) -> t.Any:  # m21.stream.Voice:
        if idx in (0, PartName.Tenor):
            return self.tenor
        if idx in (1, PartName.Lead):
            return self.lead
        if idx in (2, PartName.Bari):
            return self.bari
        if idx in (3, PartName.Bass):
            return self.bass

        # we don't support slicing (or out-of-range idx)
        raise IndexError(idx)


class VocalRange:
    def __init__(
        self,
        lowest: m21.pitch.Pitch,
        highest: m21.pitch.Pitch
    ):
        self.lowest: m21.pitch.Pitch = lowest
        self.highest: m21.pitch.Pitch = highest

    def isTooLow(self, p: m21.pitch.Pitch) -> bool:
        return p < self.lowest

    def isTooHigh(self, p: m21.pitch.Pitch) -> bool:
        return p > self.highest

    def isOutOfRange(self, p: m21.pitch.Pitch) -> bool:
        return self.isTooLow(p) or self.isTooHigh(p)

    def isInRange(self, p: m21.pitch.Pitch) -> bool:
        return not self.isOutOfRange(p)


PART_RANGES: dict[ArrangementType, dict[PartName, VocalRange]] = {
    ArrangementType.LowerVoices: {
        PartName.Tenor: VocalRange(m21.pitch.Pitch('B-3'), m21.pitch.Pitch('B-4')),
        PartName.Lead: VocalRange(m21.pitch.Pitch('D3'), m21.pitch.Pitch('F4')),
        PartName.Bari: VocalRange(m21.pitch.Pitch('B2'), m21.pitch.Pitch('F4')),
        PartName.Bass: VocalRange(m21.pitch.Pitch('F2'), m21.pitch.Pitch('B-3')),
    },
    ArrangementType.UpperVoices: {
        PartName.Tenor: VocalRange(m21.pitch.Pitch('F#4'), m21.pitch.Pitch('F5')),
        PartName.Lead: VocalRange(m21.pitch.Pitch('A3'), m21.pitch.Pitch('C5')),
        PartName.Bari: VocalRange(m21.pitch.Pitch('A3'), m21.pitch.Pitch('C5')),
        PartName.Bass: VocalRange(m21.pitch.Pitch('E-3'), m21.pitch.Pitch('D4')),
    },
    # There are no standard ranges for mixed voice barbershop: know the vocal ranges
    # of the group you are arranging for.
}


class VocalRangeInfo:
    # Contains vocal range info about a single vocal part
    # fullRange is lowest pitch seen and highest pitch seen
    # tessitura is range within which the vocal part only leaves briefly (TBD)
    # posts is a list of pitches (might be empty) that the part sings with a
    #   very long single note duration.
    def __init__(
        self,
        s: m21.stream.Stream | None
    ):
        self.fullRange: VocalRange | None = None
        # self.tessitura: VocalRange | None = None
        # self.posts: list[m21.pitch.Pitch] = []
        if s is None:
            return

        # Scan all notes in s, gathering up fullRange (and someday tessitura and posts,
        # but those require duration analysis and s.stripTies() which gets complicated).

        # FWIW, A0 is the lowest note on the piano, and C8 is the highest.  Should be
        # well beyond any reasonable vocal range max/min.
        for i, n in enumerate(s[m21.note.Note]):
            if i == 0:
                self.fullRange = VocalRange(deepcopy(n.pitch), deepcopy(n.pitch))
                continue

            if t.TYPE_CHECKING:
                assert isinstance(self.fullRange, VocalRange)

            if n.pitch < self.fullRange.lowest:
                self.fullRange.lowest = deepcopy(n.pitch)
            if n.pitch > self.fullRange.highest:
                self.fullRange.highest = deepcopy(n.pitch)

    def getSemitonesAdjustments(
        self,
        arrType: ArrangementType
    ) -> tuple[int, int, int]:
        if self.fullRange is None:
            raise MusicEngineException('getSemitonesAdjustments called on empty VocalRange')

        goalRange: VocalRange = PART_RANGES[arrType][PartName.Lead]
        currRange: VocalRange = self.fullRange

        # We do all of our computations in terms of semitones-too-low, because we want
        # to return semitonesTooLow (say, 3) which means we have to transpose
        # by that many (say, 3) semitones (i.e. 3 semitones up).  If the current
        # range is too high (by say, 5 semitones), semitonesTooLow will be negative
        # (say, -5), because we have to transpose by that many (say, -5) semitones
        # (i.e. 5 semitones down).

        # How many semitones too low (relative to goalRange) are both ends of the range?
        # We take the float because we will have to do averaging and rounding.
        lowEndSemitonesTooLow: float = goalRange.lowest.ps - currRange.lowest.ps
        highEndSemitonesTooLow: float = goalRange.highest.ps - currRange.highest.ps

        semitonesTooLow: int = round((lowEndSemitonesTooLow + highEndSemitonesTooLow) / 2.)

        return semitonesTooLow, round(lowEndSemitonesTooLow), round(highEndSemitonesTooLow)

    def getTranspositionSemitones(
        self,
        arrType: ArrangementType,
        returnLowAndHigh: bool = False
    ) -> int:
        if self.fullRange is None:
            raise MusicEngineException('getTranspositionSemitones called on empty VocalRange')

        semitonesTooLow: int = self.getSemitonesAdjustments(arrType)[0]
        return semitonesTooLow

    def getAdjustedPartRanges(self) -> dict[ArrangementType, dict[PartName, VocalRange]]:
        def transposeBySemitones(
            vr: VocalRange,
            lowSemitones: int,
            highSemitones: int
        ) -> VocalRange:
            newVR: VocalRange = VocalRange(
                vr.lowest.transpose(lowSemitones),
                vr.highest.transpose(highSemitones)
            )
            return newVR

        newRanges: dict[ArrangementType, dict[PartName, VocalRange]] = {}
        for arrType, rangeDict in PART_RANGES.items():
            transpositionSemitones: int
            lowEndSemitonesTooLow: int
            highEndSemitonesTooLow: int
            transpositionSemitones, lowEndSemitonesTooLow, highEndSemitonesTooLow = (
                self.getSemitonesAdjustments(arrType)
            )
            # Assume transposition by transpositionSemitones
            lowEndSemitonesTooLow -= transpositionSemitones
            highEndSemitonesTooLow -= transpositionSemitones

            # if self.fullRange is less than normal part range, don't bother shrinking
            # the other parts (that would be silly).

            # if lowEndSemitonesTooLow is < 0, low end is too high: ignore it
            lowEndSemitonesTooLow = max(lowEndSemitonesTooLow, 0)
            # if highEndSemitonesTooLow is > 0, high end is too low; ignore it
            highEndSemitonesTooLow = min(highEndSemitonesTooLow, 0)

            newRanges[arrType] = {}
            for partName, vocalRange in rangeDict.items():
                newRanges[arrType][partName] = transposeBySemitones(
                    vocalRange, -lowEndSemitonesTooLow, -highEndSemitonesTooLow
                )

        return newRanges


class HarmonyRange:
    def __init__(
        self,
        startOffset: OffsetQL,
        endOffset: OffsetQL,
        chord: m21.harmony.ChordSymbol | None,  # None is same as NoChord
        melodyNote: m21.note.GeneralNote | None,  # None is same as invisible Rest
        chordMeas: m21.stream.Measure,
        melodyMeas: m21.stream.Measure,
        startOffsetInChordMeas: OffsetQL,
        startOffsetInMelodyMeas: OffsetQL
    ):
        if startOffset == MAX_OFFSETQL:
            raise MusicEngineException('oops')
        if endOffset == MAX_OFFSETQL:
            raise MusicEngineException('oops')

        self.startOffset: OffsetQL = startOffset
        self.endOffset: OffsetQL = endOffset
        self.durQL = opFrac(endOffset - startOffset)

        self.chordMeas: m21.stream.Measure = chordMeas
        self.melodyMeas: m21.stream.Measure = melodyMeas

        self.startOffsetInChordMeas: OffsetQL = startOffsetInChordMeas
        self.endOffsetInChordMeas: OffsetQL = opFrac(startOffsetInChordMeas + self.durQL)

        self.startOffsetInMelodyMeas = startOffsetInMelodyMeas
        self.endOffsetInMelodyMeas: OffsetQL = opFrac(startOffsetInMelodyMeas + self.durQL)

        self._chord: m21.harmony.ChordSymbol | None = chord
        self._melodyNote: m21.note.GeneralNote | None = melodyNote

    def __repr__(self) -> str:
        return (f'[{self.startOffset} .. {self.endOffset}]: '
            + f'chord={self._chord}, melodyNote={self._melodyNote}')

class HarmonyIterator:
    # One important requirement: clients must be able to modify the passed-in Parts
    # during iteration.  We actually iterate over lists instead of over streams.
    # And we do not return the actual ChordSymbol and Note, we just return startOffset
    # and endOffset (the client needs to go get/process the ChordSymbol portion and
    # Note portion themselves).
    # Note that while the Parts can be modified, we assume that measure offsets and
    # durations will not change, because we keep track of current measure (based on
    # offset) in both chords and melody during iteration, for performance purposes.
    def __init__(self, chords: m21.stream.Part, melody: m21.stream.Part):
        self.chords: m21.stream.Part = chords
        self.melody: m21.stream.Part = melody

        self.chordMeasures: list[m21.stream.Measure] = []
        self.chordMeasStartOffsets: list[OffsetQL] = []
        self.chordMeasEndOffsets: list[OffsetQL] = []
        self.chordMeasIdx = 0

        self.melodyMeasures: list[m21.stream.Measure] = []
        self.melodyMeasStartOffsets: list[OffsetQL] = []
        self.melodyMeasEndOffsets: list[OffsetQL] = []
        self.melodyMeasIdx = 0

        self.highestTime: OffsetQL = max(self.chords.highestTime, self.melody.highestTime)

        self.currRange: HarmonyRange | None = None

        self.currChordEnd = MAX_OFFSETQL
        self.currNoteEnd = MAX_OFFSETQL

        self.cList: list[m21.harmony.ChordSymbol] = []
        self.cStartOffsetList: list[OffsetQL] = []
        self.cEndOffsetList: list[OffsetQL] = []
        self.cNextIdx: int = 0
        self.mList: list[m21.note.Note] = []
        self.mStartOffsetList: list[OffsetQL] = []
        self.mEndOffsetList: list[OffsetQL] = []
        self.mNextIdx: int = 0

        self.lookAheadChord: m21.harmony.ChordSymbol | None = None
        self.lookAheadNote: m21.note.GeneralNote | None = None

    @staticmethod
    def computeMeasureLists(
        part: m21.stream.Part
    ) -> tuple[list[m21.stream.Measure], list[OffsetQL], list[OffsetQL]]:
        outMeasures: list[m21.stream.Measure] = []
        outStartOffsets: list[OffsetQL] = []
        outEndOffsets: list[OffsetQL] = []

        for meas in part.getElementsByClass(m21.stream.Measure):
            startOffset: OffsetQL = part.elementOffset(meas)
            endOffset: OffsetQL = opFrac(startOffset + meas.quarterLength)
            outMeasures.append(meas)
            outStartOffsets.append(startOffset)
            outEndOffsets.append(endOffset)

        return outMeasures, outStartOffsets, outEndOffsets

    def __iter__(self) -> 'HarmonyIterator':
        self.currRange = None

        self.chordMeasures, self.chordMeasStartOffsets, self.chordMeasEndOffsets = (
            self.computeMeasureLists(self.chords)
        )

        if self.melody is self.chords:
            self.melodyMeasures = self.chordMeasures
            self.melodyMeasStartOffsets = self.chordMeasStartOffsets
            self.melodyMeasEndOffsets = self.chordMeasEndOffsets
        else:
            self.melodyMeasures, self.melodyMeasStartOffsets, self.melodyMeasEndOffsets = (
                self.computeMeasureLists(self.melody)
            )

        self.chordMeasIdx = 0
        self.melodyMeasIdx = 0

        self.cList = list(
            self.chords.recurse().getElementsByClass(m21.harmony.ChordSymbol)
        )
        self.cStartOffsetList = []
        self.cEndOffsetList = []
        for c in self.cList:
            startOffset: OffsetQL = c.getOffsetInHierarchy(self.chords)
            endOffset: OffsetQL = opFrac(startOffset + c.quarterLength)
            self.cStartOffsetList.append(startOffset)
            self.cEndOffsetList.append(endOffset)
        self.cNextIdx = 0

        self.mList = list(
            self.melody
            .recurse()
            .getElementsByClass(m21.note.GeneralNote)
            .getElementsNotOfClass(m21.harmony.ChordSymbol)
        )
        self.mStartOffsetList = []
        self.mEndOffsetList = []
        for m in self.mList:
            startOffset = m.getOffsetInHierarchy(self.melody)
            endOffset = opFrac(startOffset + m.quarterLength)
            self.mStartOffsetList.append(startOffset)
            self.mEndOffsetList.append(endOffset)
        self.mNextIdx = 0

        self.lookAheadNote = self.getNextNote(throughLookAhead=False)
        self.lookAheadChord = self.getNextChord(throughLookAhead=False)
        return self

    def __next__(self) -> HarmonyRange:
        # bump self.currRange to the next range that has a new melody note/chordsym combination,
        # where the note or the chordsym might be None, but if both never will be (that's
        # the end of the score, and we stop iterating there instead)
        nextRange: HarmonyRange | None = self.getNextRange()
        if nextRange is None:
            raise StopIteration

        self.currRange = nextRange
        return self.currRange

    def bumpMelodyMeasure(self, mIndex: int):
        startOffset: OffsetQL = self.mStartOffsetList[mIndex]
        endOffset: OffsetQL = self.mEndOffsetList[mIndex]

        # find the melodyMeasure that contains both of these offsets.  Start at
        # self.melodyMeasIdx (ignore previous measures), and update self.melodyMeasIdx
        # to point to it.
        for idx in range(self.melodyMeasIdx, len(self.melodyMeasures)):
            if self.melodyMeasStartOffsets[idx] <= startOffset <= self.melodyMeasEndOffsets[idx]:
                if self.melodyMeasStartOffsets[idx] <= endOffset <= self.melodyMeasEndOffsets[idx]:
                    self.melodyMeasIdx = idx
                    return
        raise MusicEngineException('could not bump melody measure')

    def bumpChordMeasure(self, cIndex: int):
        startOffset: OffsetQL = self.cStartOffsetList[cIndex]
        endOffset: OffsetQL = self.cEndOffsetList[cIndex]
        # find the chordMeasure that contains both of these offsets.  Start at
        # self.chordMeasIdx (ignore previous measures), and update self.chordMeasIdx
        # to point to it.
        for idx in range(self.chordMeasIdx, len(self.chordMeasures)):
            if self.chordMeasStartOffsets[idx] <= startOffset <= self.chordMeasEndOffsets[idx]:
                if self.chordMeasStartOffsets[idx] <= endOffset <= self.chordMeasEndOffsets[idx]:
                    self.chordMeasIdx = idx
                    return
        raise MusicEngineException('could not bump chord measure')

    def getNextNote(self, throughLookAhead: bool = True) -> m21.note.GeneralNote | None:
        output: m21.note.GeneralNote | None = None
        if throughLookAhead:
            # normal call (from the outside); return what's in the lookahead, and
            # repopulate it, and bump the melodyMeas if necessary.
            output = self.lookAheadNote
            self.bumpMelodyMeasure(self.mNextIdx - 1)  # index of lookAheadNote/output
            self.lookAheadNote = self.getNextNote(throughLookAhead=False)
            return output

        # not through lookahead, just compute and return it.
        try:
            output = self.mList[self.mNextIdx]
            self.mNextIdx += 1
        except IndexError:
            output = None

        while output is not None and (
                output.duration.isGrace or isinstance(output, m21.harmony.ChordSymbol)):
            # chords and melody may be the exact same part, so when we're looking
            # for melody notes, we gotta skip chordsyms.  We also skip grace notes
            # as uninteresting for harmonization.
            try:
                output = self.mList[self.mNextIdx]
                self.mNextIdx += 1
            except IndexError:
                output = None

        return output

    def getNextChord(self, throughLookAhead: bool = True) -> m21.harmony.ChordSymbol | None:
        output: m21.harmony.ChordSymbol | None = None
        if throughLookAhead:
            # normal call (from the outside); return what's in the lookahead, and
            # repopulate it, and bump the chordMeas if necessary.
            output = self.lookAheadChord
            self.bumpChordMeasure(self.cNextIdx - 1)  # index of lookAheadChord/output
            self.lookAheadChord = self.getNextChord(throughLookAhead=False)
            return output

        # not through lookahead, just compute and return it.
        try:
            output = self.cList[self.cNextIdx]
            self.cNextIdx += 1
        except IndexError:
            output = None

        return output

    def getNextRange(self) -> HarmonyRange | None:
        if self.currRange is None:
            # starting at beginning of score, first peek a bit at the first note and chord
            # before actually getting them.
            if self.lookAheadNote is None and self.lookAheadChord is None:
                # There are no notes in self.melody, and no notes in self.chords
                # Stop iterating.
                return None

            firstNoteOffset: OffsetQL = MAX_OFFSETQL
            firstNoteQL: OffsetQL = MAX_OFFSETQL
            if self.lookAheadNote is not None:
                firstNoteOffset = self.lookAheadNote.getOffsetInHierarchy(self.melody)
                firstNoteQL = self.lookAheadNote.quarterLength

            firstChordOffset: OffsetQL = MAX_OFFSETQL
            firstChordQL: OffsetQL = MAX_OFFSETQL
            if self.lookAheadChord is not None:
                firstChordOffset = self.lookAheadChord.getOffsetInHierarchy(self.chords)
                firstChordQL = self.lookAheadChord.quarterLength

            firstChord: m21.harmony.ChordSymbol | None = None
            firstNote: m21.note.GeneralNote | None = None

            if firstNoteOffset == 0:
                if firstChordOffset == 0:
                    # we want to get both firstNote and firstChord (we peeked at them
                    # and they are both useful at the start).
                    firstChord = self.getNextChord()
                    firstNote = self.getNextNote()

                    # harmony is [0..lowestEndOffset]
                    lowestEndOffset: OffsetQL = min(
                        firstNoteQL, firstChordQL
                    )
                    self.currRange = HarmonyRange(
                        opFrac(0), lowestEndOffset, firstChord, firstNote,
                        self.chordMeasures[self.chordMeasIdx],
                        self.melodyMeasures[self.melodyMeasIdx],
                        opFrac(
                            opFrac(0)
                            - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                        ),
                        opFrac(
                            opFrac(0)
                            - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                        )
                    )
                    self.currChordEnd = firstChordQL
                    self.currNoteEnd = firstNoteQL
                    return self.currRange

                # we only want the first note (the chord we peeked at is for later)
                firstNote = self.getNextNote()
                # harmony is [0..min(firstChordStart,firstNoteEnd], with no chord
                lowestOffset: OffsetQL = min(firstChordOffset, firstNoteQL)
                self.currRange = HarmonyRange(
                    opFrac(0), lowestOffset, None, firstNote,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        opFrac(0)
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        opFrac(0)
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = firstChordQL
                self.currNoteEnd = firstNoteQL
                return self.currRange

            if firstChordOffset == 0:
                # firstNoteOffset is not 0, so we only need to get the firstChord
                firstChord = self.getNextChord()
                # harmony is [0..firstNoteOffset], with no note
                # (this seems unlikely, since firstNote would normally be a rest
                # in this case, but we handle it anyway)
                self.currRange = HarmonyRange(
                    opFrac(0), firstNoteOffset, firstChord, None,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        opFrac(0)
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        opFrac(0)
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = firstChordQL
                self.currNoteEnd = firstNoteQL
                return self.currRange

            # neither offset is 0 (possible for chord, unlikely for note)
            # We leave them both in the lookAhead buffer, and carry on.
            # harmony is [0..lowestOffset]
            lowestOffset = min(
                firstNoteOffset, firstChordOffset
            )
            self.currRange = HarmonyRange(
                opFrac(0), lowestOffset, None, None,
                self.chordMeasures[self.chordMeasIdx],
                self.melodyMeasures[self.melodyMeasIdx],
                opFrac(
                    opFrac(0)
                    - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                ),
                opFrac(
                    opFrac(0)
                    - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                )
            )
            self.currChordEnd = firstChordQL
            self.currNoteEnd = firstNoteQL
            return self.currRange

        # In middle of score

        # There are 4 possible reasons for prevEnd:
        # 1. It's the end of prevChord
        # 2. It's the end of prevNote
        # 3. It's the start of the next chord
        # 4. It's the start of the next note
        # The reason can be any combination of the above; there can be one reason or
        # up to four reasons.  All those possibilities need to be handled by the code
        # below.
        prevEnd: OffsetQL = self.currRange.endOffset
        if prevEnd >= self.highestTime:
            # we are at the end of the score; stop iterating
            return None

        prevChord: m21.harmony.ChordSymbol | None = self.currRange._chord
        prevChordEnd: OffsetQL = self.currChordEnd

        prevNote: m21.note.GeneralNote | None = self.currRange._melodyNote
        prevNoteEnd: OffsetQL = self.currNoteEnd

        newChordStart: OffsetQL = MAX_OFFSETQL
        if self.lookAheadChord is not None:
            newChordStart = self.lookAheadChord.getOffsetInHierarchy(self.chords)

        newNoteStart: OffsetQL = MAX_OFFSETQL
        if self.lookAheadNote is not None:
            newNoteStart = self.lookAheadNote.getOffsetInHierarchy(self.melody)

        # Don't iterate to the next chord or note unless we need it here.
        newChord: m21.harmony.ChordSymbol | None = None
        newChordEnd: OffsetQL = MAX_OFFSETQL
        newNote: m21.note.GeneralNote | None = None
        newNoteEnd: OffsetQL = MAX_OFFSETQL

        # There are several places below where we check prevEnd >= newNote/ChordStart.
        # That's weird; we should never see a note that should have started already,
        # but we just ran into it.  Well... if there is some weird stuff in the score (see
        # Allan Clarke, Roger Cook and Roger Greenaway - Long Cool Woman (Transcribed).mxl,
        # measure 35, with a <forward> at end of measure that shows up as an extra
        # space (durQL=1/24) that somehow doesn't increase the duration of the measure),
        # then this can happen.  So we handle it by pretending the note/chord starts NOW.
        # The "gap" shows up as a separate note/chord, and will get harmonized.  In this
        # particular score, it's just an invisible rest that propagates to the other parts.

        if newChordStart <= prevEnd:
            newChord = self.getNextChord()
            if newChord is not None:
                newChordEnd = opFrac(
                    newChord.getOffsetInHierarchy(self.chords) + newChord.quarterLength
                )

        if newNoteStart <= prevEnd:
            newNote = self.getNextNote()
            if newNote is not None:
                newNoteEnd = opFrac(
                    newNote.getOffsetInHierarchy(self.melody) + newNote.quarterLength
                )

        if prevEnd >= prevChordEnd:
            if prevEnd == prevNoteEnd:
                # Simplest case: both prevChord and prevNote ended here. There may
                # or may not be newChord and/or newNote.
                # harmony is [prevEnd..lowestNewEnd] with newChord? and newNote?
                lowestNewEnd: OffsetQL = min(newChordEnd, newNoteEnd)
                self.currRange = HarmonyRange(
                    prevEnd, lowestNewEnd, newChord, newNote,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        prevEnd
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        prevEnd
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = newChordEnd
                self.currNoteEnd = newNoteEnd
                return self.currRange

            if prevEnd == newNoteStart:
                # prevChord ended here, prevNote did not, but a new note starts here
                # (that means there was no prevNote at all). A new chord may also start.
                # We need to start newChord? and newNote.
                # Note that newChordEnd is MAX_OFFSETQL if there is no newChord, so
                # this simple code works fine.
                # if prevNote is not None, there are overlapping notes here.
                assert prevNote is None and newNote is not None
                lowestOffset = min(newChordEnd, newNoteEnd)
                self.currRange = HarmonyRange(
                    prevEnd, lowestOffset, newChord, newNote,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        prevEnd
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        prevEnd
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = newChordEnd
                self.currNoteEnd = newNoteEnd
                return self.currRange

            # prevChord ended here, prevNote did not, and a new note does not start.
            # A new chord may or may not start. So this is a chord change in the
            # middle of a melody note (or in the middle of no note at all).
            # harmony is [prevEnd..lowestNewEnd] with newChord? and prevNote?
            lowestNewEnd = min(newChordEnd, prevNoteEnd)
            self.currRange = HarmonyRange(
                prevEnd, lowestNewEnd, newChord, prevNote,
                self.chordMeasures[self.chordMeasIdx],
                self.melodyMeasures[self.melodyMeasIdx],
                opFrac(
                    prevEnd
                    - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                ),
                opFrac(
                    prevEnd
                    - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                )
            )
            self.currChordEnd = newChordEnd
            self.currNoteEnd = prevNoteEnd
            return self.currRange

        # prevEnd != prevChordEnd
        if prevEnd == prevNoteEnd:
            if prevEnd >= newChordStart:
                # prevNote ended here, prevChord did not, but a new chord starts here
                # (that means there was no prevChord at all).  A new note may also start.
                # We need to start a new note (if there is one), and the next chord.
                # harmony is [prevEnd..lowestOffset] with newChord and newNote?
                lowestOffset = min(newChordEnd, newNoteEnd)
                self.currRange = HarmonyRange(
                    prevEnd, lowestOffset, newChord, newNote,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        prevEnd
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        prevEnd
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = newChordEnd
                self.currNoteEnd = newNoteEnd
                return self.currRange

            # prevNote ended here, prevChord did not, and a new chord does not start.
            # So this is a note change in the middle of a chord (or in the middle of
            # no chord at all).
            # harmony is [prevEnd..lowestNewEnd] with prevChord? and newNote?
            assert prevNote is not None
            lowestNewEnd = min(prevChordEnd, newNoteEnd)
            self.currRange = HarmonyRange(
                prevEnd, lowestNewEnd, prevChord, newNote,
                self.chordMeasures[self.chordMeasIdx],
                self.melodyMeasures[self.melodyMeasIdx],
                opFrac(
                    prevEnd
                    - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                ),
                opFrac(
                    prevEnd
                    - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                )
            )
            self.currChordEnd = prevChordEnd
            self.currNoteEnd = newNoteEnd
            return self.currRange

        # prevEnd != prevChordEnd and prevEnd != prevNoteEnd
        if prevEnd >= newChordStart:
            if prevEnd >= newNoteStart:
                # prev chord did not end, nor did prev note. But there is a new note and chord.
                # There must have been no note or chord, and now there is both.
                # harmony is [prevEnd..lowestNewEnd] with new chord and new note
                assert prevNote is None and prevChord is None
                lowestNewEnd = min(newChordEnd, newNoteEnd)
                self.currRange = HarmonyRange(
                    prevEnd, lowestNewEnd, newChord, newNote,
                    self.chordMeasures[self.chordMeasIdx],
                    self.melodyMeasures[self.melodyMeasIdx],
                    opFrac(
                        prevEnd
                        - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                    ),
                    opFrac(
                        prevEnd
                        - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                    )
                )
                self.currChordEnd = newChordEnd
                self.currNoteEnd = newNoteEnd
                return self.currRange

            # prev chord did not end, nor did prev note.  But there is a new chord (and no
            # new note).  This is a new chord in the middle of a note (and prevChord is None).
            # harmony is [prevEnd..lowestNewEnd] with newChord and prevNote (which may be None).
            assert prevChord is None
            lowestNewEnd = min(newChordEnd, prevNoteEnd)
            self.currRange = HarmonyRange(
                prevEnd, lowestNewEnd, newChord, prevNote,
                self.chordMeasures[self.chordMeasIdx],
                self.melodyMeasures[self.melodyMeasIdx],
                opFrac(
                    prevEnd
                    - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                ),
                opFrac(
                    prevEnd
                    - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                )
            )
            self.currChordEnd = newChordEnd
            self.currNoteEnd = prevNoteEnd
            return self.currRange

        # prevEnd != prevChordEnd, prevEnd != prevNoteEnd,
        # prevEnd != newChordStart
        if prevEnd >= newNoteStart:
            # prev chord did not end, nor did prev note.  But there is a new note (and no
            # new chord).  This is a new note in the middle of a chord (and prevNote is None).
            # harmony is [prevEnd..newNoteEnd] with prevChord (which may be None) and newNote.
            assert prevNote is None
            self.currRange = HarmonyRange(
                prevEnd, newNoteEnd, prevChord, newNote,
                self.chordMeasures[self.chordMeasIdx],
                self.melodyMeasures[self.melodyMeasIdx],
                opFrac(
                    prevEnd
                    - self.chords.elementOffset(self.chordMeasures[self.chordMeasIdx])
                ),
                opFrac(
                    prevEnd
                    - self.melody.elementOffset(self.melodyMeasures[self.melodyMeasIdx])
                )
            )
            self.currChordEnd = prevChordEnd
            self.currNoteEnd = newNoteEnd
            return self.currRange

        raise MusicEngineException(
            'HarmonyIterator saw a case it could not handle (should not happen)'
        )


class MusicEngine:
    @staticmethod
    def toMusicXML(score: m21.stream.Score) -> str:
        output: str | bytes = m21.converter.toData(score, fmt='musicxml', makeNotation=False)
        if t.TYPE_CHECKING:
            assert isinstance(output, str)
        return output

    @staticmethod
    def toHumdrum(score: m21.stream.Score) -> str:
        output: str | bytes = m21.converter.toData(score, fmt='humdrum', makeNotation=False)
        if t.TYPE_CHECKING:
            assert isinstance(output, str)
        return output

    @staticmethod
    def toMei(score: m21.stream.Score) -> str:
        output: str | bytes = m21.converter.toData(score, fmt='mei', makeNotation=False)
        if t.TYPE_CHECKING:
            assert isinstance(output, str)
        return output

    @staticmethod
    def toMusic21Score(fileData: str | bytes, fileName: str) -> m21.stream.Score:
        fmt: str = m21.common.findFormatFile(fileName)
        print(f'toMusicScore(fileName={fileName}): fmt={fmt}')
        if isinstance(fileData, bytes):
            if fileData[:4] == b'PK\x03\x04':
                # it's a zip file (probably .mxl file), extract the contents
                print('It\'s a zip file')
                with zipfile.ZipFile(BytesIO(fileData), 'r') as f:
                    newData: str | bytes = MusicEngine._extractContents(f, fmt)
                    if not newData:
                        # will turn into abort(422, 'Unprocessable music score')
                        raise MusicEngineException
                    fileData = newData
                pass
            else:
                # Some parsers do this for you, but some do not.
                # TODO: Here and in converter21's importers,
                # TODO: support utf-16 as well.
                try:
                    print('decoding utf-8')
                    fileData = fileData.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        print('utf-8 failed; decoding latin-1')
                        if t.TYPE_CHECKING:
                            assert isinstance(fileData, bytes)
                        fileData = fileData.decode('latin-1')
                    except Exception:
                        print('couldn\'t decode, trying parse() anyway')
                        pass  # carry on with fileData as it was

        print(f'toMusicScore: parsing: first 300 bytes of score: {fileData[0:300]!r}')
        output = m21.converter.parse(fileData, format=fmt, forceSource=True)
        if t.TYPE_CHECKING:
            assert isinstance(output, m21.stream.Score)
        return output

    @staticmethod
    def copyNote(note: m21.note.Note) -> m21.note.Note:
        output: m21.note.Note = deepcopy(note)

        output.lyrics = []
        output._tie = None
        if output.pitch.accidental is not None:
            output.pitch.accidental.displayStatus = None

        return output

    @staticmethod
    def _extractContents(f: zipfile.ZipFile,
                         dataFormat: str = 'musicxml') -> str | bytes:
        # stolen verbatim from music21.converter (where it is only applied to files,
        # and we only have bytes).
        post: str | bytes = ''
        if dataFormat == 'musicxml':  # try to auto-harvest
            # will return data as a string
            # note that we need to read the META-INF/container.xml file
            # and get the root file full-path
            # a common presentation will be like this:
            # ['musicXML.xml', 'META-INF/', 'META-INF/container.xml']
            for subFp in f.namelist():
                # the name musicXML.xml is often used, or get top level
                # xml file
                if 'META-INF' in subFp:
                    continue
                # include .mxl to be kind to users who zipped up mislabeled files
                if pathlib.Path(subFp).suffix not in ['.musicxml', '.xml', '.mxl']:
                    continue

                post = f.read(subFp)
                if isinstance(post, bytes):
                    foundEncoding = re.match(br"encoding=[\'\"](\S*?)[\'\"]", post[:1000])
                    if foundEncoding:
                        defaultEncoding = foundEncoding.group(1).decode('ascii')
                        print('Found encoding: ', defaultEncoding)
                    else:
                        defaultEncoding = 'UTF-8'
                    try:
                        post = post.decode(encoding=defaultEncoding)
                    except UnicodeDecodeError:  # sometimes windows written...
                        if t.TYPE_CHECKING:
                            assert isinstance(post, bytes)
                        print('trying utf-16-le')
                        post = post.decode(encoding='utf-16-le')
                        post = re.sub(r"encoding=([\'\"]\S*?[\'\"])",
                                      "encoding='UTF-8'", post)

                break

        return post

    _SHARPS_TO_MAJOR_KEYS: dict[int, str] = {
        -7: 'C-',
        -6: 'G-',
        -5: 'D-',
        -4: 'A-',
        -3: 'E-',
        -2: 'B-',
        -1: 'F',
        0: 'C',
        1: 'G',
        2: 'D',
        3: 'A',
        4: 'E',
        5: 'B',
        6: 'F#',
        7: 'C#'
    }

    @staticmethod
    def getBestTranspositionForKeySig(
        keySig: m21.key.KeySignature,
        semitonesUp: int
    ) -> m21.interval.Interval:
        # if semitonesUp is more than an octave, trim it, but remember how many octaves
        # you trimmed.
        # And this is the horrible thing you have to do to get integer
        # truncation toward zero.
        octavesUp: int = -(-semitonesUp // 12) if semitonesUp < 0 else semitonesUp // 12
        semitonesUp = semitonesUp - (octavesUp * 12)

        majorKey: str = MusicEngine._SHARPS_TO_MAJOR_KEYS[keySig.sharps]

        # We need to transpose the key, and pick the right enharmonic
        # key that has <= 7 sharps or flats, or we'll end up in the
        # key of G# major and have 8 sharps (or worse).
        keyPitch: m21.pitch.Pitch = m21.pitch.Pitch(majorKey)
        newKeyPitch: m21.pitch.Pitch
        if semitonesUp == 0:
            newKeyPitch = deepcopy(keyPitch)
        else:
            chromatic = m21.interval.ChromaticInterval(semitonesUp)
            newKeyPitch = chromatic.transposePitch(keyPitch)

        # put octaves on them now, and then check it
        keyPitch.octave = 4
        newKeyPitch.octave = 4
        if (newKeyPitch < keyPitch) != (semitonesUp < 0):
            # We need to adjust newKeyPitch's octave now,
            # so we transpose in the right direction.
            if semitonesUp < 0:
                # we should be transposing down, not up
                newKeyPitch.octave -= 1
            else:
                # we should be transposing up, not down
                newKeyPitch.octave += 1

        newKeyPitch.octave += octavesUp

        if (newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values()
                and newKeyPitch.name != 'C-' and newKeyPitch.name != 'C#'):
            # we prefer 5 flats to 7 sharps, and 5 sharps to 7 flats
            interval = m21.interval.Interval(keyPitch, newKeyPitch)
            return interval

        newKeyPitch.getEnharmonic(inPlace=True)
        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
            interval = m21.interval.Interval(keyPitch, newKeyPitch)
            return interval

        # sometimes getEnharmonic cycles between three pitches, so try for a 3rd time
        newKeyPitch.getEnharmonic(inPlace=True)
        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
            interval = m21.interval.Interval(keyPitch, newKeyPitch)
            return interval

        raise MusicEngineException(
            'Unexpected failure to find a reasonable key to transpose into'
        )

    @staticmethod
    def getBestTranspositionsForScore(
        score: m21.stream.Score,
        semitonesUp: int
    ) -> list[tuple[OffsetQL, m21.key.KeySignature, m21.interval.Interval]]:
        # returns a sorted (by offset) list of (offset, keysig, interval) tuples
        keySigs: list[m21.key.KeySignature] = list(
            score.recurse()
            .getElementsByClass(m21.key.KeySignature)
        )

        keySigAndTransposeIntervalAtOffsetList: list[
            dict[
                OffsetQL,
                tuple[m21.key.KeySignature, m21.interval.Interval]
            ]
        ] = []

        # we try semitonesUp, as well as down an extra half and up an extra half,
        # in case they get us more readable key signatures.
        for semis in (semitonesUp, semitonesUp - 1, semitonesUp + 1):
            keySigAndTransposeIntervalAtOffset: dict[
                OffsetQL,
                tuple[m21.key.KeySignature, m21.interval.Interval]
            ] = {}
            for keySig in keySigs:
                offsetInScore: OffsetQL = keySig.getOffsetInHierarchy(score)
                if offsetInScore not in keySigAndTransposeIntervalAtOffset:
                    interval: m21.interval.Interval = MusicEngine.getBestTranspositionForKeySig(
                        keySig, semis
                    )
                    keySigAndTransposeIntervalAtOffset[offsetInScore] = keySig, interval

            if opFrac(0) not in keySigAndTransposeIntervalAtOffset:
                startKey: m21.key.KeySignature = m21.key.KeySignature(0)
                interval = MusicEngine.getBestTranspositionForKeySig(startKey, semis)
                keySigAndTransposeIntervalAtOffset[opFrac(0)] = startKey, interval

            keySigAndTransposeIntervalAtOffsetList.append(keySigAndTransposeIntervalAtOffset)

        # Figure out which of the three transpositions is best (lowest total number of
        # sharps/flats the in resulting keysigs)
        lowestAccidCount: int = MAX_INT
        bestIdx: int = -1
        for i, keySigAndTransposeIntervalAtOffset in enumerate(
                keySigAndTransposeIntervalAtOffsetList):
            accidCount: int = 0
            for _offset, (keySig, interval) in keySigAndTransposeIntervalAtOffset.items():
                newKeySig: m21.key.KeySignature = keySig.transpose(interval, inPlace=False)
                accidCount += abs(newKeySig.sharps)
            if accidCount < lowestAccidCount:
                lowestAccidCount = accidCount
                bestIdx = i

        # turn best keySigAndTransposeIntervalAtOffset into a sorted
        # (by offset) list of [offset, keysig, interval] tuples
        keySigAndTransposeIntervalAtOffset = keySigAndTransposeIntervalAtOffsetList[bestIdx]
        output: list[tuple[OffsetQL, m21.key.KeySignature, m21.interval.Interval]] = []
        for offset, (keySig, interval) in keySigAndTransposeIntervalAtOffset.items():
            output.append((offset, keySig, interval))

        output = sorted(output, key=lambda x: x[0])
        return output

    @staticmethod
    def transposeInPlace(score: m21.stream.Score, semitones: int):
        offsetKeySigIntervalList: list[tuple[
            OffsetQL,
            m21.key.KeySignature,
            m21.interval.Interval
        ]] = MusicEngine.getBestTranspositionsForScore(score, semitones)

        highestScoreOffset: OffsetQL = score.highestTime
        with m21.stream.makeNotation.saveAccidentalDisplayStatus(score):
            for thisIdx, (offsetStart, _keySig, interval) in enumerate(offsetKeySigIntervalList):
                endOffset: OffsetQL = highestScoreOffset
                if thisIdx + 1 < len(offsetKeySigIntervalList):
                    endOffset = offsetKeySigIntervalList[thisIdx + 1][0]

                includeEndBoundary: bool = False
                if endOffset == highestScoreOffset:
                    includeEndBoundary = True

                flatScore: m21.stream.Score = score.flatten()
                partialScore: m21.stream.Stream = (
                    flatScore.recurse().getElementsByOffsetInHierarchy(
                        offsetStart,
                        offsetEnd=endOffset,
                        includeEndBoundary=includeEndBoundary
                    ).stream()
                )

                partialScore.transpose(interval, inPlace=True)

    @staticmethod
    def tryAddingDegree(
        leadPitchName: PitchName,
        origChord: m21.harmony.ChordSymbol,
        degree: int,
        alter: int | None = None
    ) -> m21.harmony.ChordSymbol | None:
        newChord: m21.harmony.ChordSymbol = deepcopy(origChord)
        newChord.addChordStepModification(
            m21.harmony.ChordStepModification('add', degree, alter)
        )
        added: m21.pitch.Pitch | None = newChord.getChordStep(degree)
        if added and PitchName(added.name) == leadPitchName:
            return newChord
        return None

    @staticmethod
    def tryChord(
        leadPitchName: PitchName,
        origChord: m21.harmony.ChordSymbol,
        kind: str,
        rootAlter: int | None = None,
        keepCSMs: bool = False
    ) -> m21.harmony.ChordSymbol | None:
        origRoot: m21.pitch.Pitch = origChord.root()
        newRoot: m21.pitch.Pitch = origRoot.transpose(
            m21.interval.Interval(rootAlter),
            inPlace=False
        )
        newChord: m21.harmony.ChordSymbol = m21.harmony.ChordSymbol(root=newRoot, kind=kind)
        if keepCSMs:
            for csm in origChord.getChordStepModifications():
                try:
                    newChord.addChordStepModification(csm)
                except m21.harmony.ChordStepModificationException:
                    pass  # probably tried to modify a degree that isn't in the new chord

        newPitchNames: list[PitchName] = [PitchName(p.name) for p in newChord.pitches]
        if leadPitchName in newPitchNames:
            if not newChord.chordStepModifications:
                newChord.chordKindStr = m21.harmony.getCurrentAbbreviationFor(kind)
                if newChord.chordKindStr == 'sus':
                    newChord.chordKindStr = 'sus4'
            return newChord
        return None

    CHORD_DEGREE_TO_ROOT_ALTER: dict[str, int] = {
        '1': 0,
        '2': 2,
        '-3': 3,
        '3': 4,
        '4': 5,
        '#4': 6,
        '-5': 6,
        '5': 7,
        '6': 9,
        '--7': 9,
        '-7': 10,
        '7': 11,
        '9': 14
    }

    @staticmethod
    def pitchCanBeDegreeOfChord(
        pitch: PitchName,
        degrees: str | t.Iterable[str],
        chord: m21.harmony.ChordSymbol,
        chordAlter: int = 0
    ) -> bool:
        if isinstance(degrees, str):
            degrees = [degrees]

        pitchPc: int = pitch.pitch.pitchClass
        root: m21.pitch.Pitch = chord.root()
        if chordAlter:
            root = root.transpose(chordAlter, inPlace=False)

        for degree in degrees:
            rootAlter: int = MusicEngine.CHORD_DEGREE_TO_ROOT_ALTER[degree]
            alteredRootPc: int = (root.pitchClass + rootAlter) % 12
            if pitchPc == alteredRootPc:
                return True

        return False

    @staticmethod
    def getNonPillarChordOptions(
        leadPitchName: PitchName,
        origChord: m21.harmony.ChordSymbol
    ) -> list[m21.harmony.ChordSymbol]:
        allOptions: list[m21.harmony.ChordSymbol] = []

        option1: m21.harmony.ChordSymbol | None = None
        option1a: m21.harmony.ChordSymbol | None = None
        option2: m21.harmony.ChordSymbol | None = None
        option3: m21.harmony.ChordSymbol | None = None
        option4: m21.harmony.ChordSymbol | None = None
        option5: m21.harmony.ChordSymbol | None = None
        option6: m21.harmony.ChordSymbol | None = None
        option7: m21.harmony.ChordSymbol | None = None
        option8: m21.harmony.ChordSymbol | None = None

        # 1. Extended chord: if lead is on 6, -7, or 9, just add that to the existing chord
        chordKindSet: bool = False
        # try adding 6th (or 13th)
        if MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '6', origChord):
            if origChord.chordKind == 'major':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'major-sixth')
            elif origChord.chordKind == 'minor':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-sixth')
            elif origChord.chordKind in (
                    'major-seventh', 'dominant-seventh', 'minor-seventh', 'minor-major-seventh',
                    'dominant-ninth', 'minor-ninth', 'minor-major-ninth'):
                option1 = MusicEngine.tryAddingDegree(leadPitchName, origChord, 13)
            elif origChord.chordKind == 'major-ninth':
                option1 = MusicEngine.tryAddingDegree(leadPitchName, origChord, 6)
                if option1 is not None:
                    option1.chordKindStr = 'maj69'
                    chordKindSet = True
            elif origChord.chordKind == 'major-11th':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'major-13th')
            elif origChord.chordKind == 'dominant-11th':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-13th')
            elif origChord.chordKind == 'minor-11th':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-13th')
            elif origChord.chordKind == 'minor-major-11th':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-major-13th')

        if option1 is None and MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '-7', origChord):
            # add dominant 7th
            if origChord.chordKind == 'major':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh')
            elif origChord.chordKind == 'minor':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-seventh')
            elif origChord.chordKind == 'augmented':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'augmented-seventh')
            elif origChord.chordKind == 'diminished':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'half-diminished-seventh')
#             else:
#                 option1 = MusicEngine.tryAddingDegree(leadPitchName, origChord, 7, -1)

        if option1 is None and MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '9', origChord):
            # add major 9th
            if origChord.chordKind == 'major-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'major-ninth')
            elif origChord.chordKind == 'dominant-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-ninth')
            elif origChord.chordKind == 'minor-major-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-major-ninth')
            elif origChord.chordKind == 'minor-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-ninth')
            elif origChord.chordKind == 'augmented-major-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'augmented-major-ninth')
            elif origChord.chordKind == 'augmented-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'augmented-dominant-ninth')
            elif origChord.chordKind == 'half-diminished-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'half-diminished-ninth')
            elif origChord.chordKind == 'diminished-seventh':
                option1 = MusicEngine.tryChord(leadPitchName, origChord, 'diminished-ninth')
            else:
                option1 = MusicEngine.tryAddingDegree(leadPitchName, origChord, 9)

        if option1 is not None:
            if not chordKindSet:
                option1.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option1.findFigure(), removeNoteNames=True
                    )
                )
            allOptions.append(option1)

        chordKindSet = False
        # Greg's new option1a: unsuspend fourth (to major or minor third) if orig is sus4 or 7sus4
        # or suspend fourth if orig has major or minor third, or let the lead take aug4 in a maj6
        # chord (which is actually a half-diminished-seventh a tritone above)
        if MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, ('3', '-3'), origChord):
            if origChord.chordKind == 'suspended-fourth':
                if MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '3', origChord):
                    option1a = MusicEngine.tryChord(
                        leadPitchName, origChord, 'major', keepCSMs=True
                    )
                elif MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '-3', origChord):
                    option1a = MusicEngine.tryChord(
                        leadPitchName, origChord, 'minor', keepCSMs=True
                    )
                if option1a is not None:
                    # e.g. if it was suspended-fourth with add b7, now it is
                    # minor or major with add b7, which can be simplified to
                    # 'minor-seventh' or 'dominant-seventh'
                    M21Utilities.simplifyChordSymbol(option1a)

            elif origChord.chordKind == 'suspended-fourth-seventh':
                if MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '3', origChord):
                    option1a = MusicEngine.tryChord(
                        leadPitchName, origChord, 'dominant-seventh', keepCSMs=True
                    )
                elif MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '-3', origChord):
                    option1a = MusicEngine.tryChord(
                        leadPitchName, origChord, 'minor-seventh', keepCSMs=True
                    )

        elif MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '4', origChord):
            if origChord.chordKind in ('major', 'minor'):
                option1a = MusicEngine.tryChord(
                    leadPitchName, origChord, 'suspended-fourth', keepCSMs=True
                )
            elif origChord.chordKind in (
                    'dominant-seventh', 'major-seventh', 'minor-major-seventh', 'minor-seventh'):
                option1a = MusicEngine.tryChord(
                    leadPitchName, origChord, 'suspended-fourth', keepCSMs=True
                )
                if option1a is not None:
                    # gotta put the 7th back in
                    if origChord.chordKind in ('dominant-seventh', 'minor-seventh'):
                        option1a.addChordStepModification(
                            m21.harmony.ChordStepModification('add', 7, -1)
                        )
                        if not origChord.chordStepModifications:
                            option1a.chordKindStr = '7sus4'
                            chordKindSet = True

                    else:
                        option1a.addChordStepModification(
                            m21.harmony.ChordStepModification('add', 7)
                        )
                        if not origChord.chordStepModifications:
                            option1a.chordKindStr = 'maj7sus4'
                            chordKindSet = True

        elif MusicEngine.pitchCanBeDegreeOfChord(leadPitchName, '#4', origChord):
            if origChord.chordKind == 'major-sixth':
                # a major sixth with an augmented fourth instead of a fifth is
                # a half-diminished-seventh rooted on that augmented fourth (i.e
                # rooted a tritone up)
                option1a = MusicEngine.tryChord(
                    leadPitchName, origChord, 'half-diminished-seventh', 6
                )

        if option1a is not None:
            if not chordKindSet:
                option1a.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option1a.findFigure(), removeNoteNames=True
                    )
                )
            allOptions.append(option1a)

        chordKindSet = False

        # 2. 7th chord with root 5th above original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '3', '5', '-7'), origChord, 7):
            option2 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh', 7)
            if option2 is not None:
                option2.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option2.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option2)

        # 3. 7th chord with root 5th below original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '3', '5', '-7'), origChord, -7):
            option3 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh', -7)
            if option3 is not None:
                option3.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option3.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option3)

        # 4. 7th chord with root semitone below original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '3', '5', '-7'), origChord, -1):
            option4 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh', -1)
            if option4 is not None:
                option4.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option4.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option4)

        # 5. 7th chord with root semitone above original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '3', '5', '-7'), origChord, 1):
            option5 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh', 1)
            if option5 is not None:
                option5.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option5.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option5)

        # 6. 7th chord with root tritone above/below original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '3', '5', '-7'), origChord, 6):
            option6 = MusicEngine.tryChord(leadPitchName, origChord, 'dominant-seventh', 6)
            if option6 is not None:
                option6.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option6.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option6)

        # 7. dim7th chord on original root
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '-3', '-5', '--7'), origChord):
            option7 = MusicEngine.tryChord(leadPitchName, origChord, 'diminished-seventh')
            if option7 is not None:
                option7.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option7.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option7)

        # 8. minor 7th chord with root 5th above original
        if MusicEngine.pitchCanBeDegreeOfChord(
                leadPitchName, ('1', '-3', '5', '-7'), origChord, 7):
            option8 = MusicEngine.tryChord(leadPitchName, origChord, 'minor-seventh', 7)
            if option8 is not None:
                option8.chordKindStr = (
                    M21Utilities.convertChordSymbolFigureToPrintableText(
                        option8.findFigure(), removeNoteNames=True
                    )
                )
                allOptions.append(option8)

        if not allOptions:
            raise MusicEngineException('no non-pillar chord options found')

        return allOptions

    STEM_DIRECTION: dict[PartName, str] = {
        PartName.Tenor: 'up',
        PartName.Lead: 'down',
        PartName.Bari: 'up',
        PartName.Bass: 'down'
    }

    TIE_PLACEMENT: dict[PartName, str] = {
        PartName.Tenor: 'above',
        PartName.Lead: 'below',
        PartName.Bari: 'above',
        PartName.Bass: 'below'
    }

    @staticmethod
    def fixOverlappingNotesAndGaps(leadsheet: m21.stream.Score):
        for part in leadsheet[m21.stream.Part]:
            measList: list[m21.stream.Measure] = list(part[m21.stream.Measure])
            for i, meas in enumerate(measList):
                # Remove any rest-only secondary voices (not the first voice!)
                voices: list[m21.stream.Voice] = list(meas[m21.stream.Voice])
                if len(voices) > 1:
                    voiceRemoveList: list[m21.stream.Voice] = []
                    for v, voice in enumerate(voices):
                        if v == 0:
                            continue
                        onlyRests: bool = True
                        for gn in voice[m21.note.GeneralNote]:
                            if not isinstance(gn, m21.note.Rest):
                                onlyRests = False
                                break
                        if onlyRests:
                            voiceRemoveList.append(voice)
                    for remV in voiceRemoveList:
                        meas.remove(remV)

                # Remove (or fix) any overlapping rests in each voice (and in the
                # top-level of the measure, too).

                nextMeas: m21.stream.Measure | None = None
                if i < len(measList) - 1:
                    nextMeas = measList[i + 1]

                measureDurQL: OffsetQL
                if nextMeas is not None:
                    measureDurQL = opFrac(
                        nextMeas.getOffsetInHierarchy(leadsheet)
                        - meas.getOffsetInHierarchy(leadsheet)
                    )
                else:
                    # meas is the very last measure
                    measureDurQL = opFrac(
                        leadsheet.highestTime
                        - meas.getOffsetInHierarchy(leadsheet)
                    )

                voices = list(meas[m21.stream.Voice])
                if not voices:
                    # the top-level measure
                    MusicEngine.fixOverlappingNotesAndGapsInVoice(meas, highestTime=measureDurQL)
                else:
                    # or the voices
                    for voice in voices:
                        MusicEngine.fixOverlappingNotesAndGapsInVoice(
                            voice, highestTime=measureDurQL
                        )

                if nextMeas is None:
                    # last measure, if it contains nothing of duration, insert hidden rest(s)
                    # of length meas.barDuration (expected duration due to time signature).
                    if meas.highestTime == opFrac(0):
                        theSpace: m21.note.Rest = m21.note.Rest()
                        theSpace.quarterLength = meas.barDuration.quarterLength
                        sOffset: OffsetQL = opFrac(0)
                        for space in M21Utilities.splitComplexRestDuration(theSpace):
                            space.style.hideObjectOnPrint = True
                            meas.insert(sOffset, space)
                            sOffset = opFrac(sOffset + space.quarterLength)

    @staticmethod
    def fixOverlappingNotesAndGapsInVoice(
        voice: m21.stream.Voice | m21.stream.Measure,
        highestTime: OffsetQL
    ):
        generalNotesButNotChordSymbols: list[m21.note.GeneralNote] = list(
            voice
            .getElementsByClass(m21.note.GeneralNote)
            .getElementsNotOfClass(m21.harmony.ChordSymbol)
        )
        skipNextNInOuterLoop: int = 0
        for i, gn in enumerate(generalNotesButNotChordSymbols):
            if skipNextNInOuterLoop:
                skipNextNInOuterLoop -= 1
                continue

            gnStartOffset: OffsetQL = gn.getOffsetInHierarchy(voice)
            gnEndOffset: OffsetQL = opFrac(gnStartOffset + gn.quarterLength)
            # see if the next note (and if so, the next, and so on) starts
            # in this note's offset range.  Note that we loop one extra time, using the
            # highestTime as the last nextGN
            for j in range(i + 1, len(generalNotesButNotChordSymbols) + 1):
                nextGN: m21.note.GeneralNote | None = None
                nextGNStartOffset: OffsetQL = highestTime
                nextGNEndOffset: OffsetQL = MAX_OFFSETQL
                if j < len(generalNotesButNotChordSymbols):
                    nextGN = generalNotesButNotChordSymbols[j]
                    nextGNStartOffset = nextGN.getOffsetInHierarchy(voice)
                    nextGNEndOffset = opFrac(nextGNStartOffset + nextGN.quarterLength)

                if nextGNStartOffset == gnEndOffset:
                    # gn is all good now
                    break

                if nextGNStartOffset > gnEndOffset:
                    # gn is all good now, but we have a gap to fill with hidden rest(s)
                    gapQL: OffsetQL = opFrac(nextGNStartOffset - gnEndOffset)
                    theSpace: m21.note.Rest = m21.note.Rest()
                    theSpace.quarterLength = gapQL
                    sOffset: OffsetQL = gnEndOffset
                    for space in M21Utilities.splitComplexRestDuration(theSpace):
                        space.style.hideObjectOnPrint = True
                        voice.insert(sOffset, space)
                        sOffset = opFrac(sOffset + space.quarterLength)
                    break

                # nextGN starts before gn ends

                if nextGNEndOffset <= gnEndOffset:
                    # nextGN ends before or at end of gn, we can just remove it
                    # (but only if it is a hidden rest, otherwise bail)
                    if isinstance(nextGN, m21.note.Rest) and nextGN.style.hideObjectOnPrint:
                        voice.remove(nextGN)
                        skipNextNInOuterLoop += 1
                        continue
                    raise MusicEngineException('Unuseable leadsheet: overlapping notes/rests')

                # nextGN ends after end of gn, so we need to trim the overlap
                # off the duration, and re-insert later by the overlap amount
                # (but only if nextGN is a hidden rest, otherwise bail).
                if isinstance(nextGN, m21.note.Rest) and nextGN.style.hideObjectOnPrint:
                    overlap: OffsetQL = opFrac(gnEndOffset - nextGNStartOffset)
                    newDurQL: OffsetQL = opFrac(nextGN.quarterLength - overlap)
                    voice.remove(nextGN)
                    if newDurQL != 0:
                        newOffset: OffsetQL = opFrac(nextGNStartOffset + overlap)
                        nextGN.duration.quarterLength = newDurQL
                        sOffset = newOffset
                        for space in M21Utilities.splitComplexRestDuration(nextGN):
                            space.style.hideObjectOnPrint = True
                            voice.insert(sOffset, space)
                            sOffset = opFrac(sOffset + space.quarterLength)
                    skipNextNInOuterLoop += 1
                    continue

                if nextGN is None:
                    raise MusicEngineException('Unuseable leadsheet: overlapping measures')

                raise MusicEngineException('Unuseable leadsheet: overlapping notes/rests')

    @staticmethod
    def fixChordSymbolsAtEndOfMeasure(chords: m21.stream.Part):
        measList: list[m21.stream.Measure] = list(chords[m21.stream.Measure])
        for i, meas in enumerate(measList):
            measHighestTime: OffsetQL = meas.highestTime

            # Move any ChordSymbol at end of meas to offset 0 in nextMeas
            # (unless there is no nextMeas, or there is already a ChordSymbol
            # there, then just remove it from meas).
            for cs in meas[m21.harmony.ChordSymbol]:
                if cs.getOffsetInHierarchy(meas) == measHighestTime:
                    meas.remove(cs)
                    if i < len(measList) - 1:
                        nextMeas: m21.stream.Measure = measList[i + 1]
                        csList: list[m21.harmony.ChordSymbol] = list(
                            nextMeas
                            .recurse()
                            .getElementsByOffsetInHierarchy(0)
                            .getElementsByClass(m21.harmony.ChordSymbol)
                        )
                        if not csList:
                            nextMeas.insert(0, cs)

    @staticmethod
    def fixupChordSymbolOffsets(melody: m21.stream.Part, chords: m21.stream.Part):
        chordSyms: list[m21.harmony.ChordSymbol] = list(
            chords.recurse().getElementsByClass(m21.harmony.ChordSymbol)
        )

        for i, cs in enumerate(chordSyms):
            fixedOffset: OffsetQL
            csOffset: OffsetQL = cs.getOffsetInHierarchy(chords)
            container: m21.stream.Stream | None = (
                chords.containerInHierarchy(cs, setActiveSite=False)
            )
            if container is None:
                raise MusicEngineException('cs not in chords')

            # where nearby is within four quarter notes either side of csOffset.
            nearbyNoteList: list[m21.note.Note] = list(
                melody.recurse()
                .getElementsByOffsetInHierarchy(
                    opFrac(csOffset - 4.0),
                    opFrac(csOffset + 4.0),
                    mustFinishInSpan=False,
                    mustBeginInSpan=False,
                    includeElementsThatEndAtStart=False)
                .getElementsByClass(m21.note.GeneralNote)
                .getElementsNotOfClass(m21.harmony.ChordSymbol)
            )
            nearestRecentNote: m21.note.GeneralNote | None = None
            nearestNoteOffset: OffsetQL | None = None
            for n in nearbyNoteList:
                nOffset: OffsetQL = n.getOffsetInHierarchy(melody)
                if nOffset > csOffset:
                    continue
                if nearestNoteOffset is None:
                    nearestNoteOffset = nOffset
                    nearestRecentNote = n
                    continue
                if nOffset > nearestNoteOffset:
                    nearestNoteOffset = nOffset
                    nearestRecentNote = n
                    continue

            if nearestRecentNote is None:
                # chord without note; we just have to make sure offset in container is
                # expressible and not complex.  We don't have to worry about tuplets.
                offset: OffsetQL = cs.getOffsetInHierarchy(container)
                # round to the nearest eighth-note offset
                fixedOffset = opFrac(int((offset * 2.) + 0.5) / 2.)
                if fixedOffset == offset:
                    continue

                container.remove(cs)
                cs.quarterLength = 0
                container.insert(fixedOffset, cs)
                continue

            if nearestNoteOffset == csOffset:
                # chord starts at a note start, we're good
                continue

            offset = cs.getOffsetInHierarchy(container)

            # first thing (no need to worry about tuplets), see if the start or end
            # of the nearestRecentNote is very close; if so, just use that.
            if t.TYPE_CHECKING:
                assert nearestNoteOffset is not None
                assert nearestRecentNote is not None
            startDiff: OffsetQL = opFrac(nearestNoteOffset - csOffset)
            noteEndOffset: OffsetQL = opFrac(nearestNoteOffset + nearestRecentNote.quarterLength)
            endDiff: OffsetQL = opFrac(noteEndOffset - csOffset)
            nearestDiff: OffsetQL = startDiff
            if abs(endDiff) < abs(nearestDiff):  # type: ignore
                nearestDiff = endDiff
            if abs(nearestDiff) < 0.125:  # type: ignore
                # less than a 32nd note away
                fixedOffset = opFrac(offset + nearestDiff)
                container.remove(cs)
                cs.quarterLength = 0
                container.insert(fixedOffset, cs)
                continue

            nearestNoteLocalOffset: OffsetQL = opFrac(
                nearestNoteOffset - container.getOffsetInHierarchy(chords)
            )
            if nearestNoteLocalOffset == 0. and offset < 1.0:  # type: ignore
                # Within first note in measure, and less than a quarter note
                # after start of that note?  Go ahead and start the chord at
                # start of that note (see "Could It Be Magic", where the Cmaj7
                # in measures 11, 17, 19, is positioned about 0.4 quarter notes
                # (just less than an eighth note) after the note at start of
                # the measure, but clearly needs to start with that note).
                fixedOffset = 0.
                if fixedOffset == offset:
                    continue

                container.remove(cs)
                cs.quarterLength = 0
                container.insert(fixedOffset, cs)
                continue

            nearestNoteLocalOffsetDur = (
                m21.duration.Duration(quarterLength=nearestNoteLocalOffset)
            )
            if len(nearestNoteLocalOffsetDur.tuplets) == 1:
                # round to the nearest tuplet-y eighth note offset
                tupletMultiplier: OffsetQL = nearestNoteLocalOffsetDur.tuplets[0].tupletMultiplier()
                tupletyEighthNotesPerQuarterNote: OffsetQL = opFrac(2.0 / tupletMultiplier)
                fixedOffset = opFrac(
                    int(
                        (offset * tupletyEighthNotesPerQuarterNote) + 0.5
                    )
                    / tupletyEighthNotesPerQuarterNote
                )

                if fixedOffset == offset:
                    continue

                container.remove(cs)
                cs.quarterLength = 0
                container.insert(fixedOffset, cs)
                continue

            # round to the nearest eighth-note offset
            fixedOffset = opFrac(int((offset * 2.) + 0.5) / 2.)
            if fixedOffset == offset:
                continue

            container.remove(cs)
            cs.quarterLength = 0
            container.insert(fixedOffset, cs)

    @staticmethod
    def realizeChordSymbolDurations(piece: m21.stream.Stream):
        # this is a copy of m21.harmony.realizeChordSymbolDurations, that instead
        # of extending a chordsym duration beyond the end-of-measure, will extend
        # to end of measure, and then insert a copy of the chordsym into the start
        # of the next measure, with the remainder of the duration.
        # This routine also handles simultaneous chords, giving them the same duration
        # (lasting until the next non-simultaneous chord).

        pf = piece.flatten()
        onlyChords = list(pf.getElementsByClass(m21.harmony.ChordSymbol))

        first = True
        lastChords: list[m21.harmony.ChordSymbol] = []

        if len(onlyChords) == 0:
            return piece

        for cs in onlyChords:
            if first:
                first = False
                lastChords = [cs]
                continue

            # last/thisChordMeas might be Voices; if so I hope they are both
            # at offset 0 in their respective Measures.
            lastChordMeas: m21.stream.Stream | None = piece.containerInHierarchy(
                lastChords[-1], setActiveSite=False)
            thisChordMeas: m21.stream.Stream | None = piece.containerInHierarchy(
                cs, setActiveSite=False)

            if t.TYPE_CHECKING:
                assert lastChordMeas is not None
                assert thisChordMeas is not None

            qlDiff = pf.elementOffset(cs) - pf.elementOffset(lastChords[-1])
            if qlDiff == 0.0:
                lastChords.append(cs)
                continue

            if lastChordMeas is thisChordMeas:
                for lc in lastChords:
                    lc.duration.quarterLength = qlDiff
            else:
                # loop over all the measures from lastChordMeas through thisChordMeas,
                # doling out a deepcopy of lastChord of an appropriate duration to each
                # measure.  lastChordMeas gets duration from lastChord offset to end
                # of lastChordMeas, the bulk of the measures get a full measure duration,
                # and thisChordMeas gets whatever is left (which should be the thisChord's
                # offset in thisChordMeas).
                fullMeasuresNow: bool = False
                for meas in piece[m21.stream.Measure]:
                    if meas is lastChordMeas:
                        lastChordOffsetInMeas: OffsetQL = (
                            lastChords[-1].getOffsetInHierarchy(lastChordMeas)
                        )
                        ql: OffsetQL = opFrac(lastChordMeas.quarterLength - lastChordOffsetInMeas)
                        if ql != 0:
                            # no deepcopy or insertion; lastChord is already in place
                            for lc in lastChords:
                                lc.quarterLength = ql
                        else:
                            # lastChord is at the very end of lastChordMeas (and since
                            # we're going to propagate it into the next measure, is
                            # unnecessary here).  Remove it.
                            for lc in lastChords:
                                lastChordMeas.remove(lc, recurse=True)

                        fullMeasuresNow = True
                        continue

                    if meas is thisChordMeas:
                        fullMeasuresNow = False
                        thisChordOffsetInMeas: OffsetQL = cs.getOffsetInHierarchy(thisChordMeas)
                        if thisChordOffsetInMeas > 0:
                            for lc in lastChords:
                                chord = deepcopy(lc)
                                chord.quarterLength = thisChordOffsetInMeas
                                meas.insert(0, chord)
                        # we're done, so break out of measure loop
                        break

                    if fullMeasuresNow:
                        # we only get here for measures between lastChordMeas and thisChordMeas
                        for lc in lastChords:
                            chord = deepcopy(lc)
                            chord.quarterLength = meas.quarterLength
                            meas.insert(0, chord)

            lastChords = [cs]

        # on exit from the loop, all but lastChords has been handled
        thisChordMeas = list(piece[m21.stream.Measure])[-1]
        lastChordMeas = piece.containerInHierarchy(lastChords[-1], setActiveSite=False)
        if t.TYPE_CHECKING:
            assert lastChordMeas is not None

        # loop over all the measures from lastChordMeas through thisChordMeas,
        # doling out a deepcopy of lastChord of an appropriate duration to each
        # measure.  lastChordMeas gets duration from lastChord offset to end
        # of lastChordMeas, the bulk of the measures get a full measure duration,
        # and thisChordMeas gets a full measure's worth of chord.
        fullMeasuresNow = False
        for meas in piece[m21.stream.Measure]:
            if meas is lastChordMeas:
                lastChordOffsetInMeas = (
                    lastChords[-1].getOffsetInHierarchy(lastChordMeas)
                )
                ql = lastChordMeas.quarterLength - lastChordOffsetInMeas
                if ql != 0:
                    # no deepcopy or insertion; lastChord is already in place
                    for lc in lastChords:
                        lc.quarterLength = ql
                else:
                    # lastChord is at the very end of lastChordMeas (and since
                    # we're going to propagate it into the next measure, is
                    # unnecessary here).  Remove it.
                    for lc in lastChords:
                        lastChordMeas.remove(lc, recurse=True)

                fullMeasuresNow = True
                continue

            if meas is thisChordMeas:
                fullMeasuresNow = False
                # Change from loop above: there is no thisChord, so just
                # fill out the entire last measure with lastChord.
                for lc in lastChords:
                    chord = deepcopy(lc)
                    chord.quarterLength = thisChordMeas.quarterLength
                    meas.insert(0, chord)
                # we're done, so break out of measure loop
                break

            if fullMeasuresNow:
                # we only get here for measures between lastChordMeas and thisChordMeas
                for lc in lastChords:
                    chord = deepcopy(lc)
                    chord.quarterLength = meas.quarterLength
                    meas.insert(0, chord)

    @staticmethod
    def getChordSymbolInHarmonyRange(
        chords: m21.stream.Part,
        hr: HarmonyRange,
    ) -> m21.harmony.ChordSymbol | None:
        # if hr._chord is still in the melody (hasn't been replaced by several chords)
        if hr._chord is None or hr._chord.activeSite is not None:
            return hr._chord

        csList: list[m21.harmony.ChordSymbol] = (
            MusicEngine.getChordSymbolsInHarmonyRange(chords, hr)
        )
        if len(csList) > 1:
            raise MusicEngineException(
                f'too many chordsyms in HarmonyRange({hr.startOffset}:{hr.endOffset})'
            )

        return csList[0] if csList else None

    @staticmethod
    def getChordSymbolsInHarmonyRange(
        chords: m21.stream.Part,
        hr: HarmonyRange,
    ) -> list[m21.harmony.ChordSymbol]:
        includeEndBoundary: bool = False
        includeElementsThatEndAtStart: bool = False
        if hr.startOffset == hr.endOffset:
            # we normally don't include end boundary, but if the start is the end,
            # we need to include that timestamp.
            includeEndBoundary = True
            includeElementsThatEndAtStart = True

        csList: list[m21.harmony.ChordSymbol] = list(
            hr.chordMeas.recurse()
            .getElementsByOffsetInHierarchy(
                hr.startOffsetInChordMeas,
                hr.endOffsetInChordMeas,
                includeEndBoundary=includeEndBoundary,
                mustFinishInSpan=False,
                mustBeginInSpan=False,
                includeElementsThatEndAtStart=includeElementsThatEndAtStart)
            .getElementsByClass(m21.harmony.ChordSymbol)
        )

        return csList

    @staticmethod
    def getMelodyNoteInHarmonyRange(
        melody: m21.stream.Part,
        hr: HarmonyRange,
    ) -> m21.note.GeneralNote | None:
        # if hr._melodyNote is still in the melody (hasn't been replaced by several notes)
        # Currently, this is always true (or hr._melodyNote is None, which is just as good).
        if hr._melodyNote is None or hr._melodyNote.activeSite is not None:
            return hr._melodyNote

        includeEndBoundary: bool = False
        includeElementsThatEndAtStart: bool = False
        if hr.startOffset == hr.endOffset:
            # we normally don't include end boundary, but if the start is the end,
            # we need to include that timestamp.
            includeEndBoundary = True
            includeElementsThatEndAtStart = True

        noteList: list[m21.note.Note] = list(
            hr.melodyMeas.recurse()
            .getElementsByOffsetInHierarchy(
                hr.startOffsetInMelodyMeas,
                hr.endOffsetInMelodyMeas,
                includeEndBoundary=includeEndBoundary,
                mustFinishInSpan=False,
                mustBeginInSpan=False,
                includeElementsThatEndAtStart=includeElementsThatEndAtStart)
            .getElementsByClass(m21.note.GeneralNote)
            .getElementsNotOfClass(m21.harmony.ChordSymbol)
        )
        if len(noteList) > 1:
            raise MusicEngineException(
                f'too many notes in HarmonyRange({hr.startOffset}:{hr.endOffset})'
            )

        return noteList[0] if noteList else None

    @staticmethod
    def pickBestChordSymbol(
        chordSyms: list[m21.harmony.ChordSymbol],
        melodyNote: m21.note.GeneralNote | None
    ) -> m21.harmony.ChordSymbol:
        def pitchClassCardinality(cs: m21.harmony.ChordSymbol) -> int:
            return cs.pitchClassCardinality

        sortedChords: list[m21.harmony.ChordSymbol] = (
            sorted(chordSyms, key=pitchClassCardinality, reverse=True)
        )

        matchingChords: list[m21.harmony.ChordSymbol] = []
        if not isinstance(melodyNote, m21.note.Note):
            # all the chords match
            # print(f'melodyPitchName: None (probably a Rest)')
            matchingChords = copy(sortedChords)
        else:
            # find the chords that contain the melodyNote's pitch (ignoring octave)
            melodyPitchName: PitchName = PitchName(melodyNote.pitch.name)
            # print(f'melodyPitchName: {melodyPitchName}')
            for cs in sortedChords:
                chordPitchNames = (
                    MusicEngine.getChordVocalParts(
                        Chord(cs),
                        melodyPitchName
                    ).values()
                )
                if melodyPitchName in chordPitchNames:
                    matchingChords.append(cs)

        # print(f'sortedChords: {sortedChords}')
        # print(f'matchingChords: {matchingChords}')

        if matchingChords:
            # print('found a match')
            for cs in matchingChords:
                # see if there is a barbershop 7th
                if MusicEngine.isBarbershopSeventh(cs):
                    # print(f'returning dom7 {cs}')
                    return cs
            for cs in matchingChords:
                # see if there is some other 7th
                if cs.isSeventh():
                    # print(f'returning other 7th {cs}')
                    return cs

            # default to chord with most notes (that also has melody note in it)
            # print(f'returning first chord {matchingChords[0]}')
            return matchingChords[0]

        # print('found NO match')
        # none of them have the melody note
        for cs in sortedChords:
            # see if there is a barbershop 7th
            if MusicEngine.isBarbershopSeventh(cs):
                # print(f'returning dom7 {cs}')
                return cs
        for cs in sortedChords:
            # see if there is some other 7th
            if cs.isSeventh():
                # print(f'returning other 7th {cs}')
                return cs

        # default to chord with most notes
        # print(f'returning first chord {sortedChords[0]}')
        return sortedChords[0]

    @staticmethod
    def isBarbershopSeventh(cs: m21.harmony.ChordSymbol) -> bool:
        if cs.chordKind == 'dominant-seventh':
            return True

        # Work backward from the underlying pitches instead (because maybe there is no
        # cs.chordKind, or cs.chordKind is 'pedal' or 'power' or 'major' with other
        # added degrees, or other weirdness).
        try:
            cs1: m21.harmony.ChordSymbol = m21.harmony.chordSymbolFromChord(cs)
            if cs1.chordKind == 'dominant-seventh':
                return True
        except Exception:
            # m21.harmony.chordSymbolFromChord crashes (bug?) on chords with adds/omits/alters
            pass

        return False

    @staticmethod
    def pickBetweenSimultaneousChords(melody: m21.stream.Part, chords: m21.stream.Part):
        chordSyms: list[m21.harmony.ChordSymbol] = list(
            chords.recurse().getElementsByClass(m21.harmony.ChordSymbol)
        )

        skipN: int = 0
        for i, cs in enumerate(chordSyms):
            # skip forward skipN chord symbols, driving skipN to zero
            if skipN:
                skipN -= 1
                continue

            offset: OffsetQL = cs.getOffsetInHierarchy(chords)
            chordSymsAtOffset: list[m21.harmony.ChordSymbol] = []

            chordSymsAtOffset.append(cs)

            for j in range(i + 1, len(chordSyms)):
                csj: m21.harmony.ChordSymbol = chordSyms[j]
                if offset != csj.getOffsetInHierarchy(chords):
                    break
                chordSymsAtOffset.append(csj)
                skipN += 1

            if len(chordSymsAtOffset) <= 1:
                # skip when we don't have multiple simultaneous chords
                continue

            noteList: list[m21.note.Note] = list(
                melody.recurse()
                .getElementsByOffsetInHierarchy(
                    offset,
                    mustFinishInSpan=False,
                    mustBeginInSpan=False,
                    includeElementsThatEndAtStart=False)
                .getElementsByClass(m21.note.GeneralNote)
                .getElementsNotOfClass(m21.harmony.ChordSymbol)
            )
            if len(noteList) > 1:
                raise MusicEngineException('more than one note at chord start')

            melodyNoteAtOffset: m21.note.GeneralNote | None = None
            if noteList:
                melodyNoteAtOffset = noteList[0]

            bestcso: m21.harmony.ChordSymbol = (
                MusicEngine.pickBestChordSymbol(chordSymsAtOffset, melodyNoteAtOffset)
            )

            for cso in chordSymsAtOffset:
                # remove all except bestcs
                if cso is bestcso:
                    continue

                cVoice = chords.containerInHierarchy(cso, setActiveSite=False)
                if cVoice is None:
                    raise MusicEngineException('cso not in chords')

                cVoice.remove(cso)

    @staticmethod
    def addChordOptionsForNonPillarNotes(melody: m21.stream.Part, chords: m21.stream.Part):
        hr: HarmonyRange
        hiter: HarmonyIterator = HarmonyIterator(chords, melody)
        for hr in hiter:
            chordSym: m21.harmony.ChordSymbol | None = (
                MusicEngine.getChordSymbolInHarmonyRange(chords, hr)
            )
            melodyNote: m21.note.GeneralNote | None = (
                MusicEngine.getMelodyNoteInHarmonyRange(melody, hr)
            )
            cVoice: m21.stream.Stream | None = None
            mVoice: m21.stream.Stream | None = None
            mMeas: m21.stream.Stream | None = None

            if not isinstance(melodyNote, m21.note.Note):
                # skipping Rests, Unpitcheds (there will be no Chords, we already rejected
                # those scores).  Also skipping ChordSymbols (since chords and melody
                # might be the exact same part).
                continue

            if chordSym is None or isinstance(chordSym, m21.harmony.NoChord):
                # skip melody notes that have no chordsym at all
                continue

            cVoice = chords.containerInHierarchy(chordSym, setActiveSite=False)
            if cVoice is None:
                raise MusicEngineException('hr.chord not in hr.chords')
            mVoice = melody.containerInHierarchy(melodyNote, setActiveSite=False)
            if mVoice is None:
                raise MusicEngineException('hr.melodyNote not in hr.melody')

            if cVoice.getOffsetInHierarchy(chords) != mVoice.getOffsetInHierarchy(melody):
                raise MusicEngineException('mismatched chords v melody voice offsets')

            if isinstance(mVoice, m21.stream.Measure):
                mMeas = mVoice
            elif isinstance(mVoice, m21.stream.Voice):
                mMeas = melody.containerInHierarchy(mVoice, setActiveSite=False)

            # We need to see if melodyNote is in the chordsym, and if not, come up with
            # alternate chordsym options.

            melodyPitchName: PitchName = PitchName(melodyNote.pitch.name)

            chordPitchNames = (
                MusicEngine.getChordVocalParts(
                    Chord(chordSym),
                    melodyPitchName
                ).values()
            )

            options: list[m21.harmony.ChordSymbol] = []

            # whether or not melodyNote is in the chord, check for syncopated (early) melody
            # note and add the syncopated (early) next chord as an option in that case.
            if (mMeas is not None
                    and melodyNote.quarterLength <= 0.5
                    and melodyNote.tie is not None
                    and melodyNote.tie.type in ('start', 'continue')):
                # melody note is eighth note or smaller, tied to next note
                melodyNoteEndQL: OffsetQL = opFrac(
                    melodyNote.getOffsetInHierarchy(mMeas) + melodyNote.quarterLength
                )
                if melodyNoteEndQL == mMeas.quarterLength:
                    # melody note is last note in measure
                    nextNote: m21.note.GeneralNote | None = hiter.lookAheadNote
                    if isinstance(nextNote, m21.note.Note):
                        nextPitchName: PitchName = PitchName(nextNote.pitch.name)
                        if nextPitchName == melodyPitchName:
                            # melody note is syncopated (early) next note
                            nextChord: m21.harmony.ChordSymbol | None = hiter.lookAheadChord
                            if nextChord is not None:
                                nextChordPitchNames = (
                                    MusicEngine.getChordVocalParts(
                                        Chord(nextChord),
                                        melodyPitchName
                                    ).values()
                                )
                                if melodyPitchName in nextChordPitchNames:
                                    # best option (0) is syncopated (early) next chord
                                    options.insert(0, deepcopy(nextChord))

            if melodyPitchName not in chordPitchNames:
                options.extend(
                    MusicEngine.getNonPillarChordOptions(melodyPitchName, chordSym)
                )

            if options:
                chosenIdx: int = 0
                chosenOption = options[chosenIdx]
                # We need a place to store options for a subrange of the melodyNote
                # melodyNote.shopit_options_dict[] = options  # type: ignore
                # melodyNote.shopit_current_option_index = chosenIdx  # type: ignore

                startOffsetInVoice: OffsetQL = (
                    hr.startOffset - cVoice.getOffsetInHierarchy(chords)
                )

                MusicEngine.replaceChordSymbolPortion(
                    cVoice,
                    chordSym,
                    chosenOption,
                    startOffsetInVoice,
                    opFrac(hr.endOffset - hr.startOffset)
                )

    @staticmethod
    def replaceChordSymbolPortion(
        cStream: m21.stream.Stream,
        origcs: m21.harmony.ChordSymbol,
        newcs: m21.harmony.ChordSymbol,
        newcsOffset: OffsetQL,
        newcsQL: OffsetQL
    ):
        origcsOffset: OffsetQL = origcs.getOffsetInHierarchy(cStream)
        firstOrigcsQL: OffsetQL = opFrac(newcsOffset - origcsOffset)
        secondOrigcsQL: OffsetQL = opFrac(origcs.quarterLength - (firstOrigcsQL + newcsQL))
        secondOrigcsOffset: OffsetQL = opFrac(newcsOffset + newcsQL)

        # first, trim the first bit of origCS
        if firstOrigcsQL > 0:
            # we need to leave a bit of the original cs in place, with ql trimmed
            # (don't modify in place, though; deepcopy and replace)
            firstOrigcs: m21.harmony.ChordSymbol = deepcopy(origcs)
            firstOrigcs.quarterLength = firstOrigcsQL
            cStream.replace(origcs, firstOrigcs)
        else:
            # the portion of origcs before newcs is _gone_
            cStream.remove(origcs)

        # next, insert newcs
        newcs.quarterLength = newcsQL
        cStream.insert(newcsOffset, newcs)

        # finally, insert the second portion of origcs (deepcopied)
        if secondOrigcsQL > 0:
            secondOrigcs: m21.harmony.ChordSymbol = deepcopy(origcs)
            secondOrigcs.quarterLength = secondOrigcsQL
            cStream.insert(secondOrigcsOffset, secondOrigcs)

    @staticmethod
    def removeAllBeams(leadSheet: m21.stream.Score):
        # in place
        for nc in leadSheet[m21.note.NotRest]:
            nc.beams = m21.beam.Beams()

    @staticmethod
    def removeAllDirections(leadSheet: m21.stream.Score):
        for d in leadSheet[(
            m21.expressions.TextExpression,
            m21.tempo.TempoIndication,
            m21.dynamics.Dynamic,
            m21.dynamics.DynamicWedge
        )]:
            leadSheet.remove(d, recurse=True)

    @staticmethod
    def makeAccidentals(shoppedVoices: list[FourVoices]):
        currMeasure: FourVoices
        for mIdx, currMeasure in enumerate(shoppedVoices):
            # prevMeasure is the previous measure of all four voices (to look
            # at for voice-leading decisions).
            prevMeasure: FourVoices | None = None
            if mIdx > 0:
                prevMeasure = shoppedVoices[mIdx - 1]

            for partName in (PartName.Tenor, PartName.Lead, PartName.Bari, PartName.Bass):
                # set accidental visibility properly
                harmonyVoice: m21.stream.Voice = currMeasure[partName]
                harmonyNotes: list[m21.note.Note] = list(harmonyVoice[m21.note.Note])
                tiedPitchNames: set[str] = set()
                while True:  # fake loop to avoid deep if nesting
                    if prevMeasure is None:
                        break

                    if not harmonyNotes or harmonyNotes[0].tie is None:
                        break

                    prevHarmonyNotes: list[m21.note.GeneralNote] = list(
                        prevMeasure[partName][m21.note.GeneralNote]
                    )
                    if not prevHarmonyNotes:
                        break
                    if not isinstance(prevHarmonyNotes[-1], m21.note.Note):
                        # can't be tied with Note (well, it could be a Chord, but we know not)
                        break
                    if prevHarmonyNotes[-1].tie is None:
                        break

                    prevNameWithOctave = prevHarmonyNotes[-1].pitch.nameWithOctave
                    if prevNameWithOctave != harmonyNotes[0].pitch.nameWithOctave:
                        break
                    # Last pitch (in partName) in previous measure is tied with first pitch
                    # (in partName) in this measure, which will make any accidental on the
                    # first pitch hidden (tell makeAccidentals, so it will know to do that).
                    tiedPitchNames.add(prevNameWithOctave)
                    break

                harmonyVoice.makeAccidentals(
                    useKeySignature=True,
                    searchKeySignatureByContext=True,  # current keysig might not be in this voice
                    cautionaryPitchClass=True,   # don't hide accidental for different octave
                    overrideStatus=True,         # because we may have left displayStatus set wrong
                    tiePitchSet=tiedPitchNames,  # tied across barline needs no repeated accidental
                    inPlace=True
                )

    @staticmethod
    def fixupTies(shoppedVoices: list[FourVoices]):
        # turn list of FourVoices into four really long lists of notes
        tenorNoteList: list[m21.note.GeneralNote] = []
        leadNoteList: list[m21.note.GeneralNote] = []
        bariNoteList: list[m21.note.GeneralNote] = []
        bassNoteList: list[m21.note.GeneralNote] = []

        for fourVoices in shoppedVoices:
            tenorNoteList.extend(list(fourVoices.tenor[m21.note.GeneralNote]))
            leadNoteList.extend(list(fourVoices.lead[m21.note.GeneralNote]))
            bariNoteList.extend(list(fourVoices.bari[m21.note.GeneralNote]))
            bassNoteList.extend(list(fourVoices.bass[m21.note.GeneralNote]))

        for i, leadNote in enumerate(leadNoteList):
            if leadNote.tie is not None:
                # while we're here, make the lead ties have the correct placement
                leadNote.tie.placement = MusicEngine.TIE_PLACEMENT[PartName.Lead]

            if i == len(leadNoteList) - 1:
                # no next note, we're done with all the lead notes
                break

            nextLeadNote: m21.note.GeneralNote = leadNoteList[i + 1]
            if nextLeadNote.tie is not None:
                nextLeadNote.tie.placement = MusicEngine.TIE_PLACEMENT[PartName.Lead]

            if not isinstance(leadNote, m21.note.Note):
                continue
            if not isinstance(nextLeadNote, m21.note.Note):
                continue
            if leadNote.tie is None:
                continue
            if leadNote.tie.type not in ('start', 'continue'):
                continue
            if leadNote.pitch.ps != nextLeadNote.pitch.ps:
                # we check this because ties are often confused, and we want to make sure
                continue

            # We have a tie between these two notes!
            # If any of the harmony parts have two notes at these same
            # two offsets, we should replicate this tie there (with
            # appropriate placement)
            for partName in (PartName.Tenor, PartName.Bari, PartName.Bass):
                if partName == PartName.Tenor:
                    harmNote = tenorNoteList[i]
                    nextHarmNote = tenorNoteList[i + 1]  # safe (see check above)
                elif partName == PartName.Bari:
                    harmNote = bariNoteList[i]
                    nextHarmNote = bariNoteList[i + 1]
                elif partName == PartName.Bass:
                    harmNote = bassNoteList[i]
                    nextHarmNote = bassNoteList[i + 1]

                if not isinstance(harmNote, m21.note.Note):
                    continue
                if not isinstance(nextHarmNote, m21.note.Note):
                    continue
                if harmNote.offset != leadNote.offset:
                    continue
                if nextHarmNote.offset != nextLeadNote.offset:
                    continue
                if harmNote.pitch.ps != nextHarmNote.pitch.ps:
                    continue

                harmNote.tie = deepcopy(leadNote.tie)
                harmNote.tie.placement = MusicEngine.TIE_PLACEMENT[partName]
                if nextLeadNote.tie is None:
                    # None implies a stop to the current tie
                    continue
                nextHarmNote.tie = deepcopy(nextLeadNote.tie)
                nextHarmNote.tie.placement = MusicEngine.TIE_PLACEMENT[partName]

    @staticmethod
    def splitComplexNotesAndRests(shopped: m21.stream.Score):
        for p in shopped.parts:
            for m in p[m21.stream.Measure]:
                for v in m.voices:
                    for el in v:
                        if not isinstance(el, (m21.note.Note, m21.note.Rest)):
                            continue
                        if el.duration.isGrace:
                            continue
                        if len(el.duration.components) == 1:
                            continue
                        splits: m21.base._SplitTuple = el.splitAtDurations()
                        if len(splits) <= 1:
                            continue
                        currOffset: OffsetQL = el.getOffsetInHierarchy(v)
                        v.remove(el)
                        for split in splits:
                            v.insert(currOffset, split)
                            currOffset = opFrac(currOffset + split.quarterLength)

    @staticmethod
    def shopPillarMelodyNotesFromLeadSheet(
        inLeadSheet: m21.stream.Score,
        arrType: ArrangementType
    ) -> m21.stream.Score:
        # Never in place, always creates a new score from scratch
        # raises MusicEngineException if it can't do the job.

        # First, make a deepcopy of the inLeadSheet, so we can modify it at will.
        # For example, we might need to transpose it to a different key, or generate
        # ChordSymbols from the piano accompaniment.
        leadSheet: m21.stream.Score = deepcopy(inLeadSheet)

        # Most directions, dynamics, metronome marks, etc, will no longer apply,
        # and some scores have directions with offsets that extend the measure,
        # causing mass confusion about gaps between notes.
        MusicEngine.removeAllDirections(leadSheet)
        # leadSheet.show('musicxml.pdf', makeNotation=False)

        # Before testing if it can be used as a leadsheet, remove any "all rest" voices,
        # and fix up any rests that overlap with other general notes (either remove
        # them, or leave the non-overlapping bit in place).
        # "Bryan Adams - (Everything I Do) I Do It For You.mxl" is an example with
        # overlapping hidden rests in the top-level measure (Sibelius was trying to
        # position a chord symbol in the middle of a note, and should have given the
        # <forward> tag a voice number).
        # Many MusicXML files have rests in secondary voices to do the same thing (and
        # fixOverlappingRests will remove those voices).
        MusicEngine.fixOverlappingNotesAndGaps(leadSheet)

        melody: m21.stream.Part
        chords: m21.stream.Part
        melody, chords = MusicEngine.useAsLeadSheet(leadSheet)

        # move chords at very end of measure to very beginning of next measure
        MusicEngine.fixChordSymbolsAtEndOfMeasure(chords)

        # more fixups to the leadsheet score
        M21Utilities.fixupBadChordKinds(chords, inPlace=True)

        # some chord symbols are positioned at very strange offsets, and we need to
        # round them off to reasonable offsets (we use melody to determine whether
        # there are tuplets in play, etc).
        MusicEngine.fixupChordSymbolOffsets(melody, chords)

        # We call realizeChordSymbolDurations() because otherwise ChordSymbols have
        # duration == 0 or 1, which doesn't help us find the ChordSymbol that has a
        # time range that contains a particular offset.  We have our own copy of this,
        # with an added "bugfix" that splits chordsyms across barlines, so the new
        # chordsym duration doesn't push the barline out.
        MusicEngine.realizeChordSymbolDurations(chords)

        # if there are simultaneous chords (same offset) we should pick one
        # and remove the others.  Pick one that has the melody note in it,
        # if possible.
        # leadSheet.show('musicxml.pdf', makeNotation=False)
        MusicEngine.pickBetweenSimultaneousChords(melody, chords)

        # inLeadSheet.show('musicxml.pdf', makeNotation=False)
        # leadSheet.show('musicxml.pdf', makeNotation=False)

        # testing only
        # notes = list(melody.recurse()
        #     .getElementsByClass(m21.note.GeneralNote)
        #     .getElementsNotOfClass(m21.harmony.ChordSymbol))
        #
        # numNotes: int = len(notes)
        # # don't count grace notes
        # for note in notes:
        #     if note.duration.isGrace:
        #         numNotes -= 1
        #
        # numChords: int = len(chords[m21.harmony.ChordSymbol])
        # numHRs: int = 0
        # for _hr in HarmonyIterator(chords, melody):
        #     numHRs += 1
        # if numHRs < numNotes or numHRs > numNotes + numChords:
        #     raise MusicEngineException(f'numNotes={numNotes}, numHRs={numHRs}')

        # Any time the melody note is not in the chord, find some options for
        # better chords, insert one (adjusting other chords' durations as
        # necessary), and note the others somehow, so the user can choose.
        # leadSheet.show('musicxml.pdf', makeNotation=False)
        MusicEngine.addChordOptionsForNonPillarNotes(melody, chords)
        # leadSheet.show('musicxml.pdf', makeNotation=False)

        # remove all beams (because the beams get bogus in the partially filled-in
        # harmony parts, causing occasional export crashes).  We will call
        # m21.stream.makeBeams when necessary, to make valid beams again.
        MusicEngine.removeAllBeams(leadSheet)

        # Now pick a key that will work for lead voice range, and transpose.
        melodyInfo: VocalRangeInfo = VocalRangeInfo(melody)
        semitones: int = (
            melodyInfo.getTranspositionSemitones(arrType)
        )

        # if the melodyInfo (after transposition) will still go out of range,
        # it will at least be centered around the lead range (it will go out
        # a similar amount above and below).  Figure out how much above and below
        # it will go, and increase the other part ranges similarly.  Hey, you
        # picked a rangy song, so your whole quartet needs to be as rangy as
        # your lead (otherwise the bass will end up above the lead, and the
        # tenor below the lead).
        # Note: this assumes you will have transposed the melody by
        # melodyInfo.getTranspositionSemitones(arrType). i.e. it will
        # return a lead range that is transposed from the melody range,
        # along with the other similarly transposed part ranges.
        adjustedPartRanges: dict[ArrangementType, dict[PartName, VocalRange]] = (
            melodyInfo.getAdjustedPartRanges()
        )
        partRanges: dict[PartName, VocalRange] = adjustedPartRanges[arrType]

        # Transpose the whole leadSheet score by that number of semitones
        # (in place). Note that if we're doing a LowerVoices arrangement
        # this will probably make the leadSheet unreadable if you were to
        # print it out, since the upper staff will be an octave lower than
        # is appropriate for treble clef.  If you wanted to print it out,
        # you could switch the clef to Treble8vbClef first, to make it
        # look right.  But the arrangement code doesn't care: it will
        # put the right clefs in the output, and it knows  exactly what
        # octave the melody notes are in (without caring about the clef).
        MusicEngine.transposeInPlace(leadSheet, semitones)
        allKeySigs: list[m21.key.KeySignature] = list(
            leadSheet.recurse()
            .getElementsByClass(m21.key.KeySignature)
        )
        for ks in allKeySigs:
            if abs(ks.sharps) > 6:
                raise MusicEngineException(f'bad transposition to key with sharps={ks.sharps}')

        # First we process the melody into the lead part (creating the entire output
        # score structures as we go, including parts, and measures; inserting
        # any clefs/keysigs/timesigs in all the appropriate measures; and inserting
        # the chord symbols into the measures in the top staff).
        #
        # This creates two data structures:
        # 1. shopped: m21.stream.Score/Parts/Measures
        # 2. shoppedVoices: list[list[Voice]]
        #       outer list is one element per two-Measure grand staff
        #       inner list is the four Voices in that two-Measure grand staff
        # We harmonize the shoppedVoices list, and then insert all those Voices
        # into the appropriate Measures in the Score.


        shopped: m21.stream.Score = m21.stream.Score()
        shoppedVoices: list[FourVoices]  # some operations are easier on list of FourVoices
        shopped, shoppedVoices = MusicEngine.processPillarChordsLead(
            arrType,
            partRanges,
            melody,
            chords,
            leadSheet.metadata
        )

        # Then we will harmonize the three harmony parts, one at a time all the way
        # through, harmonizing only the melody notes that are in the specified chord
        # (the melody pillar notes), potentially tweaking other already harmonized
        # parts as we go, to get better voice leading.
        for partName in (PartName.Bass, PartName.Tenor, PartName.Bari):
            MusicEngine.processPillarChordsHarmony(partRanges, partName, chords, melody, shopped)

        # We can't do this on the fly in processPillarChordsHarmony, because sometimes
        # parts trade notes, so the accidental might never be computed.  e.g. the bari
        # takes a tenor note (that has correctly computed accidental) and gives the tenor
        # a different note (that hasn't yet had its accidental computed).
        MusicEngine.makeAccidentals(shoppedVoices)

        # Time to remove the placeholder rests we added earlier to all the measures in bbStaff
        # (in MusicEngine.processPillarChordsLead).
        bbStaff: m21.stream.Part
        for staff in shopped[m21.stream.Part]:
            if staff.id == 'Bass/Baritone':
                bbStaff = staff
                break

        for bbMeas in bbStaff[m21.stream.Measure]:
            rests: list[m21.note.Rest] = list(bbMeas[m21.note.Rest])
            for rest in rests:
                if hasattr(rest, 'shopit_isPlaceHolder'):
                    bbMeas.remove(rest)

        # Put regularized beams back in
        for part in shopped.parts:
            m21.stream.makeNotation.makeBeams(part, inPlace=True, setStemDirections=False)

        # fix up complex note and rest durations in shopped score
        MusicEngine.splitComplexNotesAndRests(shopped)

        # If there is a tie in the lead voice, and the notes in a harmony part are also
        # the same as each other, put a tie there, too.
        # MusicEngine.fixupTies(shoppedVoices)

        return shopped

    @staticmethod
    def processPillarChordsLead(
        arrType: ArrangementType,
        partRanges: dict[PartName, VocalRange],
        melody: m21.stream.Part,
        chords: m21.stream.Part,
        metadata: m21.metadata.Metadata
    ) -> tuple[m21.stream.Score, list[FourVoices]]:
        # initial empty shoppedVoices and shopped (Score)
        shoppedVoices: list[FourVoices] = []
        shopped: m21.stream.Score = m21.stream.Score()
        shopped.metadata = deepcopy(metadata)
        if shopped.metadata.title:
            if arrType == ArrangementType.UpperVoices:
                shopped.metadata.title += ' (Upper Voices)'
            elif arrType == ArrangementType.LowerVoices:
                shopped.metadata.title += ' (Lower Voices)'
        elif shopped.metadata.movementName:
            if arrType == ArrangementType.UpperVoices:
                shopped.metadata.movementName += ' (Upper Voices)'
            elif arrType == ArrangementType.LowerVoices:
                shopped.metadata.movementName += ' (Lower Voices)'

        # Set up the initial shopped Score with two Parts: Tenor/Lead and Bari/Bass
        tlStaff: m21.stream.Part = m21.stream.Part()
        tlStaff.id = 'Tenor/Lead'
        shopped.insert(0, tlStaff)
        bbStaff: m21.stream.Part = m21.stream.Part()
        # If you change bbStaff.id to something else, search and replace,
        # or it'll break something.
        bbStaff.id = 'Bass/Baritone'
        shopped.insert(0, bbStaff)

        mCurrEnding: m21.spanner.RepeatBracket | None = None
        tlCurrEnding: m21.spanner.RepeatBracket | None = None
        bbCurrEnding: m21.spanner.RepeatBracket | None = None

        for mIdx, (mMeas, cMeas) in enumerate(
            zip(melody[m21.stream.Measure], chords[m21.stream.Measure])
        ):
            # Keep track of stuff we deepcopy into tlMeas/bbMeas (which should
            # then be skipped when populating the four voices.
            measureStuff: list[m21.base.Music21Object] = []

            # create and append the next tlMeas and bbMeas
            # Note that we do not set number to mMeas.measureNumberWithSuffix, since
            # measure number parsing doesn't recreate the suffix correctly all the time.
            # Just set number, and then if necessary, set numberSuffix.
            tlMeas = m21.stream.Measure(number=mMeas.measureNumber)
            if mMeas.numberSuffix:
                tlMeas.numberSuffix = mMeas.numberSuffix
            tlMeas.id = 'Tenor/Lead'  # we look for this later when inserting Voices
            tlStaff.append(tlMeas)
            bbMeas = m21.stream.Measure(number=mMeas.measureNumber)
            if mMeas.numberSuffix:
                bbMeas.numberSuffix = mMeas.numberSuffix
            bbMeas.id = 'Bari/Bass'  # we look for this later when inserting Voices
            bbStaff.append(bbMeas)

            # repeat brackets (need to be duplicated across parts)
            rbList: list[m21.spanner.RepeatBracket] = (
                mMeas.getSpannerSites(m21.spanner.RepeatBracket)
            )
            if rbList:
                if len(rbList) != 1:
                    raise MusicEngineException(
                        f'too many repeat endings in measure {mMeas.measureNumberWithSuffix}'
                    )
                if rbList[0] is mCurrEnding:
                    if t.TYPE_CHECKING:
                        assert tlCurrEnding is not None
                        assert bbCurrEnding is not None
                    tlCurrEnding.addSpannedElements(tlMeas)
                    bbCurrEnding.addSpannedElements(bbMeas)
                else:
                    mCurrEnding = rbList[0]

                    tlCurrEnding = m21.spanner.RepeatBracket(number=mCurrEnding.number)
                    tlCurrEnding.addSpannedElements(tlMeas)
                    tlStaff.append(tlCurrEnding)

                    bbCurrEnding = m21.spanner.RepeatBracket(number=mCurrEnding.number)
                    bbCurrEnding.addSpannedElements(bbMeas)
                    bbStaff.append(bbCurrEnding)
            else:
                mCurrEnding = None
                tlCurrEnding = None
                bbCurrEnding = None

            if mIdx == 0:
                # clef (just put in the clefs we like; hopefully the transposition
                # we did will make those reasonable clefs).  We will ignore all
                # clef changes in the rest of the melody; if there are any, we'll
                # just get lots of leger lines, I guess.  That's the way we like it.
                if arrType == ArrangementType.LowerVoices:
                    tlMeas.insert(0, m21.clef.Treble8vbClef())
                    bbMeas.insert(0, m21.clef.BassClef())
                elif arrType == ArrangementType.UpperVoices:
                    tlMeas.insert(0, m21.clef.TrebleClef())
                    bbMeas.insert(0, m21.clef.Bass8vaClef())

            # left barline
            if mMeas.leftBarline:
                measureStuff.append(mMeas.leftBarline)
                tlMeas.leftBarline = deepcopy(mMeas.leftBarline)
                bbMeas.leftBarline = deepcopy(mMeas.leftBarline)

            # {tl,bb}Meas.insert(0) any keySig/timeSig that are at offset 0
            # in mMeas.recurse(). Just one of each type though.
            sigs: list[m21.key.KeySignature | m21.meter.TimeSignature] = list(
                mMeas.recurse()
                .getElementsByOffsetInHierarchy(0.0)
                .getElementsByClass([m21.key.KeySignature, m21.meter.TimeSignature])
            )

            timeSigFound: bool = False
            keySigFound: bool = False
            for sig in sigs:
                if not timeSigFound:
                    if isinstance(sig, m21.meter.TimeSignature):
                        measureStuff.append(sig)
                        tlMeas.insert(0, deepcopy(sig))
                        bbMeas.insert(0, deepcopy(sig))
                        timeSigFound = True
                if not keySigFound:
                    if isinstance(sig, m21.key.KeySignature):
                        measureStuff.append(sig)
                        tlMeas.insert(0, deepcopy(sig))
                        bbMeas.insert(0, deepcopy(sig))
                        keySigFound = True
                if keySigFound and timeSigFound:
                    break

            # right barline
            if mMeas.rightBarline:
                measureStuff.append(mMeas.rightBarline)
                tlMeas.rightBarline = deepcopy(mMeas.rightBarline)
                bbMeas.rightBarline = deepcopy(mMeas.rightBarline)

            # create two voices in each measure:
            # (tenor/lead in tlMeas, and bari/bass in bbMeas)
            tenor = m21.stream.Voice()
            tenor.id = 'tenor'
            lead = m21.stream.Voice()
            lead.id = 'lead'
            tlMeas.insert(0, tenor)
            tlMeas.insert(0, lead)

            bari = m21.stream.Voice()
            bari.id = 'bari'
            bass = m21.stream.Voice()
            bass.id = 'bass'
            bbMeas.insert(0, bari)
            bbMeas.insert(0, bass)

            # insert them also in the shoppedVoices list as FourVoices
            shoppedVoices.append(FourVoices(tenor=tenor, lead=lead, bari=bari, bass=bass))

            # Walk all the ChordSymbols in cMeas and put them in tlMeas (so
            # they will display above the top staff).
            for cs in cMeas.recurse().getElementsByClass(m21.harmony.ChordSymbol):
                measureStuff.append(cs)
                offset = cs.getOffsetInHierarchy(cMeas)
                tlMeas.insert(offset, deepcopy(cs))

            # Recurse all elements of mMeas, skipping any measureStuff
            # and any clefs and any LayoutBase (we don't care how the
            # leadsheet was laid out) and put them in the lead voice.
            for el in mMeas.recurse():
                if isinstance(el, m21.stream.Stream):
                    # e.g. a voice within the measure
                    continue
                if isinstance(el, (m21.clef.Clef, m21.layout.LayoutBase)):
                    continue
                if el in measureStuff:
                    continue
                offset = el.getOffsetInHierarchy(mMeas)
                if isinstance(el, m21.chord.Chord) and not isinstance(el, m21.harmony.ChordSymbol):
                    # Don't put a chord in the melody; put the top note from the chord instead
                    note = deepcopy(el.notes[-1])
                    note.lyrics = deepcopy(el.lyrics)
                    el = note
                else:
                    el = deepcopy(el)
                if isinstance(el, m21.note.NotRest):
                    el.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Lead]
                lead.insert(offset, el)

            # tlMeas will be of the right duration due to the melody and chords,
            # but bbMeas will not.  It needs to have the right duration before
            # (1) append the right barline and (2) append another measure, or
            # those items will have the wrong offset in the score.  So just
            # put an invisible rest in bbMeas that has the same duration as
            # bbMeas.  Note that if (e.g.) tlMeas is 5 quarter-notes long, you
            # can't just put in one rest, because 5 isn't a valid single note/rest
            # duration.  So we split it into simple duration rests, and insert
            # them all.  We will remove these after shopping the score, so we
            # mark them with a custom attribute (rest.shopit_isPlaceHolder = True).
            placeholderRest: m21.note.Rest = m21.note.Rest()
            placeholderRest.quarterLength = tlMeas.quarterLength
            rOffset: OffsetQL = 0.
            for rest in M21Utilities.splitComplexRestDuration(placeholderRest):
                rest.style.hideObjectOnPrint = True
                rest.shopit_isPlaceHolder = True  # type: ignore
                bbMeas.insert(rOffset, rest)
                rOffset = opFrac(rOffset + rest.quarterLength)

        return shopped, shoppedVoices

    @staticmethod
    def processPillarChordsHarmony(
        partRanges: dict[PartName, VocalRange],
        partName: PartName,
        chords: m21.stream.Part,
        melody: m21.stream.Part,
        shopped: m21.stream.Score
    ):
        tlPart: m21.stream.Part = shopped.parts[0]
        bbPart: m21.stream.Part = shopped.parts[1]
        tlMeasures: list[m21.stream.Measure] = list(tlPart[m21.stream.Measure])
        bbMeasures: list[m21.stream.Measure] = list(bbPart[m21.stream.Measure])

        measIndex: int = 0
        tlMeas: m21.stream.Measure = tlMeasures[measIndex]
        bbMeas: m21.stream.Measure = bbMeasures[measIndex]
        currVoices: FourVoices = FourVoices(
            tenor=tlMeas[m21.stream.Voice][0],
            lead=tlMeas[m21.stream.Voice][1],
            bari=bbMeas[m21.stream.Voice][0],
            bass=bbMeas[m21.stream.Voice][1]
        )
        prevVoices: FourVoices | None = None

        for hr in HarmonyIterator(chords, melody):
            chordSym: m21.harmony.ChordSymbol | None = (
                MusicEngine.getChordSymbolInHarmonyRange(chords, hr)
            )
            melodyNote: m21.note.GeneralNote | None = (
                MusicEngine.getMelodyNoteInHarmonyRange(melody, hr)
            )
            if melodyNote is None:
                raise MusicEngineException('no melodyNote at all (not even a space)')

            cVoice: m21.stream.Stream | None = None
            if chordSym is not None:
                cVoice = (
                    chords.containerInHierarchy(chordSym, setActiveSite=False)
                )
                if cVoice is None:
                    raise MusicEngineException('hr.chord not in hr.chords')

            mVoice: m21.stream.Stream | None = (
                melody.containerInHierarchy(melodyNote, setActiveSite=False)
            )
            if mVoice is None:
                raise MusicEngineException('hr.melodyNote not in hr.melody')

            mContainer: m21.stream.Stream | None = mVoice
            if not isinstance(mContainer, m21.stream.Measure):
                mContainer = melody.containerInHierarchy(mVoice, setActiveSite=False)
            if not isinstance(mContainer, m21.stream.Measure):
                raise MusicEngineException('mVoice not (in) a Measure')
            mMeas: m21.stream.Measure = mContainer

            # update currVoices/prevVoices as appropriate
            if tlMeas.getOffsetInHierarchy(shopped) != mMeas.getOffsetInHierarchy(melody):
                measIndex += 1
                tlMeas = tlMeasures[measIndex]
                bbMeas = bbMeasures[measIndex]
                if tlMeas.getOffsetInHierarchy(shopped) != mMeas.getOffsetInHierarchy(melody):
                    raise MusicEngineException('cannot find next measure to shop')
                prevVoices = currVoices
                currVoices = FourVoices(
                    tenor=tlMeas[m21.stream.Voice][0],
                    lead=tlMeas[m21.stream.Voice][1],
                    bari=bbMeas[m21.stream.Voice][0],
                    bass=bbMeas[m21.stream.Voice][1]
                )

            leadOffsetInScore: OffsetQL = melodyNote.getOffsetInHierarchy(melody)
            leadVoice: m21.stream.Voice = currVoices[PartName.Lead]
            leadOffsetInVoice: OffsetQL = opFrac(
                leadOffsetInScore - leadVoice.getOffsetInHierarchy(shopped)
            )
            harmonyOffsetInVoice: OffsetQL = opFrac(
                hr.startOffset - leadVoice.getOffsetInHierarchy(shopped)
            )
            harmonyQL: OffsetQL = opFrac(hr.endOffset - hr.startOffset)

            elements: list[m21.base.Music21Object] = list(
                leadVoice
                .recurse()
                .getElementsByOffsetInHierarchy(leadOffsetInVoice)
                .getElementsByClass(m21.note.GeneralNote)
                .getElementsNotOfClass(m21.harmony.ChordSymbol)
            )

            # count non-grace notes
            nonGraceCount: int = 0
            nonGraceIndex: int | None = None
            el: m21.base.Music21Object
            for idx, el in enumerate(elements):
                if el.duration.isGrace:
                    continue
                nonGraceIndex = idx
                nonGraceCount += 1

            if nonGraceCount != 1:
                raise MusicEngineException('multiple (or zero) lead notes at offset')

            if t.TYPE_CHECKING:
                assert nonGraceIndex is not None
            el = elements[nonGraceIndex]
            if isinstance(el, m21.chord.Chord):
                raise MusicEngineException(
                    'm21.chord.Chord (not ChordSymbol) found in leadsheet melody'
                )

            if isinstance(el, m21.note.Rest):
                # a rest in the lead is a rest in the harmony part
                # Hide the tenor rest and bari rest (we only want
                # to see one rest in each staff).  Also set all rest
                # positions to center of staff, because we don't want
                # it positioned just for the one voice.
                el.stepShift = 0  # I wish setting to 0 did something...
                rest: m21.note.Rest = deepcopy(el)
                rest.quarterLength = harmonyQL
                if partName in (PartName.Tenor, PartName.Bari):
                    rest.style.hideObjectOnPrint = True
                    rest.stepShift = 0
                currVoices[partName].insert(harmonyOffsetInVoice, rest)
                continue

            if not isinstance(el, m21.note.Note):
                continue

            # it's a non-grace Note
            leadNote: m21.note.Note = el
            if chordSym is None or isinstance(chordSym, m21.harmony.NoChord):
                # Must be a melody pickup before the first chord, or a place
                # in the music where there it is specifically notated that
                # there is no chord at all.
                # Put (visible) rests in the other three parts. Hide Bari
                # (but not Tenor this time) and set rest position on the
                # visible rests.
                noChordRest: m21.note.Rest = m21.note.Rest()
                noChordRest.quarterLength = harmonyQL
                if partName == PartName.Bari:
                    noChordRest.style.hideObjectOnPrint = True
                else:
                    noChordRest.stepShift = 0  # I wish setting to 0 did something...

                currVoices[partName].insert(harmonyOffsetInVoice, noChordRest)
                continue

            leadPitchName: PitchName = PitchName(leadNote.pitch.name)
            chord: Chord = Chord(chordSym)
            chordPitchNames = MusicEngine.getChordVocalParts(
                chord, leadPitchName
            ).values()

            if len(chordPitchNames) < 3:
                # not enough notes to figure out a harmonization
                space: m21.note.Rest = m21.note.Rest()
                space.quarterLength = harmonyQL
                space.style.hideObjectOnPrint = True
                currVoices[partName].insert(harmonyOffsetInVoice, space)
                continue

            if leadPitchName not in chordPitchNames:
                # lead is not on a pillar chord note, fill in bass/tenor/bari with
                # spaces (invisible rests).
                # raise MusicEngineException('lead note not in chord; should never happen')
                space = m21.note.Rest()
                space.quarterLength = harmonyQL
                space.style.hideObjectOnPrint = True
                currVoices[partName].insert(harmonyOffsetInVoice, space)
                continue

            # Lead has a pillar chord note.  Fill in the <partName> note
            # (potentially adjusting other non-lead notes to improve
            # voice leading).
            # Params:
            #   partName: PartName, which part we are harmonizing (might adjust others)
            #   currVoices: FourVoices, where we insert(and adjust) the note(s))
            #   harmonyOffsetInVoice: OffsetQL, offset in currVoices[x] where we are working
            #   harmonyQL: OffsetQL, duration of harmony note needed
            #   thisFourNotes: FourNotes (read-only), for ease of looking up and down
            #   prevFourNotes: FourNotes (read-only), for ease of looking back
            thisFourNotes: FourNotes = (
                MusicEngine.getFourNotesAtOffset(currVoices, harmonyOffsetInVoice)
            )
            prevFourNotes: FourNotes = (
                MusicEngine.getFourNotesBeforeOffset(currVoices, prevVoices, harmonyOffsetInVoice)
            )
            if leadNote is not thisFourNotes[PartName.Lead]:
                raise MusicEngineException('we are confused about the lead note')

            if partName == PartName.Bass:
                MusicEngine.harmonizePillarChordBass(
                    partRanges,
                    currVoices,
                    harmonyOffsetInVoice,
                    harmonyQL,
                    chord,
                    thisFourNotes,
                    prevFourNotes
                )
            elif partName == PartName.Tenor:
                MusicEngine.harmonizePillarChordTenor(
                    partRanges,
                    currVoices,
                    harmonyOffsetInVoice,
                    harmonyQL,
                    chord,
                    thisFourNotes,
                    prevFourNotes
                )
            elif partName == PartName.Bari:
                MusicEngine.harmonizePillarChordBari(
                    partRanges,
                    currVoices,
                    harmonyOffsetInVoice,
                    harmonyQL,
                    chord,
                    thisFourNotes,
                    prevFourNotes
                )
            else:
                raise MusicEngineException(
                    'Should not reach here: partName not in Bass, Tenor, Bari'
                )

    @staticmethod
    def _addBassPitchToVocalParts(
        vocalPartsInOut: dict[int, PitchName],
        chord: Chord,
        leadPitchName: PitchName,
        orderedRolesToReplace: tuple[int, ...]
    ):
        bassPitchName: PitchName | None = chord.preferredBassPitchName
        if not bassPitchName:
            return

        # if bassPitchName is already in the chord (i.e. it's just an inversion, not
        # an extra note), then we set vocalPartsInOut[0] without deleting the pitch from the
        # normal roles (so we will know what role the bass note is trying to play).
        foundIt: bool = False
        for role, pName in vocalPartsInOut.items():
            if bassPitchName == pName:
                vocalPartsInOut[0] = pName
                foundIt = True
                break
        if foundIt:
            return

        if len(vocalPartsInOut) < 4:
            # Qe have room for the extra /bass note! No deletions necessary.
            vocalPartsInOut[0] = bassPitchName
            return

        # We have to choose a non-bass-pitch to delete, to make room for the
        # extra bass pitch. Check in order of orderedRolesToReplace (but don't
        # remove the lead pitch!)
        foundIt = False
        for role in orderedRolesToReplace:
            if role in vocalPartsInOut and vocalPartsInOut[role] != leadPitchName:
                foundIt = True
                del vocalPartsInOut[role]
                vocalPartsInOut[0] = bassPitchName
                break

        if foundIt:
            return

        raise MusicEngineException(
            'error trying to fit /bass into {chord.sym.figure}/{bassPitchName}'
        )

    # Degrees of the chord in our favorite order of unimportance (i.e. remove 5 first;
    # no-one will miss it)
    _DEGREES_TO_REMOVE: tuple[int, ...] = (5, 1, 7, 9, 11, 13, 3, 6, 2, 4)

    @staticmethod
    def getChordVocalParts(
        chord: Chord,
        leadPitchName: PitchName
    ) -> dict[int, PitchName]:
        # This is the place where we decide which of the chord pitches should end
        # up being sung. If the chord is not one we understand, return an empty dict,
        # so that the client will bail on trying to harmonize this chord (for starters,
        # because the lead note is obviously not in it).
        # We return four parts, unless the root is to be doubled, in which case we
        # return three parts.
        output: dict[int, PitchName] = {}  # key: 0 means 'bass should get this one if possible'
        allOfThem: dict[int, PitchName] = (
            MusicEngine.getChordPitchNames(chord)
        )
        roles: tuple[int, ...] = tuple(allOfThem.keys())

        # Catch the weird cases first (we have to pick which note(s) to drop)
        if roles == (1, 3, 5, 7, 9, 11, 13):
            # 13th chord of some sort. For now, just return 7/9/11/13
            # unless the lead is on 1, 3, or 5, in which case return
            # lead/9/11/13 (this is a guess; lead/7/11/13 et al are
            # just as likely correct).
            if leadPitchName == allOfThem[1]:
                output[1] = allOfThem[1]
            elif leadPitchName == allOfThem[3]:
                output[3] = allOfThem[3]
            elif leadPitchName == allOfThem[5]:
                output[5] = allOfThem[5]
            else:
                output[7] = allOfThem[7]

            output[9] = allOfThem[9]
            output[11] = allOfThem[11]
            output[13] = allOfThem[13]
            # if the /bass note is an extra note (not just an inversion), we will drop
            # 11 or 7 (in that order of preference) to make room for it.
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, (11, 7))
            return output

        if roles == (1, 3, 5, 7, 9, 11):
            # 11th chord of some sort.
            # Vol 2 Figure 14.18 likes 5/7/9/11.
            # But if lead is on 1 or 3, we will return lead/7/9/11
            if leadPitchName == allOfThem[1]:
                output[1] = allOfThem[1]
            elif leadPitchName == allOfThem[3]:
                output[3] = allOfThem[3]
            else:
                output[5] = allOfThem[5]

            output[7] = allOfThem[7]
            output[9] = allOfThem[9]
            output[11] = allOfThem[11]

            # If the /bass note is an extra note (not just an inversion), we will drop
            # 5 or 7 (in that order of preference) to make room for it.
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, (5, 7))
            return output

        if roles == (1, 3, 5, 7, 9):
            # 9th chord
            # Vol 2 Figure 14.30 likes 3, 5, 7, 9.
            # But if lead is on 1, we will return 1/5/7/9.
            if leadPitchName == allOfThem[1]:
                output[1] = allOfThem[1]
            else:
                output[3] = allOfThem[3]
            output[5] = allOfThem[5]
            output[7] = allOfThem[7]
            output[9] = allOfThem[9]

            # If the /bass note is an extra note (not just an inversion), we will drop
            # 5 or 3 (in that order of preference) to make room for it.
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, (5, 3))
            return output

        if roles == (1, 3, 5, 6):
            # 6th chord.
            output = copy(allOfThem)

            # If the /bass note is an extra note (not just an inversion), we will drop
            # 5 or 1 (in that order of preference) to make room for it.
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, (5, 1))
            return output

        if roles in (
                (1, 3, 5, 7),
                (1, 4, 5, 7),
                (1, 2, 5, 7)):
            # 7th Chord of some sort.
            output = copy(allOfThem)
            # If the /bass note is an extra note (not just an inversion), we will drop
            # 5 or 1 (in that order of preference) to make room for it.
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, (5, 1))
            return output

        if len(allOfThem) == 3:
            # Triad of some sort (could be sus4, sus2...)
            output = copy(allOfThem)
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, tuple())
            return output

        if len(allOfThem) == 4:
            # triad add something?
            output = copy(allOfThem)
            MusicEngine._addBassPitchToVocalParts(
                output, chord, leadPitchName, MusicEngine._DEGREES_TO_REMOVE
            )
            return output

        if len(allOfThem) >= 5:
            numToRemove: int = len(allOfThem) - 4
            output = copy(allOfThem)
            for deg in MusicEngine._DEGREES_TO_REMOVE:
                if deg in output:
                    if output[deg] == leadPitchName:
                        # leave the lead note in the chord
                        continue
                    numToRemove -= 1
                    del output[deg]
                    if numToRemove == 0:
                        break
            MusicEngine._addBassPitchToVocalParts(
                output, chord, leadPitchName, MusicEngine._DEGREES_TO_REMOVE
            )
            return output

        output = copy(allOfThem)
        # If the /bass note is an extra note (not just an inversion), we will drop
        # a random note (in our favorite order of unimportance) to make room for it.
        MusicEngine._addBassPitchToVocalParts(
            output, chord, leadPitchName, MusicEngine._DEGREES_TO_REMOVE
        )
        return output

    @staticmethod
    def getChordPitchNames(
        chord: Chord
    ) -> dict[int, PitchName]:
        # returns all of 'em, even if there are lots of notes in the chord
        output: dict[int, PitchName] = copy(chord.roleToPitchNames)
        return output

    @staticmethod
    def moveIntoRange(n: m21.note.Note, partRange: VocalRange):
        if n.pitch.octave is None:
            raise MusicEngineException('n.pitch.octave is None')

        if partRange.isInRange(n.pitch):
            return

        if partRange.isTooLow(n.pitch):
            n.pitch.octave += 1
            if partRange.isTooLow(n.pitch):
                n.pitch.octave += 1
                if partRange.isTooLow(n.pitch):
                    raise MusicEngineException('note is WAY too low for part')
            return

        if partRange.isTooHigh(n.pitch):
            n.pitch.octave -= 1
            if partRange.isTooHigh(n.pitch):
                n.pitch.octave -= 1
                if partRange.isTooHigh(n.pitch):
                    raise MusicEngineException('note is WAY too high for part')
            return

        raise MusicEngineException('should not get here (note is both in and out of range')

    @staticmethod
    def harmonizePillarChordBass(
        partRanges: dict[PartName, VocalRange],
        measure: FourVoices,
        offset: OffsetQL,
        durQL: OffsetQL,
        pillarChord: Chord,
        thisFourNotes: FourNotes,
        prevFourNotes: FourNotes
    ):
        # From "Arranging Barbershop: Volume 2" pp11-13
        #
        # Bass: Start the bass on the root or fifth for seventh chords and the root
        #           for triads. Try to follow the general shape of the melody so that
        #           bass notes are higher when the melody is higher and lower when the
        #           melody is lower.  This will help the voicings to not become too
        #           spread.  Consider bass voice leading when the harmony is a seventh
        #           chord and the melody is not on the root or fifth.

        # Note that we harmonize the entire Bass part before starting on any other harmony part.
        # This means we do it by only looking at the chords and the Lead part, and we can't
        # adjust any other part.

        if isinstance(pillarChord.sym, m21.harmony.NoChord):
            raise MusicEngineException('harmonizePillarChordBass: NoChord is not a pillar chord')

        partRange: VocalRange = partRanges[PartName.Bass]

        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note | None = None

        leadPitchName: PitchName = PitchName(lead.pitch.name)

        # chordRole (key) is int, where 1 means root of the chord, 3 means third of the chord, etc
        chPitch: dict[int, PitchName] = MusicEngine.getChordVocalParts(
            pillarChord,
            leadPitchName
        )

        preferredBass: PitchName | None = None
        if 0 in chPitch:
            preferredBass = chPitch[0]
            del chPitch[0]  # remove the /bass entry

        roleList: list[int] = list(chPitch.keys())
        roleList.sort()
        roles: tuple[int, ...] = tuple(roleList)

        root: PitchName | None = None
        fifth: PitchName | None = None

        # availablePitches is only consulted as a last resort
        availablePitches: list[PitchName] = []
        for p in chPitch.values():
            if p == leadPitchName:
                continue
            availablePitches.append(p)

        if preferredBass and leadPitchName != preferredBass:
            # bass always gets the preferredBass, unless the lead is already on it.
            bass = MusicEngine.makeNote(preferredBass, durQL, copyFrom=lead, below=lead)
            MusicEngine.moveIntoRange(bass, partRange)
        elif roles in (
                (1, 3, 5),
                (1, 2, 5),
                (1, 4, 5),
                (1, 3, 6)):
            # Triad: you can double the root if there's no "extra" /bass note
            root = chPitch[1]
            fifth = chPitch[roles[2]]  # we treat 5 or 6 as the fifth
            other: PitchName = chPitch[roles[1]]

            if leadPitchName == root:
                # Lead is on root, take doubled root an octave below
                bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # root an octave below lead is too low, try the fifth below the lead
                    bass = MusicEngine.makeNote(fifth, durQL, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # still too low, just sing the same note (root) as the lead
                        bass = MusicEngine.copyNote(lead)
                        bass.quarterLength = durQL

            elif leadPitchName == other:
                # Lead is on 2, 3, or 4, take root a 9th, 10th or 11th below
                bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isTooLow(bass.pitch):
                    # Take fifth (below the lead note)
                    bass = MusicEngine.makeNote(fifth, durQL, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # Fine, take the root below the lead
                        bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)

            elif leadPitchName == fifth:
                # Lead is on fifth, take root below
                bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)
                if partRange.isOutOfRange(bass.pitch):
                    # Ugh. Lead must be really crazy. Force the bass into range,
                    # even if it is above the lead.
                    MusicEngine.moveIntoRange(bass, partRange)
            elif leadPitchName == preferredBass:
                # lead is on /bass note, take the root
                bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)
                MusicEngine.moveIntoRange(bass, partRange)
            else:
                # Should never happen, because we wouldn't call this routine if
                # the lead wasn't on a chord note.
                raise MusicEngineException(
                    'harmonizePillarChordBass: lead note not in pillar chord'
                )

        elif len(roles) == 4:
            # we only care about root and fifth
            if roles in (
                    (1, 3, 5, 7),
                    (1, 4, 5, 7),
                    (1, 2, 5, 7)):
                # 7th chord: no doubling the root.
                root = chPitch[1]
                fifth = chPitch[5]
            elif roles == (1, 3, 5, 6):
                # 6th chord: There are lots of ways to do this,
                # depending on context and what type of 6th chord it is (for example
                # we could treat a maj6 as a 7th chord rooted on chPitch[6]).
                # This is what we will do for now.
                root = chPitch[1]
                fifth = chPitch[5]
            elif roles == (1, 5, 7, 9):
                # 9th chord with no third
                root = chPitch[1]
                fifth = chPitch[5]
            elif roles == (3, 5, 7, 9):
                # 9th chord with no root: Treat it as a 7th chord rooted at 3
                root = chPitch[3]
                fifth = chPitch[7]
            elif roles == (1, 7, 9, 11):
                # 11th chord with no third/fifth: treat 11 as 5
                root = chPitch[1]
                fifth = chPitch[11]
            elif roles == (3, 7, 9, 11):
                # 11th chord with no root/fifth: Treat as if rooted at 9 (and weird)
                root = chPitch[9]
                fifth = chPitch[7]
            elif roles == (5, 7, 9, 11):
                # 11th chord with no root/third: Treat it as a 7th chord rooted at 5
                root = chPitch[5]
                fifth = chPitch[9]
            elif roles == (1, 9, 11, 13):
                # 13th chord with no third/fifth/seventh: Treat as 7th chord rooted at 9
                root = chPitch[9]
                fifth = chPitch[13]
            elif roles == (3, 9, 11, 13):
                # 13th chord with no root/fifth/seventh: Treat as rooted at 9, I think.
                # It's weird.
                root = chPitch[9]
                fifth = chPitch[13]
            elif roles == (5, 9, 11, 13):
                # 13th chord with no root/third/seventh: Treat as 7sus2 rooted on 5
                root = chPitch[5]
                fifth = chPitch[9]
            elif roles == (7, 9, 11, 13):
                # 13th chord with no root/third/fifth: Treat it as a 7th chord rooted at 7
                root = chPitch[7]
                fifth = chPitch[11]
            elif len(roles) == 4 and 1 in roles and 3 in roles and 5 in roles:
                # triad add <something>
                root = chPitch[1]
                fifth = chPitch[5]
            else:
                # hope for root and/or fifth to be there, but we will use
                # availablePitches if we have to.
                if 1 in chPitch:
                    root = chPitch[1]
                if 5 in chPitch:
                    fifth = chPitch[5]

            if root and fifth and leadPitchName == root:
                # put bass on fifth below lead, or above lead if necessary
                bass = MusicEngine.makeNote(fifth, durQL, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    bass = MusicEngine.makeNote(fifth, durQL, copyFrom=lead, above=lead)

            elif root and fifth and leadPitchName == fifth:
                # bass on root
                bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)
                if partRange.isTooHigh(bass.pitch):
                    bass = MusicEngine.makeNote(
                        root, durQL, copyFrom=lead, below=lead, extraOctaves=1
                    )

            elif (root and leadPitchName != root) or (fifth and leadPitchName != fifth):
                while True:
                    # we will only iterate once, breaking out if we find a good note
                    rootBelowLead: m21.note.Note | None = None
                    fifthBelowLead: m21.note.Note | None = None
                    if root:
                        rootBelowLead = MusicEngine.makeNote(
                            root, durQL, copyFrom=lead, below=lead
                        )
                    if fifth:
                        fifthBelowLead = MusicEngine.makeNote(
                            fifth, durQL, copyFrom=lead, below=lead
                        )

                    if rootBelowLead is not None and partRange.isInRange(rootBelowLead.pitch):
                        bass = rootBelowLead
                        break

                    if fifthBelowLead is not None and partRange.isInRange(fifthBelowLead.pitch):
                        bass = fifthBelowLead
                        break

                    if rootBelowLead is not None and partRange.isTooHigh(rootBelowLead.pitch):
                        if t.TYPE_CHECKING:
                            assert root is not None
                        rootBelowLead = MusicEngine.makeNote(
                            root, durQL, copyFrom=lead, below=lead, extraOctaves=1
                        )
                        if partRange.isInRange(rootBelowLead.pitch):
                            bass = rootBelowLead
                            break

                    # give up on root, lets go with the fifth, positioned to be in-range,
                    # either an extra octave below the lead, or just above the lead
                    if fifth and fifthBelowLead is not None:
                        if partRange.isTooHigh(fifthBelowLead.pitch):
                            fifthBelowLead = MusicEngine.makeNote(
                                fifth, durQL, copyFrom=lead, below=lead, extraOctaves=1
                            )
                            if partRange.isInRange(fifthBelowLead.pitch):
                                bass = fifthBelowLead
                                break
                        else:
                            # must have been too low, try above the lead
                            fifthAboveLead = MusicEngine.makeNote(
                                fifth, durQL, copyFrom=lead, above=lead
                            )
                            if partRange.isInRange(fifthAboveLead.pitch):
                                bass = fifthAboveLead
                                break

                    # OK, give up on being smart, and use the root or fifth (or
                    # availablePitches[0] if no root or fifth), positioned in bass
                    # range, no matter how far from lead.  The lead note must be
                    # _way_ out of range.
                    if root in availablePitches:
                        bass = MusicEngine.makeNote(root, durQL, copyFrom=lead, below=lead)
                        MusicEngine.moveIntoRange(bass, partRange)
                    elif fifth in availablePitches:
                        bass = MusicEngine.makeNote(fifth, durQL, copyFrom=lead, below=lead)
                        MusicEngine.moveIntoRange(bass, partRange)
                    else:
                        if len(availablePitches) < 2:
                            raise MusicEngineException(f'too few available pitches: {chPitch}')
                        bass = MusicEngine.makeNote(
                            availablePitches[0], durQL, copyFrom=lead, below=lead
                        )
                        MusicEngine.moveIntoRange(bass, partRange)

                    # all done, break out of "loop once"
                    break

            else:
                # ignore root/third/fifth/seventh and just use availablePitches
                if len(availablePitches) < 2:
                    raise MusicEngineException(f'too few available pitches: {chPitch}')
                bass = MusicEngine.makeNote(availablePitches[0], durQL, copyFrom=lead, below=lead)
                MusicEngine.moveIntoRange(bass, partRange)
        else:
            if len(availablePitches) < 2:
                raise MusicEngineException(f'too few available pitches: {chPitch}')
            bass = MusicEngine.makeNote(availablePitches[0], durQL, copyFrom=lead, below=lead)
            MusicEngine.moveIntoRange(bass, partRange)

        # Specify stem directions explicitly
        bass.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Bass]

        # Put the bass note in the bass voice
        bassVoice: m21.stream.Voice = measure[PartName.Bass]
        bassVoice.insert(offset, bass)

    @staticmethod
    def harmonizePillarChordTenor(
        partRanges: dict[PartName, VocalRange],
        measure: FourVoices,
        offset: OffsetQL,
        durQL: OffsetQL,
        pillarChord: Chord,
        thisFourNotes: FourNotes,
        prevFourNotes: FourNotes
    ):
        # From "Arranging Barbershop: Volume 2" pp11-13
        #
        # Tenor: (Above melody) Use one of the unused notes in the chord, or double the
        #           root if it is a triad.  Consider voice leading and seek for fewer
        #           awkward leaps.  There may be times with choosing a different bass
        #           note would allow a smoother tenor part.

        # Note that we harmonize the entire Tenor part after the entire Bass part and
        # before the Bari part.  So we can reference the Bass part (and adjust it as
        # necessary).

        if isinstance(pillarChord.sym, m21.harmony.NoChord):
            raise MusicEngineException('harmonizePillarChordTenor: NoChord is not a pillar chord')

        partRange: VocalRange = partRanges[PartName.Tenor]

        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note = thisFourNotes[PartName.Bass]
        tenor: m21.note.Note | None = None

        leadPitchName: PitchName = PitchName(lead.pitch.name)

        if not isinstance(bass, m21.note.Note):
            space: m21.note.Rest = m21.note.Rest()
            space.quarterLength = lead.quarterLength
            space.style.hideObjectOnPrint = True
            measure[PartName.Tenor].insert(offset, space)
            return

        availablePitchNames: list[PitchName] = thisFourNotes.getAvailablePitchNames(pillarChord)
        if not availablePitchNames:
            raise MusicEngineException('no available pitches for tenor')

        if t.TYPE_CHECKING:
            assert isinstance(lead, m21.note.Note)

        # First attempt: Just go for what's available, starting with the available
        # notes above the lead (preferring closer-to-the-lead notes), then the
        # available notes below the lead (preferring closer-to-the-lead notes).
        orderedPitchNames: list[PitchName] = MusicEngine.orderPitchNamesStartingAbove(
            availablePitchNames,
            leadPitchName
        )

        for p in orderedPitchNames:
            tenor = MusicEngine.makeNote(p, durQL, copyFrom=lead, above=lead)
            if partRange.isInRange(tenor.pitch):
                break

        if t.TYPE_CHECKING:
            assert tenor is not None

        if partRange.isTooLow(tenor.pitch):
            # try again, an extra octave up
            for p in orderedPitchNames:
                tenor = MusicEngine.makeNote(p, durQL, copyFrom=lead, above=lead, extraOctaves=1)
                if partRange.isInRange(tenor.pitch):
                    break

        if t.TYPE_CHECKING:
            assert tenor is not None

        if partRange.isTooHigh(tenor.pitch):
            for p in reversed(orderedPitchNames):
                tenor = MusicEngine.makeNote(p, durQL, copyFrom=lead, below=lead)
                if partRange.isInRange(tenor.pitch):
                    break

        if t.TYPE_CHECKING:
            assert tenor is not None

        if partRange.isTooHigh(tenor.pitch):
            # try again, an extra octave below
            for p in reversed(orderedPitchNames):
                tenor = MusicEngine.makeNote(p, durQL, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isInRange(tenor.pitch):
                    break

        if t.TYPE_CHECKING:
            assert tenor is not None

        if partRange.isOutOfRange(tenor.pitch):
            # last resort: the first note above the lead, put in whatever octave works.
            tenor = MusicEngine.makeNote(availablePitchNames[0], durQL, copyFrom=lead, above=lead)
            MusicEngine.moveIntoRange(tenor, partRange)

        # Specify stem directions explicitly
        tenor.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Tenor]

        tenorVoice: m21.stream.Voice = measure[PartName.Tenor]
        tenorVoice.insert(offset, tenor)

    @staticmethod
    def harmonizePillarChordBari(
        partRanges: dict[PartName, VocalRange],
        measure: FourVoices,
        offset: OffsetQL,
        durQL: OffsetQL,
        pillarChord: Chord,
        thisFourNotes: FourNotes,
        prevFourNotes: FourNotes
    ):
        # From "Arranging Barbershop: Volume 2" pp11-13
        #
        # Bari: Complete each chord or double the root if it is a triad.  As with the
        #           tenor, try to not have the part jump around unnecessarily and consider
        #           changing the bass or tenor note if it leads to a smoother baritone
        #           part.

        # Note that we harmonize the entire Bari part after the entire Bass part and
        # the entire Tenor part.  So we can reference the Bass/Tenor parts (and adjust
        # them as necessary).

        if isinstance(pillarChord.sym, m21.harmony.NoChord):
            raise MusicEngineException('harmonizePillarChordBari: NoChord is not a pillar chord')

        bariPartRange: VocalRange = partRanges[PartName.Bari]
        tenorPartRange: VocalRange = partRanges[PartName.Tenor]

        tenor: m21.note.Note = thisFourNotes[PartName.Tenor]
        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note = thisFourNotes[PartName.Bass]
        if not isinstance(bass, m21.note.Note):
            space: m21.note.Rest = m21.note.Rest()
            space.quarterLength = lead.quarterLength
            space.style.hideObjectOnPrint = True
            measure[PartName.Bari].insert(offset, space)
            return

        availablePitchNames: list[PitchName] = thisFourNotes.getAvailablePitchNames(pillarChord)

        # bari gets whatever is left over (we can improve voice leading by trading notes,
        # obviously, but for now this is it).
        bari: m21.note.Note = MusicEngine.makeNote(
            availablePitchNames[0],
            durQL,
            copyFrom=lead,
            below=tenor
        )
        MusicEngine.moveIntoRange(bari, bariPartRange)

        tenorChanged: bool = False

        if bari.pitch < bass.pitch:
            # bari is below the bass, that's not right.  We need to push the tenor up to
            # the bari pitch (in a higher octave), and take the tenor pitch (in the bari
            # range.  We're just spreading the chord upward, since there wasn't room for
            # the bari.
            oldBari: m21.note.Note = bari
            oldTenor: m21.note.Note = tenor
            bari = MusicEngine.makeNote(
                PitchName(oldTenor.pitch.name), durQL, copyFrom=lead, above=bass
            )
            tenor = MusicEngine.makeNote(
                PitchName(oldBari.pitch.name), durQL, copyFrom=lead, above=lead
            )
            MusicEngine.moveIntoRange(bari, bariPartRange)
            MusicEngine.moveIntoRange(tenor, tenorPartRange)
            tenorChanged = True
        elif bari.pitch > tenor.pitch:
            # trade with the tenor (this time the bari is taking the tenor note as is,
            # and the tenor is taking the bari note as is).  But moveIntoRange anyway,
            # to be sure.
            oldBari = bari
            oldTenor = tenor
            bari = MusicEngine.copyNote(oldTenor)
            tenor = MusicEngine.copyNote(oldBari)
            MusicEngine.moveIntoRange(bari, bariPartRange)
            MusicEngine.moveIntoRange(tenor, tenorPartRange)
            tenorChanged = True

        # Specify stem directions explicitly
        bari.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Bari]
        bariVoice: m21.stream.Voice = measure[PartName.Bari]
        bariVoice.insert(offset, bari)

        if tenorChanged:
            tenor.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Tenor]
            tenorVoice: m21.stream.Voice = measure[PartName.Tenor]
            tenorVoice.replace(oldTenor, tenor)

    @staticmethod
    def orderPitchNamesStartingAbove(
        pitches: list[PitchName],
        baseName: PitchName
    ) -> list[PitchName]:
        def semitonesAboveBaseName(pitchName: PitchName) -> int:
            pitch = pitchName.pitch
            basePitch = baseName.pitch
            intv: m21.interval.Interval = m21.interval.Interval(basePitch, pitch)
            semitones: float = intv.chromatic.semitones
            if semitones == 0:
                semitones = 12  # put baseName at end of list, not start
            return round(semitones)

        sortedPitches = sorted(pitches, key=semitonesAboveBaseName)
        return sortedPitches

    @staticmethod
    def getFourNotesAtOffset(
        measure: FourVoices,
        offset: OffsetQL
    ) -> FourNotes:
        tenor: m21.note.Note | m21.note.Rest | None = None
        lead: m21.note.Note | m21.note.Rest | None = None
        bari: m21.note.Note | m21.note.Rest | None = None
        bass: m21.note.Note | m21.note.Rest | None = None

        tenorVoice: m21.stream.Voice = measure[PartName.Tenor]
        tenorNotes: list[m21.note.Note | m21.note.Rest] = list(
            tenorVoice.recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in tenorNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                # Sometimes we end up with multiple notes/rests at a single offset.
                # In that case, take the first note (or first rest, if no notes)
                if tenor is None or (
                        isinstance(tenor, m21.note.Rest) and isinstance(n, m21.note.Note)):
                    tenor = n
                continue
            if n.offset > offset:
                break

        leadNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Lead].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in leadNotes:
            if n.duration.isGrace:
                continue
            # The offset is the harmony offset; the lead note we're looking for may actually
            # just overlap this offset, not start at it.
            if n.offset <= offset < opFrac(n.offset + n.quarterLength):
                # Sometimes we end up with multiple notes/rests at a single offset.
                # In that case, take the first note (or first rest, if no notes)
                if lead is None or (
                        isinstance(lead, m21.note.Rest) and isinstance(n, m21.note.Note)):
                    lead = n
                continue
            if n.offset > offset:
                break

        bariNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bari].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in bariNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                # Sometimes we end up with multiple notes/rests at a single offset.
                # In that case, take the first note (or first rest, if no notes)
                if bari is None or (
                        isinstance(bari, m21.note.Rest) and isinstance(n, m21.note.Note)):
                    bari = n
                continue
            if n.offset > offset:
                break

        bassNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bass].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in bassNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                # Sometimes we end up with multiple notes/rests at a single offset.
                # In that case, take the first note (or first rest, if no notes)
                if bass is None or (
                        isinstance(bass, m21.note.Rest) and isinstance(n, m21.note.Note)):
                    bass = n
                continue
            if n.offset > offset:
                break

        return FourNotes(tenor=tenor, lead=lead, bari=bari, bass=bass)

    @staticmethod
    def getFourNotesBeforeOffset(
        measure: FourVoices,
        prevMeasure: FourVoices | None,
        offset: OffsetQL
    ) -> FourNotes:
        tenor: m21.note.Note | m21.note.Rest | None
        lead: m21.note.Note | m21.note.Rest | None
        bari: m21.note.Note | m21.note.Rest | None
        bass: m21.note.Note | m21.note.Rest | None
        tenorNotes: list[m21.note.Note | m21.note.Rest]
        leadNotes: list[m21.note.Note | m21.note.Rest]
        bariNotes: list[m21.note.Note | m21.note.Rest]
        bassNotes: list[m21.note.Note | m21.note.Rest]

        if offset == 0:
            if prevMeasure is None:
                # there is no previous chord, return an empty FourNotes (all Nones)
                return FourNotes()

            tenor = None
            lead = None
            bari = None
            bass = None
            # gotta grab last chord in prevMeasure instead
            tenorNotes = list(prevMeasure[PartName.Tenor]
                .getElementsByClass([m21.note.Note, m21.note.Rest]))
            if tenorNotes:
                tenor = tenorNotes[-1]

            leadNotes = list(prevMeasure[PartName.Lead]
                .getElementsByClass([m21.note.Note, m21.note.Rest]))
            if leadNotes:
                lead = leadNotes[-1]

            bariNotes = list(prevMeasure[PartName.Bari]
                .getElementsByClass([m21.note.Note, m21.note.Rest]))
            if bariNotes:
                bari = bariNotes[-1]

            bassNotes = list(prevMeasure[PartName.Bass]
                .getElementsByClass([m21.note.Note, m21.note.Rest]))
            if bassNotes:
                bass = bassNotes[-1]

            return FourNotes(tenor=tenor, lead=lead, bari=bari, bass=bass)

        # Non-zero offset, don't need prevMeasure at all, just get all the
        # notes/rests in the voice up to (but not including) offset, and
        # return the last one.
        # Note that this works for any lead notes that overlap offset, since
        # the previous lead note will be the same as the current lead note,
        # in that case.
        tenor = None
        lead = None
        bari = None
        bass = None

        tenorNotes = list(
            measure[PartName.Tenor].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
            .getElementsByOffsetInHierarchy(
                offsetStart=0, offsetEnd=offset, includeEndBoundary=False)
        )
        if tenorNotes:
            tenor = tenorNotes[-1]

        leadNotes = list(
            measure[PartName.Lead].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
            .getElementsByOffsetInHierarchy(
                offsetStart=0, offsetEnd=offset, includeEndBoundary=False)
        )
        if leadNotes:
            lead = leadNotes[-1]

        bariNotes = list(
            measure[PartName.Bari].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
            .getElementsByOffsetInHierarchy(
                offsetStart=0, offsetEnd=offset, includeEndBoundary=False)
        )
        if bariNotes:
            bari = bariNotes[-1]

        bassNotes = list(
            measure[PartName.Bass].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
            .getElementsByOffsetInHierarchy(
                offsetStart=0, offsetEnd=offset, includeEndBoundary=False)
        )
        if bassNotes:
            bass = bassNotes[-1]

        return FourNotes(tenor=tenor, lead=lead, bari=bari, bass=bass)

    @staticmethod
    def makeNote(
        pitchName: PitchName,
        durQL: OffsetQL,
        copyFrom: m21.note.Note,
        below: m21.note.Note | None = None,
        above: m21.note.Note | None = None,
        extraOctaves: int = 0,
    ) -> m21.note.Note:
        if below is not None and above is not None:
            raise MusicEngineException(
                'makeNote must be passed exactly one (not both) of above/below'
            )

        if extraOctaves < 0:
            raise MusicEngineException(
                'extraOctaves must be > 0; it will be "added" in the above or below direction.'
            )

        output: m21.note.Note = MusicEngine.copyNote(copyFrom)
        if below is not None:
            output.pitch = m21.pitch.Pitch(name=pitchName.name, octave=below.pitch.octave)
            if output.pitch >= below.pitch:
                output.pitch.octave -= 1  # type: ignore
            if extraOctaves:
                output.pitch.octave -= extraOctaves  # type: ignore

        elif above is not None:
            output.pitch = m21.pitch.Pitch(name=pitchName.name, octave=above.pitch.octave)
            if output.pitch <= above.pitch:
                output.pitch.octave += 1  # type: ignore
            if extraOctaves:
                output.pitch.octave += extraOctaves  # type: ignore
        else:
            raise MusicEngineException(
                'makeNote must be passed exactly one (not neither) of above/below'
            )

        output.quarterLength = durQL
        return output

    @staticmethod
    def makeAndInsertNote(
        pitchName: PitchName,
        copyFrom: m21.note.Note,
        replacedNote: m21.note.Note | m21.note.Rest | None = None,
        below: m21.note.Note | None = None,
        above: m21.note.Note | None = None,
        extraOctaves: int = 0,
        voice: m21.stream.Voice | None = None,
        offset: OffsetQL | None = None,
        durQL: OffsetQL | None = None
    ) -> m21.note.Note:
        if voice is None or offset is None or durQL is None:
            raise MusicEngineException('makeAndInsertNote requires voice and offset and durQL')

        # remove the note it replaces from voice first
        if replacedNote is not None:
            if replacedNote.getOffsetInHierarchy(voice) != offset:
                raise MusicEngineException('replaced note/rest must be at offset in voice')
            voice.remove(replacedNote)

        # make the new note
        newNote: m21.note.Note = MusicEngine.makeNote(
            pitchName,
            durQL,
            copyFrom=copyFrom,
            above=above,
            below=below,
            extraOctaves=extraOctaves
        )

        # insert the new note in voice
        voice.insert(offset, newNote)

        return newNote

    # @staticmethod
    # def appendShoppedChord(
    #     fourNotes: FourNotes,
    #     fourVoices: FourVoices
    # ):
    #     for note, voice in zip(fourNotes, fourVoices):
    #         voice.append(note)
    #
    # @staticmethod
    # def appendDeepCopyTo(gn: m21.note.GeneralNote, fourVoices: FourVoices):
    #     for s in fourVoices:
    #         s.append(deepcopy(gn))

    @staticmethod
    def findChordAtOffset(
        stream: m21.stream.Stream,
        offset: OffsetQL
    ) -> Chord | None:
        cs: m21.harmony.ChordSymbol | None = MusicEngine.findChordSymbolAtOffset(stream, offset)
        if cs is not None:
            return Chord(cs)  # makes a deepcopy of cs
        return None

    @staticmethod
    def findChordSymbolAtOffset(
        stream: m21.stream.Stream,
        offset: OffsetQL
    ) -> m21.harmony.ChordSymbol | None:
        for cs in stream[m21.harmony.ChordSymbol]:
            startChord: OffsetQL = cs.getOffsetInHierarchy(stream)
            endChord: OffsetQL = opFrac(startChord + cs.duration.quarterLength)
            if startChord <= offset < endChord:
                return cs  # no deepcopy, this is the ChordSymbol that is in the stream

        return None

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
    def useAsLeadSheet(
        score: m21.stream.Score
    ) -> tuple[m21.stream.Part, m21.stream.Part]:
        # returns melodyPart, chordsPart (can be the same part).
        parts: list[m21.stream.Part] = list(score.parts)
        if not parts:
            raise MusicEngineException('Unuseable leadsheet; no parts.')

        # we require one Part to be the melody: only one Voice throughout the Part, Notes and
        # Rests only (no Chords), and the score must have ChordSymbols, either in the melody
        # Part, or in another Part.
        # There can be other parts, but we will ignore them for now (in future, we could
        # go without chord symbols if there is a piano accompaniment, for example).
        melodyPart: m21.stream.Part | None = None
        for part in parts:
            multipleVoices: bool = False
            badChords: bool = False
            for meas in list(part[m21.stream.Measure]):
                voices: list[m21.stream.Voice] = list(meas[m21.stream.Voice])
                # 0 voices or 1 voice is fine (0 voices means the measure is the "voice")
                if len(voices) > 1:
                    # unuseable part: multiple voices
                    # Note: we have already removed rest-only voices.
                    multipleVoices = True
                    break

                checkForChordsHere: m21.stream.Voice | m21.stream.Measure = meas
                if voices:
                    checkForChordsHere = voices[0]
                if (checkForChordsHere
                        .getElementsByClass(m21.chord.Chord)               # look for Chords that
                        .getElementsNotOfClass(m21.harmony.ChordSymbol)):  # aren't ChordSymbols
                    badChords = True
                    break

            if not multipleVoices and not badChords:
                melodyPart = part
                break

        if melodyPart is None:
            raise MusicEngineException(
                'Unuseable leadsheet; multiple voices or chords in every part.'
            )

        chordPart: m21.stream.Part | None = None
        for part in parts:
            numChords: int = 0
            for _cs in part[m21.harmony.ChordSymbol]:
                numChords += 1
                if numChords > 1:
                    # I saw several scores that had only one chord symbol.  Rejecting those.
                    chordPart = part
                    break

            if chordPart is not None:
                break

        if chordPart is None:
            raise MusicEngineException(
                'Unuseable leadsheet; no chord symbols.'
            )

#         # check for weird duration objects making measures too big
#         for meas in melodyPart[m21.stream.Measure]:
#             if meas.quarterLength > meas.getTimeSignatures()[0].barDuration.quarterLength:
#                 raise MusicEngineException(
#                     'Unuseable leadsheet; some measures are longer than their time signature.'
#                 )
#
#         if melodyPart is not chordPart:
#             # check the chordPart, too
#             for meas in chordPart[m21.stream.Measure]:
#                 if meas.quarterLength > meas.getTimeSignatures()[0].barDuration.quarterLength:
#                     raise MusicEngineException(
#                         'Unuseable leadsheet; some measures are longer than their time signature.'
#                     )

        return melodyPart, chordPart

    @staticmethod
    def countHarmonyGaps(score: m21.stream.Score) -> int:
        # if not a (non-empty) shopped score, return -1
        parts: list[m21.stream.Part] = list(score[m21.stream.Part])
        if len(parts) != 2:
            return -1

        for partIdx, part in enumerate(parts):
            measures: list[m21.stream.Measure] = list(part[m21.stream.Measure])
            if not measures:
                return -1

            for measure in measures:
                voices: list[m21.stream.Voice] = list(measure[m21.stream.Voice])
                if len(voices) != 2:
                    return -1
                if partIdx == 0:
                    if voices[0].id != 'tenor':
                        return -1
                    if voices[1].id != 'lead':
                        return -1
                elif partIdx == 1:
                    if voices[0].id != 'bari':
                        return -1
                    if voices[1].id != 'bass':
                        return -1

        # for every lead note, see whether there are notes or spaces in the corresponding
        # tenor/bari/bass voices.  Count the spaces (gaps).

        # assume that you can check for a filled-in harmony by looking at the tenor part, since
        # we're careful to never partly fill in only one or two harmony parts.
        harmonyGaps: int = 0
        topStaff: m21.stream.Part = parts[0]
        for measure in topStaff[m21.stream.Measure]:
            voices = list(measure[m21.stream.Voice])
            tenorVoice = voices[0]
            leadVoice = voices[1]
            for leadNote in leadVoice[m21.note.Note]:
                offsetInMeasure: OffsetQL = leadNote.getOffsetInHierarchy(measure)
                for tenorNoteOrRest in tenorVoice[(m21.note.Note, m21.note.Rest)]:
                    if tenorNoteOrRest.getOffsetInHierarchy(measure) == offsetInMeasure:
                        if isinstance(tenorNoteOrRest, m21.note.Rest):
                            harmonyGaps += 1
                        # go on to the next leadNote in any case; we found the matching
                        # tenor note/rest
                        break

        return harmonyGaps
