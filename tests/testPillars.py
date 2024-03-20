from pathlib import Path
import tempfile
import argparse
import sys

import music21 as m21
import converter21

from music_site import (
    MusicEngine, MusicEngineException, ArrangementType, VocalRange, VocalRangeInfo
)


def testShopPillarsFromLeadSheet(inScore: m21.stream.Score) -> m21.stream.Score:
    return MusicEngine.shopPillarMelodyNotesFromLeadSheet(inScore)



# ------------------------------------------------------------------------------

'''
    main entry point (parse arguments and do conversion)
'''
# register converter21's Humdrum and MEI converters
converter21.register()

parser = argparse.ArgumentParser()
parser.add_argument('input_file')
args = parser.parse_args()

inPath: Path = Path(args.input_file)
print(f'leadsheet input file: {inPath}')

# use '_ShoppedPillars.mxl' if you want compressed output (fmt is still 'musicxml')
outPath: Path = Path(tempfile.gettempdir()) / (inPath.stem + '_ShoppedPillars.musicxml')
print(f'shopped pillars output file: {outPath}')

leadSheetScore: m21.stream.Score = m21.converter.parseFile(inPath)
shoppedPillarsScore: m21.stream.Score = testShopPillarsFromLeadSheet(leadSheetScore)
shoppedPillarsScore.write(fmt='musicxml', fp=outPath, makeNotation=False)


