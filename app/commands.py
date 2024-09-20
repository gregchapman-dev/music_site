# import typing as t
# import zlib

import music21 as m21

import sqlalchemy as sa

# import click  # for @click.argument('name') or whatever
from flask.cli import AppGroup

from app import app, db, MusicEngine
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
        print(f'   m21Score: {scoreName(me.m21Score)}')
    print('    scoreState:')
    print(f'        shoppedAs: {me.scoreState.shoppedAs}')
    print(f'        shoppedPartRanges: {me.scoreState.shoppedPartRanges}')
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

def scoreName(m21Score: m21.stream.Score):
    if m21Score is None:
        return 'No score.'
    if m21Score.metadata is None:
        return 'Score with no metadata.'

    title: str | None = m21Score.metadata.title
    if not title:
        return 'Untitled score'
    return title

# @user_cli.command('create')
# @click.argument('name')
# def create_user(name):
#     ...
#
# app.cli.add_command(user_cli)
#
# $ flask user create demo
