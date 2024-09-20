import typing as t
# import sys
import zlib
import pickle

import music21 as m21

import converter21

from app import MusicEngineException
from app import ArrangementType
from app import PartName
from app import VocalRange
from app import MusicEngineUtilities

# Register the Humdrum and MEI readers/writers from converter21
converter21.register()

class ScoreState:
    def __init__(self) -> None:
        self.shoppedAs: ArrangementType | None = None
        self.shoppedPartRanges: dict[PartName, VocalRange] | None = None

class MusicEngine:
    def __init__(self) -> None:
        self.m21Score: m21.stream.Score | None = None
        self.scoreState: ScoreState = ScoreState()

        # undoList is a list of commands that will back out recent changes
        self.undoList: list[dict[str, t.Any]] = []

        # redoList is a list of commands that will redo recent undoes.
        self.redoList: list[dict[str, t.Any]] = []

    def freeze(self) -> bytes:
        storage: dict[str, t.Any] = {}
        if self.m21Score is not None:
            storage['m21Score'] = MusicEngineUtilities.freezeScore(self.m21Score)
        storage['scoreState'] = self.scoreState
        storage['undoList'] = self.undoList
        storage['redoList'] = self.redoList
        output: bytes = pickle.dumps(storage)
        output = zlib.compress(output)
        return output

    @classmethod
    def thaw(cls, frozenEngine: bytes):
        try:
            uncompressed: bytes = zlib.decompress(frozenEngine)
            storage: dict[str, t.Any] = pickle.loads(uncompressed)
        except Exception as e:
            print(f'thaw failed: {e}')
            return None

        me = cls()
        if 'm21Score' in storage and storage['m21Score']:
            me.m21Score = MusicEngineUtilities.thawScore(storage['m21Score'])
            me.scoreState = storage['scoreState']
            me.undoList = storage['undoList']
            me.redoList = storage['redoList']

        return me

    @classmethod
    def fromFileData(cls, fileData: str | bytes, fileName: str):
        m21Score: m21.stream.Score = MusicEngineUtilities.toMusic21Score(fileData, fileName)
        me = cls()
        me.m21Score = m21Score
        return me

    def toMusicXML(self) -> str:
        if self.m21Score is not None:
            return MusicEngineUtilities.toMusicXML(self.m21Score)
        return ''

    def toHumdrum(self) -> str:
        if self.m21Score is not None:
            return MusicEngineUtilities.toHumdrum(self.m21Score)
        return ''

    def toMei(self) -> str:
        if self.m21Score is not None:
            return MusicEngineUtilities.toMei(self.m21Score)
        return ''

    def transposeInPlace(self, semitones: int, approximate: bool = False) -> int:
        if self.m21Score is None:
            raise MusicEngineException('Cannot transpose: there is no score.')

        actualSemitones: int = (
            MusicEngineUtilities.transposeInPlace(self.m21Score, semitones, approximate)
        )

        self.undoList.append({
            'cmd': 'transpose',
            'semitones': -actualSemitones
        })

        return actualSemitones

    def shopIt(self, arrType: ArrangementType):
        if self.m21Score is None:
            return

        # This is too big an operation to undo with a command.  Stash off the whole
        # score to restore in an undo.
#         oldScore: m21.stream.Score = self.m21Score

        shopped: m21.stream.Score
        partRanges: dict[PartName, VocalRange]
        shopped, partRanges = MusicEngineUtilities.shopIt(self.m21Score, arrType)

        # note that we do a freezeScore here so that we can just freeze the undoList
        # later without having to treat embedded scores specially.
#         self.undoList.append({
#             'command': 'restore',
#             'score': MusicEngineUtilities.freezeScore(oldScore),
#             'scoreState': self.scoreState
#         })
        # for now, this is a "cannot undo" command.  Clear the undoList.
        self.undoList = []

        self.m21Score = shopped
        self.scoreState.shoppedAs = arrType
        self.scoreState.shoppedPartRanges = partRanges

    def chooseChordOption(self, optionId: str):
        if self.m21Score is None:
            raise MusicEngineException('Cannot choose chord option: there is no score.')

        if self.scoreState.shoppedPartRanges is None:
            raise MusicEngineException('Cannot choose chord option: the score is not shopped.')

        undoOptionId: str = MusicEngineUtilities.chooseChordOption(
            self.m21Score, optionId, self.scoreState.shoppedPartRanges
        )
        self.undoList.append({
            'cmd': 'chooseChordOption',
            'optionId': undoOptionId
        })
