from pathlib import Path
import tempfile
import argparse
import sys
import datetime
import typing as t
import music21 as m21
from music21.base import VERSION_STR
import converter21
from converter21.shared import M21Utilities

from music_site import MusicEngine, ArrangementType

# returns True if the test passed
def runTheTest(inputPath: Path, results) -> bool:
    print(f'{inputPath}: ', end='')
    print(f'{inputPath}: ', end='', file=results)
    results.flush()

    # import into music21
    try:
        score1 = m21.converter.parse(inputPath, format='musicxml', forceSource=True)
        if score1 is None:
            print('score1 creation failure')
            print('score1 creation failure', file=results)
            results.flush()
            return False
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'score1 creation crash: {e}')
        print(f'score1 creation crash: {e}', file=results)
        results.flush()
        return False

    if not score1.elements:
        # empty score is valid result, but assume diff will be exact
        # (export of empty score fails miserably)
        print('empty score1')
        print('empty score1', file=results)
        results.flush()
        return True

    if not score1.isWellFormedNotation():
        print('score1 not well formed')
        print('score1 not well formed', file=results)
        results.flush()
        return False

    try:
        # Some MusicXML files have abbreviations instead of chordKinds (e.g. 'min' instead of
        # the correct 'minor').  Fix that before the diff is performed.
        M21Utilities.fixupBadChordKinds(score1, inPlace=True)

        # Some MusicXML files have beams that go 'start'/'continue' when they should be
        # 'start'/'stop'. fixupBadBeams notices that the next beam is a 'start', or is
        # not present at all, and therefore patches that 'continue' to be a 'stop'.
        M21Utilities.fixupBadBeams(score1, inPlace=True)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'M21Utilities fixup crash: {e}')
        print(f'M21Utilities fixup crash: {e}', file=results)
        results.flush()
        return False

    try:
        lowerShop: m21.stream.Score = (
            MusicEngine.shopPillarMelodyNotesFromLeadSheet(score1, ArrangementType.LowerVoices)
        )
        upperShop: m21.stream.Score = (
            MusicEngine.shopPillarMelodyNotesFromLeadSheet(score1, ArrangementType.UpperVoices)
        )
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'MusicEngine shop crash: {e}')
        print(f'MusicEngine shop crash: {e}', file=results)
        results.flush()
        return False

    # export scores back to MEI (without any makeNotation fixups)
    try:
        success: bool = True
        meiLowerPath = Path(tempfile.gettempdir())
        meiLowerPath /= (inputPath.stem + '_Lower')
        meiLowerPath = meiLowerPath.with_suffix('.mei')
        meiUpperPath = Path(tempfile.gettempdir())
        meiUpperPath /= (inputPath.stem + '_Upper')
        meiUpperPath = meiUpperPath.with_suffix('.mei')

        success = lowerShop.write(fp=meiLowerPath, fmt='mei', makeNotation=False)
        success = upperShop.write(fp=meiUpperPath, fmt='mei', makeNotation=False) and success
        if not success:
            print('MEI export failed')
            print('MEI export failed', file=results)
            results.flush()
            return False
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'MEI export crash: {e}')
        print(f'MEI export crash: {e}', file=results)
        results.flush()
        return False

    # export scores back to Humdrum (without any makeNotation fixups)
    try:
        success: bool = True
        krnLowerPath = Path(tempfile.gettempdir())
        krnLowerPath /= (inputPath.stem + '_Lower')
        krnLowerPath = krnLowerPath.with_suffix('.krn')
        krnUpperPath = Path(tempfile.gettempdir())
        krnUpperPath /= (inputPath.stem + '_Upper')
        krnUpperPath = krnUpperPath.with_suffix('.krn')

        success = lowerShop.write(fp=krnLowerPath, fmt='humdrum', makeNotation=False)
        success = upperShop.write(fp=krnUpperPath, fmt='humdrum', makeNotation=False) and success
        if not success:
            print('Humdrum export failed')
            print('Humdrum export failed', file=results)
            results.flush()
            return False
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'Humdrum export crash: {e}')
        print(f'Humdrum export crash: {e}', file=results)
        results.flush()
        return False

    # export scores back to MusicXML (without any makeNotation fixups)
    try:
        success: bool = True
        musicxmlLowerPath = Path(tempfile.gettempdir())
        musicxmlLowerPath /= (inputPath.stem + '_Lower')
        musicxmlLowerPath = musicxmlLowerPath.with_suffix('.musicxml')
        musicxmlUpperPath = Path(tempfile.gettempdir())
        musicxmlUpperPath /= (inputPath.stem + '_Upper')
        musicxmlUpperPath = musicxmlUpperPath.with_suffix('.musicxml')

        success = lowerShop.write(fp=musicxmlLowerPath, fmt='musicxml', makeNotation=False)
        success = upperShop.write(fp=musicxmlUpperPath, fmt='musicxml', makeNotation=False) and success
        if not success:
            print('MusicXML export failed')
            print('MusicXML export failed', file=results)
            results.flush()
            return False
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'MusicXML export crash: {e}')
        print(f'MusicXML export crash: {e}', file=results)
        results.flush()
        return False

    # compute how many melody notes have no harmonization
    lowerGaps: int = 0
    upperGaps: int = 0
    try:
        lowerGaps = MusicEngine.countHarmonyGaps(lowerShop)
        upperGaps = MusicEngine.countHarmonyGaps(upperShop)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f'Gap counting crash: {e}')
        print(f'Gap counting crash: {e}', file=results)
        results.flush()
        return False

    print(f'all good (lowerGaps: {lowerGaps} upperGaps: {upperGaps})')
    print(f'all good (lowerGaps: {lowerGaps} upperGaps: {upperGaps})', file=results)

    return True

# ------------------------------------------------------------------------------

'''
    main entry point (parse arguments and do conversion)
'''
converter21.register()
converter21.M21Utilities.adjustMusic21Behavior()

parser = argparse.ArgumentParser(
            description='Loop over listfile (list of .musicxml files), shopping and then exporting back to .mei, .krn, and .musicxml.  No comparisons yet, just make sure it doesn\'t raise an Exception.')
parser.add_argument(
        'list_file',
        help='file containing a list of the .musicxml/.mxl files to shop (full paths)')

print('music21 version:', VERSION_STR, file=sys.stderr)
args = parser.parse_args()


listPath: Path = Path(args.list_file)
goodPath: Path = Path(str(listPath.parent) + '/' + str(listPath.stem)
                        + '.goodList.txt')
badPath: Path = Path(str(listPath.parent) + '/' + str(listPath.stem)
                        + '.badList.txt')
resultsPath: Path = Path(str(listPath.parent) + '/' + str(listPath.stem)
                        + '.resultsList.txt')

fileList: [str] = []
with open(listPath, encoding='utf-8') as listf:
    s: str = listf.read()
    fileList = s.split('\n')

with open(goodPath, 'w', encoding='utf-8') as goodf:
    with open(badPath, 'w', encoding='utf-8') as badf:
        with open(resultsPath, 'w', encoding='utf-8') as resultsf:
            startTime = datetime.datetime.now()
            print(f'start time: {startTime}')
            print(f'start time: {startTime}', file=resultsf)
            for i, file in enumerate(fileList):
                if not file or file[0] == '#':
                    # blank line, or commented out
                    print(file)
                    print(file, file=resultsf)
                    resultsf.flush()
                    continue

                if runTheTest(Path(file), resultsf):
                    resultsf.flush()
                    print(file, file=goodf)
                    goodf.flush()
                else:
                    resultsf.flush()
                    print(file, file=badf)
                    badf.flush()
            endTime = datetime.datetime.now()
            elapsedTime = endTime - startTime
            print(f'end time: {endTime}')
            print(f'elapsed time: {elapsedTime}')
            print(f'end time: {endTime}', file=resultsf)
            print(f'elapsed time: {elapsedTime}', file=resultsf)

            resultsf.flush()
        badf.flush()
    goodf.flush()

print('done.')

