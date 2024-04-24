import typing as t
import pathlib
import re
import zipfile
from enum import Enum, IntEnum, auto
from io import BytesIO
from copy import copy, deepcopy
from collections.abc import Sequence

import music21 as m21
from music21.common.numberTools import OffsetQL, opFrac
from music21.figuredBass import realizerScale


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
        self.roleToPitchNames: dict[int, str] = {}
        self.preferredBassPitchName: str = ''  # may not be mentioned anywhere else in self

        if isinstance(self.sym, m21.harmony.NoChord):
            return

        bass: m21.pitch.Pitch = self.sym.bass()
        if bass is not None and bass.name != self.sym.root().name:
            # we have a specified bass note, perhaps not in the main chord
            # Stash off it's name as the preferred bass pitchName, and
            # recompute the chord pitches as if there was no bass specified
            # (by setting the bass to the root).
            self.preferredBassPitchName = bass.name
            self.sym.bass(self.sym.root())
            MusicEngine._updatePitches(self.sym)

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

        pitchNames: list[str] = [p.name for p in self.pitches]

        # loop over pitches and pitchesForRole, moving pitchesForRole elements to match pitches
        role: int = 1
        pitchIdx: int = 0
        while pitchIdx < len(pitchNames):
            pitchName = pitchNames[pitchIdx]
            pitchForRole = pitchesForRole[role]
            while pitchForRole is None:
                role += 1
                pitchForRole = pitchesForRole[role]
            if pitchForRole.name == pitchName:
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
                self.roleToPitchNames[role] = pitchForRole.name

    def __len__(self) -> int:
        return len(self.roleToPitchNames)

    def __getitem__(self, idx: int | str | slice) -> t.Any:  # -> str | None (pitchName)
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

    def getAvailablePitchNames(self, chord: Chord) -> list[str]:
        # We assume that bass harmonization doesn't call this, and (also) will have
        # already used the /bass note if specified.
        availableRoleToPitchNames: dict[int, str] = (
            MusicEngine.getChordVocalParts(chord, self[PartName.Lead].name)
        )
        bass: str = ''
        roleToPitchNamesWithoutBass: dict[int, str] = copy(availableRoleToPitchNames)
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
                if n.pitch.name in roleToPitchNamesWithoutBass.values():
                    if doubleTheRoot and n.pitch.name == roleToPitchNamesWithoutBass.get(1, None):
                        # don't remove the root until you see the root a second time
                        doubleTheRoot = False
                        continue

                    removeRole: int = 0  # there is no role 0
                    for k, v in roleToPitchNamesWithoutBass.items():
                        if v == n.pitch.name:
                            removeRole = k
                            break
                    if removeRole != 0:
                        roleToPitchNamesWithoutBass.pop(removeRole, None)
                elif n.pitch.name == bass:
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

    def getTranspositionSemitones(
        self,
        partName: PartName,
        arrType: ArrangementType
    ) -> int:
        if self.fullRange is None:
            raise MusicEngineException('getTranspositionSemitones called on empty VocalRange')

        goalRange: VocalRange = PART_RANGES[arrType][partName]
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

        # if lowEndSemitonesTooLow > 0:
        #     # we need to transpose up a bit
        #     if highEndSemitonesTooLow <= 0:
        #         # no room to transpose up
        #         # Strike a compromise
        #         semitonesTooLow = round((lowEndSemitonesTooLow + highEndSemitonesTooLow) / 2.)
        #     else:
        #         semitonesTooLow = round(lowEndSemitonesTooLow)
        # elif highEndSemitonesTooLow < 0:
        #     # we need to transpose down a bit
        #     if lowEndSemitonesTooLow >= 0:
        #         # no room to transpose down
        #         # Strike a compromise
        #         semitonesTooLow = round((lowEndSemitonesTooLow + highEndSemitonesTooLow) / 2.)
        #     else:
        #         semitonesTooLow = round(highEndSemitonesTooLow)

        return semitonesTooLow


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

        if hasattr(output, 'music_engine_badly_spelled_pitch'):
            del output.music_engine_badly_spelled_pitch  # type: ignore
        if hasattr(output, 'music_engine_well_spelled_pitch'):
            del output.music_engine_well_spelled_pitch  # type: ignore

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

        keySigAndTransposeIntervalAtOffset: dict[
            OffsetQL,
            tuple[m21.key.KeySignature, m21.interval.Interval]
        ] = {}
        for keySig in keySigs:
            offsetInScore: OffsetQL = opFrac(keySig.getOffsetInHierarchy(score))
            if offsetInScore not in keySigAndTransposeIntervalAtOffset:
                interval: m21.interval.Interval = MusicEngine.getBestTranspositionForKeySig(
                    keySig, semitonesUp
                )
                keySigAndTransposeIntervalAtOffset[offsetInScore] = keySig, interval

        if opFrac(0) not in keySigAndTransposeIntervalAtOffset:
            startKey: m21.key.KeySignature = m21.key.KeySignature(0)
            interval = MusicEngine.getBestTranspositionForKeySig(startKey, semitonesUp)
            keySigAndTransposeIntervalAtOffset[opFrac(0)] = startKey, interval

        # turn it into a sorted (by offset) list of [offset, keysig, interval] tuples

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
                MusicEngine.transposeAlternateSpellings(partialScore, interval)

    STEM_DIRECTION: dict[PartName, str] = {
        PartName.Tenor: 'up',
        PartName.Lead: 'down',
        PartName.Bari: 'up',
        PartName.Bass: 'down'
    }

    @staticmethod
    def realizeChordSymbolDurations(piece: m21.stream.Stream):
        # this is a copy of m21.harmony.realizeChordSymbolDurations, that instead
        # of extending a chordsym duration beyond the end-of-measure, will extend
        # to end of measure, and then insert a copy of the chordsym into the start
        # of the next measure, with the remainder of the duration.
        # We make the simplifying assumption (for now) that any given ChordSymbol
        # will only cross one barline.

        # TODO: deal with two chordsyms at the same offset; they should both last until
        # the _next_ chord.

        pf = piece.flatten()
        onlyChords = list(pf.getElementsByClass(m21.harmony.ChordSymbol))

        first = True
        lastChord = None

        if len(onlyChords) == 0:
            return piece

        for cs in onlyChords:
            if first:
                first = False
                lastChord = cs
                continue

            if t.TYPE_CHECKING:
                assert lastChord is not None

            # last/thisChordMeas might be Voices; if so I hope they are both
            # at offset 0 in their respective Measures.
            lastChordMeas: m21.stream.Stream | None = piece.containerInHierarchy(
                lastChord, setActiveSite=False)
            thisChordMeas: m21.stream.Stream | None = piece.containerInHierarchy(
                cs, setActiveSite=False)

            if t.TYPE_CHECKING:
                assert lastChordMeas is not None
                assert thisChordMeas is not None

            qlDiff = pf.elementOffset(cs) - pf.elementOffset(lastChord)
            if lastChordMeas is thisChordMeas:
                lastChord.duration.quarterLength = qlDiff
            else:
                # split qlDiff into two parts:
                # 1. the available room in lastChordMeas, and
                # 2. the remainder (which will land in thisChordMeas)
                thisChordOffsetInMeas: OffsetQL = cs.getOffsetInHierarchy(thisChordMeas)
                qlDiff1: OffsetQL = qlDiff - thisChordOffsetInMeas
                qlDiff2: OffsetQL = qlDiff - qlDiff1
                lastChord.duration.quarterLength = qlDiff1
                if qlDiff2 != 0:
                    lastChord2: m21.harmony.ChordSymbol = deepcopy(lastChord)
                    lastChord2.duration.quarterLength = qlDiff2
                    thisChordMeas.insert(0, lastChord2)
            lastChord = cs

        # on exit from the loop, all but lastChord has been handled
        if t.TYPE_CHECKING:
            assert lastChord is not None
        qlDiff = pf.highestTime - pf.elementOffset(lastChord)
        if lastChordMeas is thisChordMeas:
            lastChord.duration.quarterLength = qlDiff
        else:
            # split qlDiff into two parts:
            # 1. the available room in lastChordMeas, and
            # 2. the remainder (which will land in thisChordMeas)
            lastChordMeas = piece.containerInHierarchy(lastChord, setActiveSite=False)
            if t.TYPE_CHECKING:
                assert lastChordMeas is not None
            qlDiff1 = qlDiff - lastChord.getOffsetInHierarchy(lastChordMeas)
            qlDiff2 = qlDiff - qlDiff1
            lastChord.duration.quarterLength = qlDiff1
            if qlDiff2 != 0:
                lastChord2 = deepcopy(lastChord)
                lastChord2.duration.quarterLength = qlDiff2
                if t.TYPE_CHECKING:
                    assert thisChordMeas is not None
                thisChordMeas.insert(0, lastChord2)

    @staticmethod
    def fixupBadlySpelledNotes(melody: m21.stream.Part, chords: m21.stream.Part):
        # note that melody and chords may or may not be the same part.
        for nc in melody[m21.note.NotRest]:
            if isinstance(nc, m21.harmony.ChordSymbol):
                continue
            offset: OffsetQL = nc.getOffsetInHierarchy(melody)
            if isinstance(nc, m21.chord.Chord):
                nc = nc.notes[0]

            if t.TYPE_CHECKING:
                assert isinstance(nc, m21.note.Note)
            notePitch: m21.pitch.Pitch = deepcopy(nc.pitch)
            chord = MusicEngine.findChordAtOffset(chords, offset)
            if chord is None:
                continue

            chordPitchNames: list[str] = [p.name for p in chord.pitches]
            if notePitch.name in chordPitchNames:
                continue

            # check for enharmonic equivalence
            notePitch.getEnharmonic(inPlace=True)
            if notePitch.name in chordPitchNames:
                nc.music_engine_badly_spelled_pitch = deepcopy(nc.pitch)  # type: ignore
                nc.music_engine_well_spelled_pitch = deepcopy(notePitch)  # type: ignore
                nc.pitch = notePitch
                continue

            # check again (some pitches cycle between three enharmonics)
            notePitch.getEnharmonic(inPlace=True)
            if notePitch.name in chordPitchNames:
                nc.music_engine_badly_spelled_pitch = deepcopy(nc.pitch)  # type: ignore
                nc.music_engine_well_spelled_pitch = deepcopy(notePitch)  # type: ignore
                nc.pitch = notePitch
                continue

    @staticmethod
    def transposeAlternateSpellings(stream: m21.stream.Stream, interval: m21.interval.Interval):
        for nc in stream[m21.note.Note]:
            if hasattr(nc, 'music_engine_badly_spelled_pitch'):
                nc.music_engine_badly_spelled_pitch.transpose(  # type: ignore
                    interval, inPlace=True
                )
            if hasattr(nc, 'music_engine_well_spelled_pitch'):
                nc.music_engine_well_spelled_pitch.transpose(  # type: ignore
                    interval, inPlace=True
                )

    @staticmethod
    def putBackAnyBadlySpelledNotes(stream: m21.stream.Stream):
        for nc in stream[m21.note.Note]:
            if hasattr(nc, 'music_engine_badly_spelled_pitch'):
                nc.pitch = deepcopy(nc.music_engine_badly_spelled_pitch)  # type: ignore

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

        melody: m21.stream.Part | None
        chords: m21.stream.Part | None
        melody, chords = MusicEngine.useAsLeadSheet(leadSheet)
        if melody is None or chords is None:
            raise MusicEngineException('not a useable leadsheet (no melody, or no chords)')

        # We call realizeChordSymbolDurations() because otherwise ChordSymbols have
        # duration == 0 or 1, which doesn't help us find the ChordSymbol that has a
        # time range that contains a particular offset.  We have our own copy of this,
        # with an added "bugfix" that splits chordsyms across barlines, so the new
        # chordsym duration doesn't push the barline out.
        MusicEngine.realizeChordSymbolDurations(leadSheet)

        # more fixups to the leadsheet score
        M21Utilities.fixupBadChordKinds(leadSheet, inPlace=True)

        # if a melody note is in the chord enharmonically, respell the
        # melody note to be obviously in the chord. e.g. a C melody note
        # in a Em7#5 chord.  The #5 in an Em7#5 is a B#, which is enharmonically
        # equivalent to C, so fix the melody note's spelling to be B#.  This
        # helps simplify the harmonization code, and everyone likes good spelling.
        MusicEngine.fixupBadlySpelledNotes(melody, chords)

        # remove all beams (because the beams get bogus in the partially filled-in
        # harmony parts, causing occasional export crashes).  We will call
        # m21.stream.makeBeams when necessary, to make valid beams again.
        MusicEngine.removeAllBeams(leadSheet)

        # Most directions, dynamics, metronome marks, etc, will no longer apply.
        MusicEngine.removeAllDirections(leadSheet)

        # Now pick a key that will work for lead voice range, and transpose.
        melodyInfo: VocalRangeInfo = VocalRangeInfo(melody)
        semitones: int = (
            melodyInfo.getTranspositionSemitones(PartName.Lead, arrType)
        )

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
        shoppedVoices: list[FourVoices]
        shopped, shoppedVoices = MusicEngine.processPillarChordsLead(
            arrType,
            melody,
            chords,
            leadSheet.metadata
        )

        # Then we will harmonize the three harmony parts, one at a time all the way
        # through, harmonizing only the melody notes that are in the specified chord
        # (the melody pillar notes), potentially tweaking other already harmonized
        # parts as we go, to get better voice leading.
        for partName in (PartName.Bass, PartName.Tenor, PartName.Bari):
            MusicEngine.processPillarChordsHarmony(arrType, partName, shoppedVoices, chords)

        # Time to remove the placeholder rests we added earlier to all the measures in bbPart
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

        # Put regularized beams (and badly spelled lead notes) back in
        for part in shopped.parts:
            m21.stream.makeNotation.makeBeams(part, inPlace=True, setStemDirections=False)

        # This ends up screwing up accidental display, I'm not sure why
        # MusicEngine.putBackAnyBadlySpelledNotes(shopped)

        return shopped

    @staticmethod
    def processPillarChordsLead(
        arrType: ArrangementType,
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

        for mIdx, (mMeas, cMeas) in enumerate(
            zip(melody[m21.stream.Measure], chords[m21.stream.Measure])
        ):
            # Keep track of stuff we deepcopy into tlMeas/bbMeas (which should
            # then be skipped when populating the four voices.
            measureStuff: list[m21.base.Music21Object] = []

            # create and append the next tlMeas and bbMeas
            tlMeas = m21.stream.Measure(number=mMeas.measureNumberWithSuffix())
            tlMeas.id = 'Tenor/Lead'  # we look for this later when inserting Voices
            tlStaff.append(tlMeas)
            bbMeas = m21.stream.Measure(number=mMeas.measureNumberWithSuffix())
            bbMeas.id = 'Bari/Bass'  # we look for this later when inserting Voices
            bbStaff.append(bbMeas)

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
                .getElementsByClass([m21.key.KeySignature, m21.meter.TimeSignature])
                .getElementsByOffsetInHierarchy(0.0)
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
                if cs.chordKind == 'augmented-ninth':
                    # Finale and music21 don't know what 'augmented-ninth' is.
                    # The example I found looks like it should be augmented-dominant-ninth
                    # (i.e. with dominant 7th), not augmented-major-ninth (i.e. with
                    # major 7th).  That is to say, 1-3-#5-b7-9, not 1-3-#5-7-9.
                    # We update it in place before deepcopying, so it is updated
                    # everywhere.
                    cs.chordKind = 'augmented-dominant-ninth'
                    # fix bug in cs._updatePitches (it doesn't know about 'augmented' ninths)
                    MusicEngine._updatePitches(cs)
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
                    el = deepcopy(el.notes[-1])
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
                rOffset += rest.quarterLength

        return shopped, shoppedVoices

    @staticmethod
    def _updatePitches(cs: m21.harmony.ChordSymbol):
        def adjustOctaves(cs, pitches):
            from music21 import pitch, chord
            self = cs  # because this is an edited copy of ChordSymbol._adjustOctaves
            if not isinstance(pitches, list):
                pitches = list(pitches)

            # do this for all ninth, thirteenth, and eleventh chords...
            # this must be done to get octave spacing right
            # possibly rewrite figured bass function with this integrated?
            # ninths = ['dominant-ninth', 'major-ninth', 'minor-ninth']
            # elevenths = ['dominant-11th', 'major-11th', 'minor-11th']
            # thirteenths = ['dominant-13th', 'major-13th', 'minor-13th']

            if self.chordKind.endswith('-ninth'):
                pitches[1] = pitch.Pitch(pitches[1].name + str(pitches[1].octave + 1))
            elif self.chordKind.endswith('-11th'):
                pitches[1] = pitch.Pitch(pitches[1].name + str(pitches[1].octave + 1))
                pitches[3] = pitch.Pitch(pitches[3].name + str(pitches[3].octave + 1))

            elif self.chordKind.endswith('-13th'):
                pitches[1] = pitch.Pitch(pitches[1].name + str(pitches[1].octave + 1))
                pitches[3] = pitch.Pitch(pitches[3].name + str(pitches[3].octave + 1))
                pitches[5] = pitch.Pitch(pitches[5].name + str(pitches[5].octave + 1))
            else:
                return pitches

            c = chord.Chord(pitches)
            c = c.sortDiatonicAscending()

            return list(c.pitches)

        self = cs  # because this is a copy of ChordSymbol._updatePitches
        if 'root' not in self._overrides or 'bass' not in self._overrides or self.chordKind is None:
            return

        # create figured bass scale with root as scale
        fbScale = realizerScale.FiguredBassScale(self._overrides['root'], 'major')

        # render in the 3rd octave by default
        self._overrides['root'].octave = 3
        self._overrides['bass'].octave = 3

        if self._notationString():
            pitches = fbScale.getSamplePitches(self._overrides['root'], self._notationString())
            # remove duplicated bass note due to figured bass method.
            pitches.pop(0)
        else:
            pitches = []
            pitches.append(self._overrides['root'])
            if self._overrides['bass'] not in pitches:
                pitches.append(self._overrides['bass'])

        pitches = adjustOctaves(self, pitches)

        if self._overrides['root'].name != self._overrides['bass'].name:

            inversionNum: int | None = self.inversion()

            if not self.inversionIsValid(inversionNum):
                # there is a bass, yet no normal inversion was found: must be added note

                inversionNum = None
                # arbitrary octave, must be below root,
                # which was arbitrarily chosen as 3 above
                self._overrides['bass'].octave = 2
                pitches.append(self._overrides['bass'])
        else:
            self.inversion(None, transposeOnSet=False)
            inversionNum = None

        pitches = self._adjustPitchesForChordStepModifications(pitches)

        if inversionNum not in (0, None):
            if t.TYPE_CHECKING:
                assert inversionNum is not None
            for p in pitches[0:inversionNum]:
                p.octave = p.octave + 1
                # Repeat if 9th/11th/13th chord in 4th inversion or greater
                if inversionNum > 3:
                    p.octave = p.octave + 1

            # if after bumping up the octaves, there are still pitches below bass pitch
            # bump up their octaves
            # bassPitch = pitches[inversionNum]

            # self.bass(bassPitch)
            for p in pitches:
                if p.diatonicNoteNum < self._overrides['bass'].diatonicNoteNum:
                    p.octave = p.octave + 1

        while self._hasPitchAboveC4(pitches):
            for thisPitch in pitches:
                thisPitch.octave -= 1

        # but if this has created pitches below lowest note (the A 3 octaves below middle C)
        # on a standard piano, we're going to have to bump all the octaves back up
        while self._hasPitchBelowA1(pitches):
            for thisPitch in pitches:
                thisPitch.octave += 1

        self.pitches = tuple(pitches)
        self.sortDiatonicAscending(inPlace=True)

        # set overrides to be pitches in the harmony
        # self._overrides = {}  # JTW: was wiping legit overrides such as root=C from 'C6'
        self.bass(self.bass(), allow_add=True)
        self.root(self.root())

    @staticmethod
    def processPillarChordsHarmony(
        arrType: ArrangementType,
        partName: PartName,
        shoppedVoices: list[FourVoices],
        chords: m21.stream.Part
    ):
        currMeasure: FourVoices
        for mIdx, (currMeasure, chordMeas) in enumerate(
            zip(shoppedVoices, chords[m21.stream.Measure])
        ):
            # fillVoice is the measure-long single voice part we will be filling
            # in with harmony notes for each pillar note in the lead voice (the
            # rest of the non-pillar notes in the lead voice will just generate
            # a space in theVoice).

            # prevMeasure is the previous measure of all four voices (too look
            # at for voice-leading decisions).
            prevMeasure: FourVoices | None = None
            if mIdx > 0:
                prevMeasure = shoppedVoices[mIdx - 1]

            leadVoice: m21.stream.Voice = currMeasure[PartName.Lead]
            for el in leadVoice:
                offset: OffsetQL = el.getOffsetInHierarchy(leadVoice)

                if isinstance(el, m21.harmony.ChordSymbol):
                    continue
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
                    if partName in (PartName.Tenor, PartName.Bari):
                        rest.style.hideObjectOnPrint = True
                        rest.stepShift = 0
                    currMeasure[partName].insert(offset, rest)
                    continue

                if not isinstance(el, m21.note.Note):
                    continue

                # it's a Note
                if el.duration.isGrace:
                    continue

                # it's a non-grace Note
                leadNote: m21.note.Note = el
                chord: Chord | None = (
                    MusicEngine.findChordAtOffset(chordMeas, offset)
                )

                if chord is None or isinstance(chord.sym, m21.harmony.NoChord):
                    # Must be a melody pickup before the first chord, or a place
                    # in the music where there it is specifically notated that
                    # there is no chord at all.
                    # Put (visible) rests in the other three parts. Hide Bari
                    # (but not Tenor this time) and set rest position on the
                    # visible rests.
                    noChordRest: m21.note.Rest = m21.note.Rest()
                    noChordRest.quarterLength = leadNote.quarterLength
                    if partName == PartName.Bari:
                        noChordRest.style.hideObjectOnPrint = True
                    else:
                        noChordRest.stepShift = 0  # I wish setting to 0 did something...

                    currMeasure[partName].insert(offset, noChordRest)
                    continue

                if leadNote.pitch.name not in MusicEngine.getChordVocalParts(
                        chord, leadNote.pitch.name).values():
                    # lead is not on a pillar chord note, fill in bass/tenor/bari with
                    # spaces (invisible rests).
                    space: m21.note.Rest = m21.note.Rest()
                    space.quarterLength = leadNote.quarterLength
                    space.style.hideObjectOnPrint = True
                    currMeasure[partName].insert(offset, space)
                    continue

                # Lead has a pillar chord note.  Fill in the <partName> note
                # (potentially adjusting other non-lead notes to improve
                # voice leading).
                # Params:
                #   partName: PartName, which part we are harmonizing (might adjust others)
                #   currMeasure: FourVoices, where we insert(and adjust) the note(s))
                #   offset: OffsetQL, offset in currMeasure[x] where we are working
                #   thisFourNotes: FourNotes (read-only), for ease of looking up and down
                #   prevFourNotes: FourNotes (read-only), for ease of looking back
                thisFourNotes: FourNotes = (
                    MusicEngine.getFourNotesAtOffset(currMeasure, offset)
                )
                prevFourNotes: FourNotes = (
                    MusicEngine.getFourNotesBeforeOffset(currMeasure, prevMeasure, offset)
                )

                if partName == PartName.Bass:
                    MusicEngine.harmonizePillarChordBass(
                        arrType,
                        currMeasure,
                        offset,
                        chord,
                        thisFourNotes,
                        prevFourNotes
                    )
                elif partName == PartName.Tenor:
                    MusicEngine.harmonizePillarChordTenor(
                        arrType,
                        currMeasure,
                        offset,
                        chord,
                        thisFourNotes,
                        prevFourNotes
                    )
                elif partName == PartName.Bari:
                    MusicEngine.harmonizePillarChordBari(
                        arrType,
                        currMeasure,
                        offset,
                        chord,
                        thisFourNotes,
                        prevFourNotes
                    )
                else:
                    raise MusicEngineException(
                        'Should not reach here: partName not in Bass, Tenor, Bari'
                    )

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
    def _addBassPitchToVocalParts(
        vocalPartsInOut: dict[int, str],
        chord: Chord,
        leadPitchName: str,
        orderedRolesToReplace: tuple[int, ...]
    ):
        bassPitchName: str = chord.preferredBassPitchName
        if not bassPitchName or bassPitchName == leadPitchName:
            # lead is already on the /bass note, the bass will have to go somewhere else
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

    _DEGREES_TO_REMOVE: tuple[int, ...] = (5, 1, 7, 9, 11, 13, 3, 6, 2, 4)

    @staticmethod
    def getChordVocalParts(
        chord: Chord,
        leadPitchName: str
    ) -> dict[int, str]:
        # This is the place where we decide which of the chord pitches should end
        # up being sung. If the chord is not one we understand, return an empty dict,
        # so that the client will bail on trying to harmonize this chord (for starters,
        # because the lead note is obviously not in it).
        # We return four parts, unless the root is to be doubled, in which case we
        # return three parts.
        output: dict[int, str] = {}  # key: 0 means 'bass should get this one if possible'
        allOfThem: dict[int, str] = (
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
            MusicEngine._addBassPitchToVocalParts(output, chord, leadPitchName, tuple())
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
        # a random note to make room for it.
        MusicEngine._addBassPitchToVocalParts(
            output, chord, leadPitchName, MusicEngine._DEGREES_TO_REMOVE
        )
        if len(output) in (0, 1, 2):
            f = chord.sym.figure
            if chord.sym.chordKind not in m21.harmony.CHORD_TYPES:
                f += chord.sym.chordKindStr + ' (' + chord.sym.chordKind + ')'
            raise MusicEngineException(
                f'getChordVocalParts could not come up with enough notes: {f} -> {output}'
            )
        if len(output) not in (3, 4):
            f = chord.sym.figure
            if chord.sym.chordKind not in m21.harmony.CHORD_TYPES:
                f += chord.sym.chordKindStr + ' (' + chord.sym.chordKind + ')'
            raise MusicEngineException(
                f'getChordVocalParts came up with too many notes: {f} -> {output}'
            )
        return output

    @staticmethod
    def getChordPitchNames(
        chord: Chord
    ) -> dict[int, str]:
        # returns all of 'em, even if there are lots of notes in the chord
        output: dict[int, str] = copy(chord.roleToPitchNames)
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
        arrType: ArrangementType,
        measure: FourVoices,
        offset: OffsetQL,
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
            raise MusicEngineException('harmonizePillarChordPart: NoChord is not a pillar chord')

        partRange: VocalRange = PART_RANGES[arrType][PartName.Bass]

        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note | None = None

        # chordRole (key) is int, where 1 means root of the chord, 3 means third of the chord, etc
        chPitch: dict[int, str] = MusicEngine.getChordVocalParts(
            pillarChord,
            lead.pitch.name
        )

        preferredBass: str = ''
        if 0 in chPitch:
            preferredBass = chPitch[0]
            del chPitch[0]  # remove the /bass entry

        roleList: list[int] = list(chPitch.keys())
        roleList.sort()
        roles: tuple[int, ...] = tuple(roleList)

        root: str = ''
        fifth: str = ''

        # availablePitches is only consulted as a last resort
        availablePitches: list[str] = []
        for p in chPitch.values():
            if p == lead.pitch.name:
                continue
            availablePitches.append(p)

        # Triad: you can double the root if there's no "extra" /bass note
        if preferredBass:
            bass = MusicEngine.makeNote(preferredBass, copyFrom=lead, below=lead)
            MusicEngine.moveIntoRange(bass, partRange)
        elif roles in (
                (1, 3, 5),
                (1, 2, 5),
                (1, 4, 5),
                (1, 3, 6)):
            root = chPitch[1]
            fifth = chPitch[roles[2]]  # we treat 5 or 6 as the fifth
            other: str = chPitch[roles[1]]

            if lead.pitch.name == root:
                # Lead is on root, take doubled root an octave below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # root an octave below lead is too low, try the fifth below the lead
                    bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # still too low, just sing the same note (root) as the lead
                        bass = MusicEngine.copyNote(lead)

            elif lead.pitch.name == other:
                # Lead is on 2, 3, or 4, take root a 9th, 10th or 11th below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isTooLow(bass.pitch):
                    # Take fifth (below the lead note)
                    bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # Fine, take the root below the lead
                        bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)

            elif lead.pitch.name == fifth:
                # Lead is on fifth, take root below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # Ugh. Lead must be really low. Push the lead up a 4th to
                    # the next higher root, and take the lead note yourself.
                    bass = MusicEngine.copyNote(lead)  # fifth, assume it's in bass range
                    lead = MusicEngine.makeAndInsertNote(  # assume it's in lead range
                        root,
                        copyFrom=lead,
                        replacedNote=lead,
                        above=bass,
                        voice=measure[PartName.Lead],
                        offset=offset,
                    )

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

            if root and fifth and lead.pitch.name == root:
                # put bass on fifth below lead, or raise lead to fifth and take lead's root)
                bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    bass = MusicEngine.copyNote(lead)  # assume it's in bass range
                    lead = MusicEngine.makeAndInsertNote(  # assume it's in lead range
                        fifth,
                        copyFrom=lead,
                        replacedNote=lead,
                        above=bass,
                        voice=measure[PartName.Lead],
                        offset=offset,
                    )

            elif root and fifth and lead.pitch.name == fifth:
                # bass on root
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooHigh(bass.pitch):
                    bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead, extraOctaves=1)

            elif (root and lead.pitch.name != root) or (fifth and lead.pitch.name != fifth):
                while True:
                    # we will only iterate once, breaking out if we find a good note
                    rootBelowLead: m21.note.Note | None = None
                    fifthBelowLead: m21.note.Note | None = None
                    if root:
                        rootBelowLead = MusicEngine.makeNote(
                            root, copyFrom=lead, below=lead
                        )
                    if fifth:
                        fifthBelowLead = MusicEngine.makeNote(
                            fifth, copyFrom=lead, below=lead
                        )

                    if rootBelowLead is not None and partRange.isInRange(rootBelowLead.pitch):
                        bass = rootBelowLead
                        break

                    if fifthBelowLead is not None and partRange.isInRange(fifthBelowLead.pitch):
                        bass = fifthBelowLead
                        break

                    if rootBelowLead is not None and partRange.isTooHigh(rootBelowLead.pitch):
                        rootBelowLead = MusicEngine.makeNote(
                            root, copyFrom=lead, below=lead, extraOctaves=1
                        )
                        if partRange.isInRange(rootBelowLead.pitch):
                            bass = rootBelowLead
                            break

                    # give up on root, lets go with the fifth, positioned to be in-range,
                    # either an extra octave below the lead, or just above the lead
                    if fifth and fifthBelowLead is not None:
                        if partRange.isTooHigh(fifthBelowLead.pitch):
                            fifthBelowLead = MusicEngine.makeNote(
                                fifth, copyFrom=lead, below=lead, extraOctaves=1
                            )
                            if partRange.isInRange(fifthBelowLead.pitch):
                                bass = fifthBelowLead
                                break
                        else:
                            # must have been too low, try above the lead
                            fifthAboveLead = MusicEngine.makeNote(fifth, copyFrom=lead, above=lead)
                            if partRange.isInRange(fifthAboveLead.pitch):
                                bass = fifthAboveLead
                                break

                    raise MusicEngineException('lead not on root/fifth; couldn\'t find bass')

            else:
                # ignore root/third/fifth/seventh and just use availablePitches
                if len(availablePitches) < 3:
                    raise MusicEngineException('too few available pitches: {chPitch}')
                bass = MusicEngine.makeNote(availablePitches[0], copyFrom=lead, below=lead)
                MusicEngine.moveIntoRange(bass, partRange)
        else:
            if len(availablePitches) < 3:
                raise MusicEngineException('too few available pitches: {chPitch}')
            bass = MusicEngine.makeNote(availablePitches[0], copyFrom=lead, below=lead)
            MusicEngine.moveIntoRange(bass, partRange)

        # Specify stem directions explicitly
        bass.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Bass]

        # Put the bass note in the bass voice
        bassVoice: m21.stream.Voice = measure[PartName.Bass]
        bassVoice.insert(offset, bass)

    @staticmethod
    def harmonizePillarChordTenor(
        arrType: ArrangementType,
        measure: FourVoices,
        offset: OffsetQL,
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
            raise MusicEngineException('harmonizePillarChordPart: NoChord is not a pillar chord')

        partRange: VocalRange = PART_RANGES[arrType][PartName.Tenor]

        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note = thisFourNotes[PartName.Bass]
        tenor: m21.note.Note | None = None

        if not isinstance(bass, m21.note.Note):
            space: m21.note.Rest = m21.note.Rest()
            space.quarterLength = lead.quarterLength
            space.style.hideObjectOnPrint = True
            measure[PartName.Tenor].insert(offset, space)
            return

        availablePitchNames: list[str] = thisFourNotes.getAvailablePitchNames(pillarChord)

        if t.TYPE_CHECKING:
            assert isinstance(lead, m21.note.Note)

        # First attempt: Just go for what's available, starting with the available
        # notes above the lead (preferring closer-to-the-lead notes), then the
        # available notes below the lead (preferring closer-to-the-lead notes).
        orderedPitchNames: list[str] = MusicEngine.orderPitchNamesStartingAbove(
            availablePitchNames,
            lead.pitch.name
        )

        for p in orderedPitchNames:
            tenor = MusicEngine.makeNote(p, copyFrom=lead, above=lead)
            if partRange.isInRange(tenor.pitch):
                break

        if tenor is None or partRange.isTooLow(tenor.pitch):
            # try again, an extra octave up
            for p in orderedPitchNames:
                tenor = MusicEngine.makeNote(p, copyFrom=lead, above=lead, extraOctaves=1)
                if partRange.isInRange(tenor.pitch):
                    break

        if tenor is None or partRange.isTooHigh(tenor.pitch):
            for p in reversed(orderedPitchNames):
                tenor = MusicEngine.makeNote(p, copyFrom=lead, below=lead)
                if partRange.isInRange(tenor.pitch):
                    break

        if tenor is None or partRange.isTooHigh(tenor.pitch):
            # try again, an extra octave below
            for p in reversed(orderedPitchNames):
                tenor = MusicEngine.makeNote(p, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isInRange(tenor.pitch):
                    break

        if tenor is None or partRange.isOutOfRange(tenor.pitch):
            raise MusicEngineException('failed to find a tenor note for a pillar chord')

        # Specify stem directions explicitly
        tenor.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Tenor]

        tenorVoice: m21.stream.Voice = measure[PartName.Tenor]
        tenorVoice.insert(offset, tenor)

    @staticmethod
    def harmonizePillarChordBari(
        arrType: ArrangementType,
        measure: FourVoices,
        offset: OffsetQL,
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
            raise MusicEngineException('harmonizePillarChordPart: NoChord is not a pillar chord')

        bariPartRange: VocalRange = PART_RANGES[arrType][PartName.Bari]
        tenorPartRange: VocalRange = PART_RANGES[arrType][PartName.Tenor]

        tenor: m21.note.Note = thisFourNotes[PartName.Tenor]
        lead: m21.note.Note = thisFourNotes[PartName.Lead]
        bass: m21.note.Note = thisFourNotes[PartName.Bass]
        if not isinstance(bass, m21.note.Note):
            space: m21.note.Rest = m21.note.Rest()
            space.quarterLength = lead.quarterLength
            space.style.hideObjectOnPrint = True
            measure[PartName.Bari].insert(offset, space)
            return

        availablePitchNames: list[str] = thisFourNotes.getAvailablePitchNames(pillarChord)

        # bari gets whatever is left over (we can improve voice leading by trading notes,
        # obviously, but for now this is it).
        bari: m21.note.Note = MusicEngine.makeNote(
            availablePitchNames[0],
            copyFrom=lead,
            below=tenor
        )
        if bariPartRange.isTooHigh(bari.pitch):
            bari = MusicEngine.makeNote(
                availablePitchNames[0],
                copyFrom=lead,
                below=tenor,
                extraOctaves=1
            )

        if bari.pitch < bass.pitch:
            # bari is below the bass, that's not right.  Trade pitches with bass.
            bari = MusicEngine.copyNote(bass)
            bass = MusicEngine.makeAndInsertNote(
                availablePitchNames[0],
                copyFrom=bass,
                replacedNote=bass,
                below=bari,
                voice=measure[PartName.Bass],
                offset=offset,
            )

        if bariPartRange.isOutOfRange(bari.pitch):
            # can we trade with tenor?  (switching octaves to stay in range if necessary)
            badBari: m21.note.Note = bari
            bari = MusicEngine.copyNote(tenor)
            tenor = MusicEngine.copyNote(badBari)

            if bariPartRange.isTooHigh(bari.pitch):
                bari.pitch.octave -= 1  # type: ignore
            elif bariPartRange.isTooLow(bari.pitch):
                bari.pitch.octave += 1  # type: ignore

            if tenorPartRange.isTooLow(tenor.pitch):
                tenor.pitch.octave += 1  # type: ignore
            elif tenorPartRange.isTooHigh(tenor.pitch):
                tenor.pitch.octave -= 1  # type: ignore

        if bariPartRange.isOutOfRange(bari.pitch):
            raise MusicEngineException('failed to find a bari note for a pillar chord')
        if tenorPartRange.isOutOfRange(tenor.pitch):
            raise MusicEngineException('failed to trade for a bari note for a pillar chord')

        # Specify stem directions explicitly
        bari.stemDirection = MusicEngine.STEM_DIRECTION[PartName.Bari]

        bariVoice: m21.stream.Voice = measure[PartName.Bari]
        bariVoice.insert(offset, bari)

    @staticmethod
    def orderPitchNamesStartingAbove(pitches: list[str], baseName: str) -> list[str]:
        def semitonesAboveBaseName(pitchName: str) -> int:
            pitch = m21.pitch.Pitch(pitchName)
            basePitch = m21.pitch.Pitch(baseName)
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
                tenor = n
                break

        leadNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Lead].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in leadNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                lead = n
                break

        bariNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bari].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in bariNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                bari = n
                break

        bassNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bass].recurse()
            .getElementsByClass([m21.note.Note, m21.note.Rest])
        )
        for n in bassNotes:
            if n.duration.isGrace:
                continue
            if n.offset == offset:
                bass = n
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
        pitchName: str,
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
            output.pitch = m21.pitch.Pitch(name=pitchName, octave=below.pitch.octave)
            if output.pitch >= below.pitch:
                output.pitch.octave -= 1  # type: ignore
            if extraOctaves:
                output.pitch.octave -= extraOctaves  # type: ignore

        elif above is not None:
            output.pitch = m21.pitch.Pitch(name=pitchName, octave=above.pitch.octave)
            if output.pitch <= above.pitch:
                output.pitch.octave += 1  # type: ignore
            if extraOctaves:
                output.pitch.octave += extraOctaves  # type: ignore
        else:
            raise MusicEngineException(
                'makeNote must be passed exactly one (not neither) of above/below'
            )

        return output

    @staticmethod
    def makeAndInsertNote(
        pitchName: str,
        copyFrom: m21.note.Note,
        replacedNote: m21.note.Note | m21.note.Rest | None = None,
        below: m21.note.Note | None = None,
        above: m21.note.Note | None = None,
        extraOctaves: int = 0,
        voice: m21.stream.Voice | None = None,
        offset: OffsetQL | None = None,
    ) -> m21.note.Note:
        if voice is None or offset is None:
            raise MusicEngineException('makeAndInsertNote requires voice and offset')

        # remove the note it replaces from voice first
        if replacedNote is not None:
            if replacedNote.getOffsetInHierarchy(voice) != offset:
                raise MusicEngineException('replaced note/rest must be at offset in voice')
            voice.remove(replacedNote)

        # make the new note
        newNote: m21.note.Note = MusicEngine.makeNote(
            pitchName,
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
        for cs in stream[m21.harmony.ChordSymbol]:
            startChord: OffsetQL = cs.getOffsetInHierarchy(stream)
            endChord: OffsetQL = startChord + cs.duration.quarterLength
            if startChord <= offset < endChord:
                return Chord(cs)

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
    ) -> tuple[m21.stream.Part | None, m21.stream.Part | None]:
        # returns melodyPart, chordsPart (can be the same part).
        parts: list[m21.stream.Part] = list(score.parts)
        if not parts:
            return None, None

        # we require the first Part to be the "lead sheet" (not a PartStaff, only one
        # Voice throughout the Part), and the score must have ChordSymbols somewhere.
        # There can be other parts, but we will ignore them for now (in future, we could
        # go without chord symbols if there is a piano accompaniment, for example).
        if isinstance(parts[0], m21.stream.PartStaff):
            return None, None

        melodyPart: m21.stream.Part = parts[0]
        for meas in list(melodyPart[m21.stream.Measure]):
            voices: list[m21.stream.Voice] = list(meas[m21.stream.Voice])
            # 0 voices or 1 voice is fine (0 voices means the measure is the "voice")
            if len(voices) > 1:
                # remove the extraneous voices (assume first voice is the melody)
                for voice in voices[1:]:
                    meas.remove(voice)

        chordPart: m21.stream.Part | None = None
        for part in parts:
            numChords: int = 0
            for _cs in part[m21.harmony.ChordSymbol]:
                numChords += 1
                if numChords > 1:
                    chordPart = part
                    break

            if chordPart is not None:
                break

        if chordPart is None:
            return melodyPart, None

        return melodyPart, chordPart

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
            # (and transpose those notes up an octave so they sound/look
            # right!).
            pass
        elif arrangementType == ArrangementType.LowerVoices:
            # Fix the treble clef if it doesn't have that little 8 below it
            # (and transpose those notes down an octave so they sound/look
            # right!).
            pass
        # should we do anything for mixed arrangements?  I should look at some.
        # Straight SATB is simple, but...

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
        for measure in topStaff:
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
