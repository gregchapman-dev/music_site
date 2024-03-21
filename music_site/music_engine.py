import typing as t
import pathlib
import re
import zipfile
from enum import Enum, IntEnum, auto
from io import BytesIO
from copy import deepcopy
from collections.abc import Sequence

import music21 as m21
from music21.common.numberTools import OffsetQL

import converter21

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


class FourNoteChord(Sequence):
    def __init__(
        self,
        tenor: m21.note.Note | m21.note.Rest,
        lead: m21.note.Note | m21.note.Rest,
        bari: m21.note.Note | m21.note.Rest,
        bass: m21.note.Note | m21.note.Rest
    ):
        self._tenor: m21.note.Note | m21.note.Rest = tenor
        self._lead: m21.note.Note | m21.note.Rest = lead
        self._bari: m21.note.Note | m21.note.Rest = bari
        self._bass: m21.note.Note | m21.note.Rest = bass

    @property
    def tenor(self) -> m21.note.Note | m21.note.Rest:
        return self._tenor

    @property
    def lead(self) -> m21.note.Note | m21.note.Rest:
        return self._lead

    @property
    def bari(self) -> m21.note.Note | m21.note.Rest:
        return self._bari

    @property
    def bass(self) -> m21.note.Note | m21.note.Rest:
        return self._bass

    def __len__(self) -> int:
        return 4

    def __getitem__(self, idx: int | str | slice) -> t.Any:  # m21.note.Note | m21.note.Rest:
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

    def __getitem__(self, idx: int | slice) -> t.Any:  # m21.stream.Voice:
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

    def isInRange(self, p: m21.pitch.Pitch) -> bool:
        return self.lowest <= p <= self.highest

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
        # We take the float because we might have to do averaging and rounding.
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
    def toMusic21Score(fileData: str | bytes, fileName: str) -> m21.stream.Score:
        fmt: str = m21.common.findFormatFile(fileName)

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
        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
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
        m21.harmony.realizeChordSymbolDurations(leadSheet)

        melody: m21.stream.Part | None
        chords: m21.stream.Part | None
        melody, chords = MusicEngine.useAsLeadSheet(leadSheet)
        if melody is None or chords is None:
            raise MusicEngineException

        # Now pick a key that will work for lead voice range, and transpose.
        melodyInfo: VocalRangeInfo = VocalRangeInfo(melody)
        semitonesUp = (
            melodyInfo.getTranspositionSemitones(PartName.Lead, ArrangementType.LowerVoices)
        )
        interval: m21.interval.Interval = (
            MusicEngine.getBestTranspositionForScore(leadSheet, semitonesUp)
        )

        # Transpose the whole leadSheet score by that interval (in place).
        # Note that if we're doing a LowerVoices arrangement this will
        # probably make the leadSheet unreadable if you were to print it
        # out, since the upper staff will be an octave lower than is
        # appropriate for treble clef.  If you wanted to print it out,
        # you could switch the clef to Treble8vbClef first, to make it
        # look right.  But the arrangement code doesn't care: it will
        # put the right clefs in the output, and it knows  exactly what
        # octave the melody notes are in (without caring about the clef).
        with m21.stream.makeNotation.saveAccidentalDisplayStatus(leadSheet):
            leadSheet.transpose(interval, inPlace=True)

        # Set up the score with two Parts: Tenor/Lead and Bari/Bass
        shopped: m21.stream.Score = m21.stream.Score()
        tlStaff: m21.stream.Part = m21.stream.Part()
        shopped.insert(0, tlStaff)
        bbStaff: m21.stream.Part = m21.stream.Part()
        shopped.insert(0, bbStaff)

        # First we process the melody into the lead part (creating the entire output
        # score structure as we go, including parts, measures, and voices; inserting
        # any clefs/keysigs/timesigs in all the appropriate measures; and inserting
        # the chord symbols into the measures in the top staff).

        for mIdx, (mMeas, cMeas) in enumerate(
            zip(melody[m21.stream.Measure], chords[m21.stream.Measure])
        ):
            # Keep track of stuff we deepcopy into tlMeas/bbMeas (which should
            # then be skipped when populating the four voices.
            measureStuff: list[m21.base.Music21Object] = []

            # create and append the next tlMeas and bbMeas
            tlMeas = m21.stream.Measure(number=mMeas.measureNumberWithSuffix())
            tlStaff.append(tlMeas)
            bbMeas = m21.stream.Measure(number=mMeas.measureNumberWithSuffix())
            bbStaff.append(bbMeas)

            if mIdx == 0:
                # clef (just put in the clefs we like; hopefully the transposition
                # we did will make those reasonable clefs).  We will ignore all
                # clef changes in the rest of the melody; if there are any, we'll
                # just get lots of leger lines, I guess.
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
            tenor.id = '1'
            lead = m21.stream.Voice()
            lead.id = '2'
            tlMeas.insert(0, tenor)
            tlMeas.insert(0, lead)

            bari = m21.stream.Voice()
            bari.id = '3'
            bass = m21.stream.Voice()
            bass.id = '4'
            bbMeas.insert(0, bari)
            bbMeas.insert(0, bass)

            # Walk all the ChordSymbols in cMeas and put them in tlMeas (so
            # they will display above the top staff).
            for cs in cMeas.recurse().getElementsByClass(m21.harmony.ChordSymbol):
                measureStuff.append(cs)
                offset = cs.getOffsetInHierarchy(cMeas)
                tlMeas.insert(offset, cs)

            # Recurse all elements of mMeas, skipping any measureStuff
            # and any clefs and put them in the lead voice.
            for el in mMeas.recurse():
                if isinstance(el, m21.clef.Clef):
                    continue
                if el in measureStuff:
                    continue
                offset = el.getOffsetInHierarchy(mMeas)
                lead.insert(offset, el)

        raise Exception

        # Then we will harmonize the three harmony parts, one at a time all the way
        # through, harmonizing only the melody notes that are in the specified chord
        # (the melody pillar notes), potentially tweaking other already harmonized
        # parts as we go, to get better voice leading, as follows:

        # From "Arranging Barbershop: Volume 2" pp11-13
        #
        # Bass: Start the bass on the root or fifth for seventh chords and the root
        #           for triads. Try to follow the general shape of the melody so that
        #           bass notes are higher when the melody is higher and lower when the
        #           melody is lower.  This will help the voicings to not become too
        #           spread.  Consider bass voice leading when the harmony is a seventh
        #           chord and the melody is not on the root or fifth.
        #
        # Tenor: (Above melody) Use one of the unused notes in the chord, or double the
        #           root if it is a triad.  Consider voice leading and seek for fewer
        #           awkward leaps.  There may be times with choosing a different bass
        #           note would allow a smoother tenor part.
        #
        # Bari: Complete each chord or double the root if it is a triad.  As with the
        #           tenor, try to not have the part jump around unnecessarily and consider
        #           changing the bass or tenor note if it leads to a smoother baritone
        #           part.

        for currPart in (PartName.Bass, PartName.Tenor, PartName.Bari):
            pass

#         for mMeas, cMeas in zip(melody[m21.stream.Measure], chords[m21.stream.Measure]):
#             voices = list(mMeas.voices)
#             if voices:
#                 mVoice = voices[0]
#             else:
#                 mVoice = mMeas
#
#             tlMeas = m21.stream.Measure(num=mMeas.measureNumberWithSuffix)
#             tlStaff.append(tlMeas)
#
#             if tlClef is not None:
#                 tlMeas.insert(0, tlClef)
#                 tlClef = None
#
#             tenor = m21.stream.Voice()
#             tenor.id = '1'
#             lead = m21.stream.Voice()
#             lead.id = '2'
#             tlMeas.insert(0, tenor)
#             tlMeas.insert(0, lead)
#
#             bbMeas = m21.stream.Measure(num=mMeas.measureNumberWithSuffix)
#             bbStaff.append(bbMeas)
#
#             if bbClef is not None:
#                 bbMeas.insert(0, bbClef)
#                 bbClef = None
#
#             bari = m21.stream.Voice()
#             bari.id = '3'
#             bass = m21.stream.Voice()
#             bass.id = '4'
#             tlMeas.insert(0, bari)
#             tlMeas.insert(0, bass)
#
#             fourVoices: FourVoices = FourVoices(tenor=tenor, lead=lead, bari=bari, bass=bass)
#
#
#             for melodyNote in mVoice:
#                 if isinstance(melodyNote, m21.harmony.ChordSymbol):
#                     # melody part might be same as chords part (which we are walking separately),
#                     # so just skip over this
#                     continue
#                 if isinstance(melodyNote, m21.note.Rest):
#                     MusicEngine.appendDeepCopyTo(melodyNote, fourVoices)
#                     continue
#                 if isinstance(melodyNote, m21.chord.Chord):
#                     raise MusicEngineException(
#                         'Chord (not ChordSymbol) found in leadsheet melody'
#                     )
#
#                 if not isinstance(melodyNote, m21.note.Note):
#                     continue
#
#                 # it's a Note
#                 offset = melodyNote.getOffsetInHierarchy(mMeas)
#                 dur = melodyNote.duration.quarterLength
#                 chordSym: m21.harmony.ChordSymbol | None = (
#                     MusicEngine.findChordSymbolOverlappingOffset(cMeas, offset)
#                 )
#                 # Figure out a voicing.
#                 tenorLeadBariBass: FourNoteChord = (
#                     MusicEngine.computeShoppedPillarChord(melodyNote, chordSym)
#                 )
#
#                 MusicEngine.appendShoppedChord(tenorLeadBariBass, fourVoices)

        return shopped

    @staticmethod
    def computeShoppedPillarChord(
        melodyNote: m21.note.Note,
        chordSym: m21.harmony.ChordSymbol | None
    ) -> FourNoteChord:
        # returns four notes: tenor, lead, bari, bass
        tenor: m21.note.Note | m21.note.Rest
        lead: m21.note.Note
        bari: m21.note.Note | m21.note.Rest
        bass: m21.note.Note | m21.note.Rest

        lead = deepcopy(melodyNote)

        if chordSym is None or isinstance(chordSym, m21.harmony.NoChord):
            # Must be a melody pickup before the first chord, or a place
            # in the music where there is specifically no chord at all.
            # Put rests in the other three parts.
            tenor = m21.note.Rest()
            tenor.duration.quarterLength = melodyNote.duration.quarterLength
            bari = deepcopy(tenor)
            bass = deepcopy(tenor)
            return FourNoteChord(tenor=tenor, lead=lead, bari=bari, bass=bass)

        # for now, only support root, third, fifth, seventh(if present)
        root: m21.pitch.Pitch = chordSym.root()
        third: m21.pitch.Pitch = chordSym.third
        fifth: m21.pitch.Pitch = chordSym.fifth
        seventh: m21.pitch.Pitch | None = chordSym.seventh

        if seventh is None:
            # Basic triad, double the root
            if lead.pitch.name == root.name:
                # is lead on low root or high root?
                if lead.pitch > m21.pitch.Pitch('F3'):
                    # lead is on high-enough root that bass can be an octave below
                    bass = MusicEngine.makeNote(root.name, below=lead)
                else:
                    # lead is on too-low a root for bass an octave below.
                    # bass gets exactly the same note as lead
                    bass = deepcopy(lead)

                # tenor on third, above the lead
                tenor = MusicEngine.makeNote(third.name, above=lead)

                # bari on fifth, below the lead
                bari = MusicEngine.makeNote(fifth.name, below=lead)

            elif lead.pitch.name == fifth.name:
                # lead on fifth: bass gets root below lead
                # bari gets root above the lead, tenor gets third above bari
                bass = MusicEngine.makeNote(root.name, below=lead)
                bari = MusicEngine.makeNote(root.name, above=lead)
                tenor = MusicEngine.makeNote(third.name, above=bari)

            elif lead.pitch.name == third.name:
                # lead on third: Choices are tenor on fifth above lead, tenor on root below lead,
                # tenor on root above lead.
                # if lead is high, bass gets root (a 10th below lead), tenor gets root below lead,
                # bari gets fifth below lead
                # if lead is low, bass gets root (a 3rd below lead) tenor gets root above lead,
                # bari gets fifth above lead

                # Example code here for high lead (showing off extraOctaves usage)
                bass = MusicEngine.makeNote(root.name, below=lead, extraOctaves=1)
                tenor = MusicEngine.makeNote(root.name, below=lead)
                bari = MusicEngine.makeNote(fifth.name, above=lead)

            else:
                # lead is not on a pillar chord note, fill in bass/tenor/bari with spaces
                bass = m21.note.Rest(lead.duration.quarterLength)
                bass.style.hideObjectOnPrint = True
                tenor = deepcopy(bass)
                bari = deepcopy(bass)

        # Specify stem directions explicitly
        if isinstance(tenor, m21.note.Note):
            tenor.stemDirection = 'up'
        lead.stemDirection = 'down'
        if isinstance(bari, m21.note.Note):
            bari.stemDirection = 'up'
        if isinstance(bass, m21.note.Note):
            bass.stemDirection = 'down'

        return FourNoteChord(tenor=tenor, lead=lead, bari=bari, bass=bass)

    @staticmethod
    def makeNote(
        pitchName: str,
        below: m21.note.Note | None = None,
        above: m21.note.Note | None = None,
        extraOctaves: int = 0
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
            output = m21.note.Note(pitchName, octave=below.pitch.octave)  # type: ignore
            if output.pitch > below.pitch:
                output.pitch.octave -= 1  # type: ignore
            if extraOctaves:
                output.pitch.octave -= extraOctaves  # type: ignore
        elif above is not None:
            output = m21.note.Note(pitchName, octave=above.pitch.octave)  # type: ignore
            if output.pitch < above.pitch:
                output.pitch.octave += 1  # type: ignore
            if extraOctaves:
                output.pitch.octave += extraOctaves  # type: ignore
        else:
            raise MusicEngineException(
                'makeNote must be passed exactly one (not neither) of above/below'
            )

        return output

    @staticmethod
    def appendShoppedChord(
        fourNotes: FourNoteChord,
        fourVoices: FourVoices
    ):
        for note, voice in zip(fourNotes, fourVoices):
            voice.append(note)

    @staticmethod
    def appendDeepCopyTo(gn: m21.note.GeneralNote, fourVoices: FourVoices):
        for s in fourVoices:
            s.append(deepcopy(gn))

    @staticmethod
    def findChordSymbolOverlappingOffset(
        measure: m21.stream.Measure,
        offset: OffsetQL
    ) -> m21.harmony.ChordSymbol | None:
        for cs in measure[m21.harmony.ChordSymbol]:
            startChord: OffsetQL = cs.getOffsetInHierarchy(measure)
            endChord: OffsetQL = startChord + cs.duration.quarterLength
            if startChord <= offset < endChord:
                return cs

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
        for meas in melodyPart[m21.stream.Measure]:
            voices: list[m21.stream.Voice] =  list(meas[m21.stream.Voice])
            # 0 voices or 1 voice is fine (0 voices means the measure is the "voice")
            if len(voices) > 1:
                return None, None

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
