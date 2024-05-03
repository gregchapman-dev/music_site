from pathlib import Path
import tempfile
import argparse
import sys
import typing as t
import music21 as m21
from music21.base import VERSION_STR
import converter21
from converter21.shared import M21Utilities

from music_site import MusicEngine, ArrangementType

# returns True if the test passed
def runTheTest(inputPath: Path) -> bool:
    # import into music21
    print(f'parsing input file: {inputPath}')
    score1 = m21.converter.parse(inputPath, format='musicxml', forceSource=True)

    assert score1 is not None
    assert score1.isWellFormedNotation()

    # Some MusicXML files have abbreviations instead of chordKinds (e.g. 'min' instead of
    # the correct 'minor').  Fix that before the diff is performed.
    M21Utilities.fixupBadChordKinds(score1, inPlace=True)

    # Some MusicXML files have beams that go 'start'/'continue' when they should be
    # 'start'/'stop'. fixupBadBeams notices that the next beam is a 'start', or is
    # not present at all, and therefore patches that 'continue' to be a 'stop'.
    M21Utilities.fixupBadBeams(score1, inPlace=True)

    print('shopping score for lower and upper voices')
    lowerShop: m21.stream.Score = (
        MusicEngine.shopPillarMelodyNotesFromLeadSheet(score1, ArrangementType.LowerVoices)
    )
    upperShop: m21.stream.Score = (
        MusicEngine.shopPillarMelodyNotesFromLeadSheet(score1, ArrangementType.UpperVoices)
    )

    # export scores back to MEI (without any makeNotation fixups)
#     print('writing both shopped scores to MEI')
#     meiLowerPath = Path(tempfile.gettempdir())
#     meiLowerPath /= (inputPath.stem + '_Lower')
#     meiLowerPath = meiLowerPath.with_suffix('.mei')
#
#     meiUpperPath = Path(tempfile.gettempdir())
#     meiUpperPath /= (inputPath.stem + '_Upper')
#     meiUpperPath = meiUpperPath.with_suffix('.mei')
#
#     lowerShop.write(fp=meiLowerPath, fmt='mei', makeNotation=False)
#     upperShop.write(fp=meiUpperPath, fmt='mei', makeNotation=False)

    # export scores back to Humdrum (without any makeNotation fixups)
#     print('writing both shopped scores to Humdrum')
#     krnLowerPath = Path(tempfile.gettempdir())
#     krnLowerPath /= (inputPath.stem + '_Lower')
#     krnLowerPath = krnLowerPath.with_suffix('.krn')
#
#     krnUpperPath = Path(tempfile.gettempdir())
#     krnUpperPath /= (inputPath.stem + '_Upper')
#     krnUpperPath = krnUpperPath.with_suffix('.krn')
#
#     lowerShop.write(fp=krnLowerPath, fmt='humdrum', makeNotation=False)
#     upperShop.write(fp=krnUpperPath, fmt='humdrum', makeNotation=False)

    lowerGaps: int = MusicEngine.countHarmonyGaps(lowerShop)
    upperGaps: int = MusicEngine.countHarmonyGaps(upperShop)
    print(f'lowerGaps = {lowerGaps}')
    print(f'upperGaps = {upperGaps}')

    # show scores via MusicXML (without any makeNotation fixups)
    print('displaying both shopped scores (via MusicXML/Musescore)')
    score1.show('musicxml.pdf', makeNotation=False)
    lowerShop.show('musicxml.pdf', makeNotation=False)
    lsfp = lowerShop.write(fmt='musicxml', makeNotation=False)
    print(f'lowerShop written to: {lsfp}')
    upperShop.show('musicxml.pdf', makeNotation=False)

    return True

# ------------------------------------------------------------------------------

'''
    main entry point (parse arguments and do conversion)
'''
converter21.register()
converter21.M21Utilities.adjustMusic21Behavior()

parser = argparse.ArgumentParser()
parser.add_argument('input_file')
print('music21 version:', VERSION_STR, file=sys.stderr)
args = parser.parse_args()

runTheTest(Path(args.input_file))

print('done.')

