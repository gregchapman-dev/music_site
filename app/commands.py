# import typing as t
# import zlib

import music21 as m21

import sqlalchemy as sa

# import click  # for @click.argument('name') or whatever
from flask.cli import AppGroup

from app import app, db, MusicEngine, PartName, VocalRange, ArrangementType
from app.models import AnonymousSession

gdb_cli = AppGroup('gdb')
app.cli.add_command(gdb_cli)

@gdb_cli.command('dump')
def dump():
    # flask gdb dump
    query = sa.select(AnonymousSession)
    sessions = db.session.scalars(query).all()
    for s in sessions:
        print(f'------------{s.sessionUUID}------------')
        printFrozenMusicEngine(s.musicEngine)
        printZippedMeiFile(s.mei)
        printZippedHumdrumFile(s.humdrum)
        printZippedMusicXmlFile(s.musicxml)

def printFrozenMusicEngine(frozenMe: bytes | None):
    if frozenMe is None:
        print('musicEngine: None.')
        return
    if frozenMe == b'':
        print('musicEngine: empty bytes.')
        return

    try:
        me: MusicEngine = MusicEngine.thaw(frozenMe)
    except Exception as e:
        print(f'musicEngine: unthawable. {e}')
        return

    print(f'musicEngine (frozen length = {len(frozenMe)}):')
    if me.m21Score is None:
        print('    m21Score: None.')
    else:
        print(f'   m21Score: {scoreString(me.m21Score)}')
    print('    scoreState:')
    print(f'        shoppedAs: {shoppedAsString(me.scoreState.shoppedAs)}')
    print(f'        shoppedPartRanges: {partRangesString(me.scoreState.shoppedPartRanges)}')
    if not me.undoList:
        print('    undoList: empty')
    else:
        print('    undoList:')
    for i, undo in enumerate(me.undoList):
        print(f'{i}: {undo}')
    if not me.redoList:
        print('    redoList: empty')
    else:
        print('    redoList:')
    for i, redo in enumerate(me.redoList):
        print(f'{i}: {redo}')

def printZippedMeiFile(zippedMei: bytes | None):
    return

def printZippedHumdrumFile(zippedHumdrum: bytes | None):
    return

def printZippedMusicXmlFile(zippedMusicXml: bytes | None):
    return

def scoreString(m21Score: m21.stream.Score) -> str:
    if m21Score is None:
        return 'No score.'
    if m21Score.metadata is None:
        return 'Score with no metadata.'

    bestTitle: str | None = m21Score.metadata.bestTitle
    if not bestTitle:
        return 'Untitled score'
    return bestTitle

def partRangesString(partRanges: dict[PartName, VocalRange] | None) -> str:
    if partRanges is None:
        return 'None'

    output: str = ''
    for i, (part, vrange) in enumerate(partRanges.items()):
        if i > 0:
            output += ', '
        output += part.name + ': ' + str(vrange)

    return output

def shoppedAsString(shoppedAs: ArrangementType | None) -> str:
    if shoppedAs is None:
        return 'None'
    return shoppedAs.name

# @user_cli.command('create')
# @click.argument('name')
# def create_user(name):
#     ...
#
# app.cli.add_command(user_cli)
#
# $ flask user create demo
