import typing as t
import pathlib
import re
import zipfile
from enum import Enum, IntEnum, auto
from io import BytesIO
from copy import copy, deepcopy
from collections.abc import Sequence

import music21 as m21
from music21.common.numberTools import OffsetQL
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
        self.sym: m21.harmony.ChordSymbol = cs
        self.pitches: tuple[m21.pitch.Pitch, ...] = cs.pitches

        # tuple[role=1..13, pitch]
        self.roleToPitchNames: dict[int, str] = {}

        if isinstance(self.sym, m21.harmony.NoChord):
            return

        # cs.pitches have octaves, and are ordered diatonically, so they will
        # help us figure out the difference between role=4 and role=11 (cs.getChordStep
        # won't help us at all).
        pitchNames: list[str] = [p.name for p in cs.pitches]
        pitchesForRole: list[m21.pitch.Pitch | None] = [None] * 14
        for i in range(1, 8):
            try:
                pitchesForRole[i] = cs.getChordStep(i)
            except m21.chord.ChordException:
                pass

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
                # shouldn't happen
                raise MusicEngineException('cannot figure out pitch roles in chord')
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
        if idx == 0 or idx == PartName.Tenor:
            return self.tenor
        if idx == 1 or idx == PartName.Lead:
            return self.lead
        if idx == 2 or idx == PartName.Bari:
            return self.bari
        if idx == 3 or idx == PartName.Bass:
            return self.bass

        # we don't support slicing (or out-of-range idx)
        raise IndexError(idx)

    def getAvailablePitchNames(self, chord: Chord) -> list[str]:
        availableRoleToPitchNames: dict[int, str] = (
            MusicEngine.getChordVocalParts(chord, self[PartName.Lead].name)
        )
        doubleTheRoot: bool = False
        if len(availableRoleToPitchNames) == 3:
            doubleTheRoot = True

        for n in self:
            if isinstance(n, m21.note.Note):
                if n.pitch.name in availableRoleToPitchNames.values():
                    if doubleTheRoot and n.pitch.name == availableRoleToPitchNames.get(1, None):
                        # don't remove the root until you see the root a second time
                        doubleTheRoot = False
                        continue

                    removeRole: int = 0  # there is no role 0
                    for k, v in availableRoleToPitchNames.items():
                        if v == n.pitch.name:
                            removeRole = k
                    if removeRole != 0:
                        availableRoleToPitchNames.pop(removeRole, None)
                else:
                    print('n.pitch.name not in fourParts, why did we use it then?')

        return list(availableRoleToPitchNames.values())


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
        if idx == 0 or idx == PartName.Tenor:
            return self.tenor
        if idx == 1 or idx == PartName.Lead:
            return self.lead
        if idx == 2 or idx == PartName.Bari:
            return self.bari
        if idx == 3 or idx == PartName.Bass:
            return self.bass

        # we don't support slicing (or out-of-range idx)
        raise IndexError()


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
        PartName.Tenor: VocalRange(m21.pitch.Pitch('G4'), m21.pitch.Pitch('F5')),
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
                self.fullRange = VocalRange(n.pitch, n.pitch)
                continue

            if t.TYPE_CHECKING:
                assert isinstance(self.fullRange, VocalRange)

            self.fullRange.lowest = min(self.fullRange.lowest, n.pitch)
            self.fullRange.highest = max(self.fullRange.highest, n.pitch)

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
                        raise Exception
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
    def getBestTranspositionForScore(
        score: m21.stream.Score,
        semitonesUp: int
    ) -> m21.interval.Interval:
        # if semitonesUp is more than an octave, trim it, but remember how many octaves
        # you trimmed.
        # And this is the horrible thing you have to do to get integer
        # truncation toward zero.
        octavesUp: int = -(-semitonesUp // 12) if semitonesUp < 0 else semitonesUp // 12
        semitonesUp = semitonesUp - (octavesUp * 12)

        # We need to transpose the key, and pick the right enharmonic
        # key that has <= 7 sharps or flats, or we'll end up in the
        # key of G# major and have 8 sharps (or worse).
        keySigs: list[m21.key.KeySignature] = list(
            score.recurse()
                .getElementsByClass(m21.key.KeySignature)
                .getElementsByOffsetInHierarchy(0.0)
        )

        majorKey: str = 'C'
        if keySigs:
            majorKey = MusicEngine._SHARPS_TO_MAJOR_KEYS[keySigs[0].sharps]

        keyPitch = m21.pitch.Pitch(majorKey)
        chromatic = m21.interval.ChromaticInterval(semitonesUp)
        newKeyPitch: m21.pitch.Pitch = chromatic.transposePitch(keyPitch)

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

        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
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

        raise Exception('Unexpected failure to find a reasonable key to transpose into')

    @staticmethod
    def transposeInPlace(score: m21.stream.Score, semitones: int):
        interval: m21.interval.Interval = (
            MusicEngine.getBestTranspositionForScore(score, semitones)
        )
        with m21.stream.makeNotation.saveAccidentalDisplayStatus(score):
            score.transpose(interval, inPlace=True)

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

            # last/thisChordMeas might be Voices; if so I hope they are both
            # at offset 0 in their respective Measures.
            lastChordMeas: m21.stream.Stream = piece.containerInHierarchy(
                lastChord, setActiveSite=False)
            lastChordOffsetInMeas = lastChord.getOffsetInHierarchy(lastChordMeas)
            thisChordMeas: m21.stream.Stream = piece.containerInHierarchy(
                cs, setActiveSite=False)
            thisChordOffsetInMeas = cs.getOffsetInHierarchy(thisChordMeas)

            qlDiff = pf.elementOffset(cs) - pf.elementOffset(lastChord)
            if lastChordMeas is thisChordMeas:
                lastChord.duration.quarterLength = qlDiff
            else:
                # split qlDiff into two parts:
                # 1. the available room in lastChordMeas, and
                # 2. the remainder (which will land in thisChordMeas)
                qlDiff1: OffsetQL = qlDiff - thisChordOffsetInMeas
                qlDiff2: OffsetQL = qlDiff - qlDiff1
                lastChord.duration.quarterLength = qlDiff1
                if qlDiff2 != 0:
                    lastChord2: m21.harmony.ChordSymbol = deepcopy(lastChord)
                    lastChord2.duration.quarterLength = qlDiff2
                    thisChordMeas.insert(0, lastChord2)
            lastChord = cs

        # on exit from the loop, all but lastChord has been handled
        qlDiff = pf.highestTime - pf.elementOffset(lastChord)
        if lastChordMeas is thisChordMeas:
            lastChord.duration.quarterLength = qlDiff
        else:
            # split qlDiff into two parts:
            # 1. the available room in lastChordMeas, and
            # 2. the remainder (which will land in thisChordMeas)
            lastChordMeas = piece.containerInHierarchy(lastChord, setActiveSite=False)
            qlDiff1 = qlDiff - lastChord.getOffsetInHierarchy(lastChordMeas)
            qlDiff2 = qlDiff - qlDiff1
            lastChord.duration.quarterLength = qlDiff1
            if qlDiff2 != 0:
                lastChord2: m21.harmony.ChordSymbol = deepcopy(lastChord)
                lastChord2.duration.quarterLength = qlDiff2
                thisChordMeas.insert(0, lastChord2)

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

        # We call realizeChordSymbolDurations() because otherwise ChordSymbols have
        # duration == 0 or 1, which doesn't help us find the ChordSymbol that has a
        # time range that contains a particular offset.
        MusicEngine.realizeChordSymbolDurations(leadSheet)

        # more fixups to the leadsheet score
        M21Utilities.fixupBadChordKinds(leadSheet, inPlace=True)
        M21Utilities.fixupBadBeams(leadSheet, inPlace=True)

        melody: m21.stream.Part | None
        chords: m21.stream.Part | None
        melody, chords = MusicEngine.useAsLeadSheet(leadSheet)
        if melody is None or chords is None:
            raise MusicEngineException('not a useable leadsheet (no melody, or no chords)')

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


        shopped: m21.stream.Score
        shoppedVoices: list[FourVoices]
        shopped, shoppedVoices = MusicEngine.processPillarChordsLead(arrType, melody, chords)

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

        return shopped

    @staticmethod
    def processPillarChordsLead(
        arrType: ArrangementType,
        melody: m21.stream.Part,
        chords: m21.stream.Part
    ) -> tuple[m21.stream.Score, list[FourVoices]]:
        # initial empty shoppedVoices and shopped (Score)
        shoppedVoices: list[FourVoices] = []
        shopped: m21.stream.Score = m21.stream.Score()

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
                    # The example I found looks like it should be augmented-dominant-night
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
        scaleInitTuple = (self._overrides['root'].name, 'major')
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
                    if partName == PartName.Tenor or partName == PartName.Bari:
                        rest.style.hideObjectOnPrint = True
                        rest.stepShift = 0
                    currMeasure[partName].insert(offset, rest)
                    continue

                if not isinstance(el, m21.note.Note):
                    continue

                # it's a Note
                leadNote: m21.note.Note = el
                ql: OffsetQL = leadNote.quarterLength
                chord: Chord | None = (
                    MusicEngine.findChordOverlappingOffset(chordMeas, offset)
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
                overrideStatus=True,         # because we did a deepcopy of lead note which
                                             # already had displayStatus set
                tiePitchSet=tiedPitchNames,  # tied across barline needs no repeated accidental
                inPlace=True
            )

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
        output: dict[int, str] = {}
        allOfThem: dict[int, str] = (
            MusicEngine.getChordPitchNames(chord)
        )

        # Catch the weird cases first (we have to pick which note(s) to drop)
        if tuple(allOfThem.keys()) == (1, 3, 5, 7, 9, 11, 13):
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
            return output

        if tuple(allOfThem.keys()) == (1, 3, 5, 7, 9, 11):
            # 11th chord of some sort.
            # Vol 2 Figure 14.18 likes 5/7/9/11.
            # (but if lead is on 1 or 3, we could return lead/7/9/11 someday)
            output[5] = allOfThem[5]
            output[7] = allOfThem[7]
            output[9] = allOfThem[9]
            output[11] = allOfThem[11]
            return output

        if tuple(allOfThem.keys()) == (1, 3, 5, 7, 9):
            # 9th chord
            # Vol 2 Figure 14.30 likes 3, 5, 7, 9.
            # (but if lead is on 1, we could return lead/5/7/9 someday)
            output[3] = allOfThem[3]
            output[5] = allOfThem[5]
            output[7] = allOfThem[7]
            output[9] = allOfThem[9]

        if tuple(allOfThem.keys()) == (1, 3, 5, 6):
            # 6th chord.
            # We like to drop the fifth (and double the root), but if the fifth is
            # in the lead, we keep the fifth instead. Note that this routine is not
            # responsible for doubling the root, so the only choice here is whether
            # or not to include the fifth.
            if leadPitchName == allOfThem[5]:
                output[5] = allOfThem[5]

            output[1] = allOfThem[1]
            output[3] = allOfThem[3]
            output[6] = allOfThem[6]
            return output

        if tuple(allOfThem.keys()) == (1, 3, 5, 7):
            # 7th Chord of some sort.
            output[1] = allOfThem[1]
            output[3] = allOfThem[3]
            output[5] = allOfThem[5]
            output[7] = allOfThem[7]
            return output

        if tuple(allOfThem.keys()) == (1, 3, 5):
            # Triad of some sort.
            output[1] = allOfThem[1]
            output[3] = allOfThem[3]
            output[5] = allOfThem[5]
            return output

        return output

    @staticmethod
    def getChordPitchNames(
        chord: Chord
    ) -> dict[int, str]:
        # returns all of 'em, even if there are lots of notes in the chord
        output: dict[int, str] = copy(chord.roleToPitchNames)
        return output

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

        roleList: list[int] = list(chPitch.keys())
        roleList.sort()
        roles: tuple[int, ...] = tuple(roleList)

        if roles == (1, 3, 5):
            # Triad: you can double the root
            root: str = chPitch[1]
            third: str = chPitch[3]
            fifth: str = chPitch[5]

            if lead.pitch.name == root:
                # Lead is on root, take doubled root an octave below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # octave below is too low, try fifth below
                    bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # still too low, just sing the same note (root) as the lead
                        bass = deepcopy(lead)

            elif lead.pitch.name == third:
                # Lead is on third, take root a 10th below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isTooLow(bass.pitch):
                    # Take fifth (below the lead's third)
                    bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # Fine, take the root just below the lead's third
                        bass = MusicEngine.makeNote(third, copyFrom=lead, below=lead)

            elif lead.pitch.name == fifth:
                # Lead is on fifth, take root below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # Ugh. Lead must be really low. Push the lead up a 4th to
                    # the next higher root, and take the lead note yourself.
                    bass = deepcopy(lead)  # fifth, assume it's in bass range
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

        elif (roles == (1, 3, 5, 7)
                or roles == (1, 3, 5, 6)
                or roles == (3, 5, 7, 9)
                or roles == (5, 7, 9, 11)
                or roles == (7, 9, 11, 13)):
            if roles == (1, 3, 5, 7):
                # 7th chord: no doubling the root.
                root = chPitch[1]
                third = chPitch[3]
                fifth = chPitch[5]
                seventh: str = chPitch[7]
            elif roles == (1, 3, 5, 6):
                # 6th with fifth: treat it as a 7th chord rooted at 6
                root = chPitch[6]
                third = chPitch[1]
                fifth = chPitch[3]
                seventh = chPitch[5]
            elif roles == (3, 5, 7, 9):
                # 9th chord with no root: Treat it as a 7th chord rooted at 3
                root = chPitch[3]
                third = chPitch[5]
                fifth = chPitch[7]
                seventh = chPitch[9]
            elif roles == (5, 7, 9, 11):
                # 11th chord with no root/third: Treat it as a 7th chord rooted at 5
                root = chPitch[5]
                third = chPitch[7]
                fifth = chPitch[9]
                seventh = chPitch[11]
            else:
                # 13th chord with no root/third/fifth: Treat it as a 7th chord rooted at 7
                root = chPitch[7]
                third = chPitch[9]
                fifth = chPitch[11]
                seventh = chPitch[13]

            if lead.pitch.name == root:
                # put bass on fifth below lead, or raise lead to fifth and take lead's root)
                bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    bass = deepcopy(lead)  # assume it's in bass range
                    lead = MusicEngine.makeAndInsertNote(  # assume it's in lead range
                        fifth,
                        copyFrom=lead,
                        replacedNote=lead,
                        above=bass,
                        voice=measure[PartName.Lead],
                        offset=offset,
                    )

            elif lead.pitch.name == third:
                bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)

            elif lead.pitch.name == fifth:
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)  # assume in bass range

            elif lead.pitch.name == seventh:
                # put bass on fifth, a 3rd below the lead (or a 10th below if that's too high)
                bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead)
                if partRange.isTooHigh(bass.pitch):
                    bass = MusicEngine.makeNote(fifth, copyFrom=lead, below=lead, extraOctaves=1)

            else:
                # Should never happen, because we wouldn't call this routine if
                # the lead wasn't on a chord note.
                raise MusicEngineException(
                    'harmonizePillarChordBass: lead note not in pillar chord'
                )

        elif roles == (1, 3, 6):
            # 6th chord (without the fifth; double the root)
            # For now, this is a copy of (1, 3, 5), with 6 taking 5's role.
            # If we need to tweak it, we can.
            root = chPitch[1]
            third = chPitch[3]
            sixth: str = chPitch[6]

            if lead.pitch.name == root:
                # Lead is on root, take doubled root an octave below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # octave below is too low, try sixth below
                    bass = MusicEngine.makeNote(sixth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # still too low, just sing the same note (root) as the lead
                        bass = deepcopy(lead)

            elif lead.pitch.name == third:
                # Lead is on third, take root a 10th below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead, extraOctaves=1)
                if partRange.isTooLow(bass.pitch):
                    # Take sixth (below the lead's third)
                    bass = MusicEngine.makeNote(sixth, copyFrom=lead, below=lead)
                    if partRange.isTooLow(bass.pitch):
                        # Fine, take the root just below the lead's third
                        bass = MusicEngine.makeNote(third, copyFrom=lead, below=lead)

            elif lead.pitch.name == sixth:
                # Lead is on sixth, take root below
                bass = MusicEngine.makeNote(root, copyFrom=lead, below=lead)
                if partRange.isTooLow(bass.pitch):
                    # Ugh. Lead must be really low. Push the lead up a 4th to
                    # the next higher root, and take the lead note yourself.
                    bass = deepcopy(lead)  # sixth, assume it's in bass range
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

        elif roles == (1, 5, 7, 9):
            # 9th chord with root
            return
            raise MusicEngineException(
                f'Don\'t know how to harmonize this chord: {roles}'
            )

        elif roles == (1, 7, 9, 11):
            # 11th chord with root in lead
            return
            raise MusicEngineException(
                f'Don\'t know how to harmonize this chord: {roles}'
            )

        elif roles == (3, 7, 9, 11):
            # 11th chord with third in lead
            return
            raise MusicEngineException(
                f'Don\'t know how to harmonize this chord: {roles}'
            )

        elif (roles == (1, 9, 11, 13)
                or roles == (3, 9, 11, 13)
                or roles == (5, 9, 11, 13)):
            # 13th chord with 1, 3, or 5 in lead
            return
            raise MusicEngineException(
                f'Don\'t know how to harmonize this chord: {roles}'
            )

        else:
            raise MusicEngineException(
                f'Don\'t know how to harmonize this chord: {roles}'
            )


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
        tenor: m21.note.Note | None = None

        availablePitchNames: list[str] = thisFourNotes.getAvailablePitchNames(pillarChord)

        if t.TYPE_CHECKING:
            assert(isinstance(lead, m21.note.Note))

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

        availablePitchNames: list[str] = thisFourNotes.getAvailablePitchNames(pillarChord)

        # bari gets whatever is left over (we can improve voice leading by trading notes,
        # obviously, but for now this is it).
        bari: m21.note.Note = MusicEngine.makeNote(availablePitchNames[0], copyFrom=lead, below=tenor)
        if bariPartRange.isTooHigh(bari.pitch):
            bari = MusicEngine.makeNote(availablePitchNames[0], copyFrom=lead, below=tenor, extraOctaves=1)

        if bari.pitch < bass.pitch:
            # bari is below the bass, that's not right.  Trade pitches with bass.
            bari = deepcopy(bass)
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
            bari, tenor = tenor, bari
            if bariPartRange.isTooHigh(bari.pitch):
                bari.pitch.octave -= 1
            if tenorPartRange.isTooLow(tenor.pitch):
                tenor.pitch.octave += 1

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
                .getElementsByOffsetInHierarchy(offset)
        )
        if tenorNotes:
            tenor = tenorNotes[0]

        leadNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Lead].recurse()
                .getElementsByClass([m21.note.Note, m21.note.Rest])
                .getElementsByOffsetInHierarchy(offset)
        )
        if leadNotes:
            lead = leadNotes[0]

        bariNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bari].recurse()
                .getElementsByClass([m21.note.Note, m21.note.Rest])
                .getElementsByOffsetInHierarchy(offset)
        )
        if bariNotes:
            bari = bariNotes[0]

        bassNotes: list[m21.note.Note | m21.note.Rest] = list(
            measure[PartName.Bass].recurse()
                .getElementsByClass([m21.note.Note, m21.note.Rest])
                .getElementsByOffsetInHierarchy(offset)
        )
        if bassNotes:
            bass = bassNotes[0]

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

        output: m21.note.Note
        octave: int | None
        if below is not None:
            output = deepcopy(copyFrom)
            output.lyrics = []  # don't copy the lyrics!

            output.pitch = m21.pitch.Pitch(name=pitchName, octave=below.pitch.octave)
            if output.pitch >= below.pitch:
                output.pitch.octave -= 1  # type: ignore
            if extraOctaves:
                output.pitch.octave -= extraOctaves  # type: ignore

        elif above is not None:
            output = deepcopy(copyFrom)
            output.lyrics = []  # don't copy the lyrics!

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
    def findChordOverlappingOffset(
        measure: m21.stream.Measure,
        offset: OffsetQL
    ) -> Chord | None:
        for cs in measure[m21.harmony.ChordSymbol]:
            startChord: OffsetQL = cs.getOffsetInHierarchy(measure)
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
            voices: list[m21.stream.Voice] =  list(meas[m21.stream.Voice])
            # 0 voices or 1 voice is fine (0 voices means the measure is the "voice")
            if len(voices) > 1:
                # remove the extraneous voices (assume first voice is the melody)
                for voice in voices[1:]:
                    meas.remove(voice)

        chordPart: m21.stream.Part | None = None
        for part in parts:
            for chordSymbol in part[m21.harmony.ChordSymbol]:
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
