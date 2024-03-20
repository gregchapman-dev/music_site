import typing as t
import pathlib
import re
import zipfile
from enum import IntEnum, auto
from io import BytesIO
from copy import deepcopy
from collections.abc import Sequence

import music21 as m21
from music21.common.numberTools import OffsetQL

import converter21

# Register the Humdrum and MEI readers/writers from converter21
converter21.register()

# from flaskr.db import get_db

class MusicEngineException(Exception):
    pass

class ArrangementType (IntEnum):
    UpperVoices = auto()
    MixedVoices = auto()
    LowerVoices = auto()

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
        if idx == 0 or idx == 'tenor':
            return self.tenor
        if idx == 1 or idx == 'lead':
            return self.lead
        if idx == 2 or idx == 'bari':
            return self.bari
        if idx == 3 or idx == 'bass':
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
        if idx == 0 or idx == 'tenor':
            return self.tenor
        if idx == 1 or idx == 'lead':
            return self.lead
        if idx == 2 or idx == 'bari':
            return self.bari
        if idx == 3 or idx == 'bass':
            return self.bass

        # we don't support slicing (or out-of-range idx)
        raise IndexError()

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
    def transposeInPlace(score: m21.stream.Score, semitones: int):
        # We need to transpose the key in our heads, and pick the right
        # enharmonic key that has <= 7 sharps or flats, or we'll end up
        # in the key of G# major and have 8 sharps.
        keySigs: list[m21.key.KeySignature] = list(
            score.recurse()
                .getElementsByClass(m21.key.KeySignature)
                .getElementsByOffsetInHierarchy(0.0)
        )

        majorKey: str = 'C'
        if keySigs:
            majorKey = MusicEngine._SHARPS_TO_MAJOR_KEYS[keySigs[0].sharps]

        keyPitch = m21.pitch.Pitch(majorKey)
        chromatic = m21.interval.ChromaticInterval(semitones)
        newKeyPitch: m21.pitch.Pitch = chromatic.transposePitch(keyPitch)
        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
            # put octaves on them now, and then check it
            keyPitch.octave = 4
            newKeyPitch.octave = 4
            if (newKeyPitch < keyPitch) != (semitones < 0):
                # We need to adjust newKeyPitch's octave now,
                # so we transpose in the right direction.
                if semitones < 0:
                    # we should be transposing down, not up
                    newKeyPitch.octave -= 1
                else:
                    # we should be transposing up, not down
                    newKeyPitch.octave += 1

            interval = m21.interval.Interval(keyPitch, newKeyPitch)
            with m21.stream.makeNotation.saveAccidentalDisplayStatus(score):
                score.transpose(interval, inPlace=True)
            return

        newKeyPitch.getEnharmonic(inPlace=True)
        if newKeyPitch.name in MusicEngine._SHARPS_TO_MAJOR_KEYS.values():
            interval = m21.interval.Interval(keyPitch, newKeyPitch)
            with m21.stream.makeNotation.saveAccidentalDisplayStatus(score):
                score.transpose(interval, inPlace=True)
            return

        raise Exception('Unexpected failure to find a reasonable key to transpose into')

    @staticmethod
    def shopPillarMelodyNotesFromLeadSheet(
        leadSheet: m21.stream.Score,
    ) -> m21.stream.Score:
        # never in place, always creates a new score from scratch
        # raises MusicEngineException if it can't do the job.
        melody: m21.stream.Part | None
        chords: m21.stream.Part | None
        melody, chords = MusicEngine.useAsLeadSheet(leadSheet)
        if melody is None or chords is None:
            raise MusicEngineException

        # We call realizeChordSymbolDurations() because otherwise ChordSymbols have
        # duration == 0, which doesn't help us find the ChordSymbol time range that
        # overlaps an offset.
        m21.harmony.realizeChordSymbolDurations(leadSheet)

        shopped: m21.stream.Score = m21.stream.Score()

        # set up two Parts (Treblev8 and Bass)
        tlStaff: m21.stream.Part = m21.stream.Part()
        shopped.insert(0, tlStaff)
        tlClef: m21.clef.Clef | None = m21.clef.Treble8vbClef()

        bbStaff: m21.stream.Part = m21.stream.Part()
        shopped.insert(0, bbStaff)
        bbClef: m21.clef.Clef | None = m21.clef.BassClef()

        for mMeas, cMeas in zip(melody[m21.stream.Measure], chords[m21.stream.Measure]):
            voices = list(mMeas.voices)
            if voices:
                mVoice = voices[0]
            else:
                mVoice = mMeas

            tlMeas = m21.stream.Measure(num=mMeas.measureNumberWithSuffix)
            tlStaff.append(tlMeas)

            if tlClef is not None:
                tlMeas.insert(0, tlClef)
                tlClef = None

            tenor = m21.stream.Voice()
            tenor.id = '1'
            lead = m21.stream.Voice()
            lead.id = '2'
            tlMeas.insert(0, tenor)
            tlMeas.insert(0, lead)

            bbMeas = m21.stream.Measure(num=mMeas.measureNumberWithSuffix)
            bbStaff.append(bbMeas)

            if bbClef is not None:
                bbMeas.insert(0, bbClef)
                bbClef = None

            bari = m21.stream.Voice()
            bari.id = '3'
            bass = m21.stream.Voice()
            bass.id = '4'
            tlMeas.insert(0, bari)
            tlMeas.insert(0, bass)

            fourVoices: FourVoices = FourVoices(tenor=tenor, lead=lead, bari=bari, bass=bass)


            for melodyNote in mVoice:
                if isinstance(melodyNote, m21.harmony.ChordSymbol):
                    # melody part might be same as chords part (which we are walking separately),
                    # so just skip over this
                    continue
                if isinstance(melodyNote, m21.note.Rest):
                    MusicEngine.appendDeepCopyTo(melodyNote, fourVoices)
                    continue
                if isinstance(melodyNote, m21.chord.Chord):
                    raise MusicEngineException(
                        'Chord (not ChordSymbol) found in leadsheet melody'
                    )

                if not isinstance(melodyNote, m21.note.Note):
                    continue

                # it's a Note
                offset = melodyNote.getOffsetInHierarchy(mMeas)
                dur = melodyNote.duration.quarterLength
                chordSym: m21.harmony.ChordSymbol | None = (
                    MusicEngine.findChordSymbolOverlappingOffset(cMeas, offset)
                )
                # Figure out a voicing.
                tenorLeadBariBass: FourNoteChord = (
                    MusicEngine.computeShoppedPillarChord(melodyNote, chordSym)
                )

                MusicEngine.appendShoppedChord(tenorLeadBariBass, fourVoices)

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
                if MusicEngine.pitchInRange(lead.pitch, 'F3', ''):
                    # lead is on high-enough root that bass can be an octave below
                    bass = m21.note.Note(root.name, octave=lead.pitch.octave - 1)
                else:
                    # lead is on too-low a root for bass an octave below.
                    # bass gets root in same octave as lead
                    bass = m21.note.Note(root.name, octave=lead.pitch.octave)

                # tenor on third, above the lead
                tenor = m21.note.Note(third.name, octave=lead.pitch.octave)
                if tenor.pitch < lead.pitch:
                    tenor.pitch.octave += 1

                # bari on fifth, below the lead
                bari = m21.note.Note(fifth.name, octave=lead.pitch.octave)
                if bari.pitch > lead.pitch:
                    bari.pitch.octave -= 1

            elif lead.pitch.name == fifth.name:
                # lead on fifth: bass gets root below lead
                # bari gets root an octave above the bass, tenor gets third above bari
                bass = m21.note.Note(root.name, octave=lead.pitch.octave)
                if bass.pitch > lead.pitch:
                    bass.pitch.octave -= 1
                bari = m21.note.Note(root.name, octave=bass.pitch.octave + 1)
                tenor = m21.note.Note(third.name, octave=bari.pitch.octave)
                if tenor.pitch < bari.pitch:
                    tenor.pitch.octave += 1

            elif lead.pitch.name == third.name:
                # lead on third: Choices are tenor on fifth above lead, tenor on root below lead,
                # tenor on root above lead.
                # if lead is high, bass gets root (a 10th below lead), tenor gets root below lead,
                # bari gets fifth below lead
                # if lead is low, bass gets root (a 3rd below lead) tenor gets root above lead,
                # bari gets fifth above lead
                bass = m21.note.Note(root.name)
                tenor = m21.note.Note(root.name)
                bari = m21.note.Note(fifth.name)
            else:
                # lead is not on a pillar chord note, fill in bass/tenor/bari with spaces
                bass = m21.note.Rest(lead.duration.quarterLength)
                bass.style.hideObjectOnPrint = True
                tenor = deepcopy(bass)
                bari = deepcopy(bass)

        # Specify stem directions explicitly
        if tenor.isNote:
            tenor.stemDirection = 'up'
        lead.stemDirection = 'down'
        if bari.isNote:
            bari.stemDirection = 'up'
        if bass.isNote:
            bass.stemDirection = 'down'

        return FourNoteChord(tenor=tenor, lead=lead, bari=bari, bass=bass)

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

    @staticmethod
    def scanForRangeInfo(score: m21.stream.Score) -> list[VocalRangeInfo]:
        return []
