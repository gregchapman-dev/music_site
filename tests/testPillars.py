from pathlib import Path
import tempfile
import argparse
import sys

import music21 as m21
import converter21

from music_site import (
    MusicEngine, MusicEngineException, ArrangementType, VocalRange, VocalRangeInfo
)


def testShopPillars(inScore: m21.stream.Score, arrType: ArrangementType) -> m21.stream.Score:
    return MusicEngine.shopPillarMelodyNotesFromLeadSheet(inScore, arrType)



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
outPath: Path = Path(tempfile.gettempdir()) / (inPath.stem + '_ShoppedPillarsLower.musicxml')
print(f'shopped pillars (lower) output file: {outPath}')

leadSheetScore: m21.stream.Score = m21.converter.parseFile(inPath, forceSource=True)
shoppedPillarsLowerVoicesScore: m21.stream.Score = (
    testShopPillars(leadSheetScore, ArrangementType.LowerVoices)
)
shoppedPillarsLowerVoicesScore.write(fmt='musicxml', fp=outPath, makeNotation=False)

outPath: Path = Path(tempfile.gettempdir()) / (inPath.stem + '_ShoppedPillarsUpper.musicxml')
print(f'shopped pillars (upper) output file: {outPath}')

shoppedPillarsUpperVoicesScore: m21.stream.Score = (
    testShopPillars(leadSheetScore, ArrangementType.UpperVoices)
)
shoppedPillarsUpperVoicesScore.write(fmt='musicxml', fp=outPath, makeNotation=False)

humdrumPath: Path = Path(tempfile.gettempdir()) / (inPath.stem + '_ShoppedPillarsUpper.krn')
print(f'shopped pillars (upper) Humdrum file: {humdrumPath}')
shoppedPillarsUpperVoicesScore.write(fmt='humdrum', fp=humdrumPath, makeNotation=False)

print('all done.')

