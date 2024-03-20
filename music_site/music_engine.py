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
    def __init__(
        self,
        lowest: m21.pitch.Pitch | None = None,
        highest: m21.pitch.Pitch | None = None
    ):
        self.lowest: m21.pitch.Pitch | None = lowest
        self.highest: m21.pitch.Pitch | None = highest

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
        self.fullRange = VocalRange()
        for i, n in enumerate(s[m21.note.Note]):
            if i == 0:
                self.fullRange.lowest = n.pitch
                self.fullRange.highest = n.pitch
                continue

            self.fullRange.lowest = min(self.fullRange.lowest, n.pitch)
            self.fullRange.highest = max(self.fullRange.highest, n.pitch)

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
        inLeadSheet: m21.stream.Score,
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
            raise MusicEngineException

        # Now, if necessary, switch from Treble clef to Treble8vb clef (and transpose
        # the melody down an octave to match).
        initialClef: m21.clef.Clef | None = None

        for clef in (melody.recurse()
            .getElementsByClass(m21.clef.Clef)
            .getElementsByOffsetInHierarchy(0.0)
        ):
            initialClef = clef
            break

        if initialClef is None or isinstance(initialClef, m21.clef.TrebleClef):
            if initialClef is not None:
                melody.remove(initialClef)
            melody.insert(0, m21.clef.Treble8vbClef())
            interval = m21.interval.Interval('P-8')
            with m21.stream.makeNotation.saveAccidentalDisplayStatus(melody):
                melody.transpose(interval, inPlace=True)

        # Now pick a key that will work for lead voice range, and transpose.
        melodyInfo = VocalRangeInfo(melody)


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

        # From "Arranging Barbershop: Volume 2" pp11-13
        # 'lead': Copy the melody
        #
        #  For each of the three harmony parts, harmonize the melody's pillar notes (the
        #       melody notes that are in the chord) as follows:
        #
        # 'bass': Start the bass on the root or fifth for seventh chords and the root
        #           for triads. Try to follow the general shape of the melody so that
        #           bass notes are higher when the melody is higher and lower when the
        #           melody is lower.  This will help the voicings to not become too
        #           spread.  Consider bass voice leading when the harmony is a seventh
        #           chord and the melody is not on the root or fifth.
        # 'tenor': (Above melody) Use one of the unused notes in the chord, or double the
        #           root if it is a triad.  Consider voice leading and seek for fewer
        #           awkward leaps.  There may be times with choosing a different bass
        #           note would allow a smoother tenor part.
        # 'bari': Complete each chord or double the root if it is a triad.  As with the
        #           tenor, try to not have the part jump around unnecessarily and consider
        #           changing the bass or tenor note if it leads to a smoother baritone
        #           part.

        # First we process the melody into the lead part (creating the entire output
        # score structure as we go, including parts, measures, and voices; inserting
        # any clefs/keysigs/timesigs in all the appropriate measures; and inserting
        # the chord symbols into the measures in the top staff).
        #
        # Then we will harmonize the three harmony parts, one at a time, potentially
        # tweaking other already harmonized parts as we go, to get better voice
        # leading.

        for currPart in ('bass', 'tenor', 'bari'):
            if currPart == 'lead':
                # copy melody notes into the lead part
                continue

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
