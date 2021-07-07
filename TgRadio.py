#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import random
import subprocess
import sys
from asyncio import create_subprocess_exec
from datetime import datetime
from functools import partial

import ffmpeg
from dotenv import load_dotenv
from ffmpeg import compile, Error
from pyrogram import Client, filters
from pyrogram.filters import command, create as create_filter
from pyrogram.methods.chats.get_chat_members import Filters
from pyrogram.types import Message
from pyrogram.utils import MAX_CHANNEL_ID
from pytgcalls import GroupCall


def _l(message):
    print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]', message)


def _dl(message):
    if '-v' in sys.argv[1:]:
        print(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]', message)


def in_background(coroutine):
    async def wrapper(*args, **kwargs):
        return asyncio.create_task(coroutine(*args, **kwargs))

    return wrapper


# Load `.env` and check
load_dotenv()
api_id = os.getenv('TG_API_ID')
if not api_id:
    _l('API_ID is missing in .env')
    exit(1)
api_hash = os.getenv('TG_API_HASH')
if not api_hash:
    _l('TG_API_HASH is missing in .env')
    exit(1)
phone_number = os.getenv('TG_PHONE_NUMBER')
if not phone_number:
    _l('TG_PHONE_NUMBER is missing in .env')
    exit(1)

# Run client
app = Client(
    os.getenv('SESSION_NAME', "TgRadio"),
    api_id=api_id,
    api_hash=api_hash,
    phone_number=phone_number
)

GROUP_CALLS = {}
GENERATORS = {}


async def custom_filter(_, __, m: Message):
    if not m.chat:
        return False
    if m.chat.type not in {"group", "supergroup"}:
        return False
    available_groups = os.getenv('AVAILABLE_GROUPS', '')
    if available_groups:
        if str(m.chat.id) not in available_groups.split(','):
            return False
    admins = await app.get_chat_members(m.chat.id, filter=Filters.ADMINISTRATORS)
    if m.from_user.id not in map(lambda x: x.user.id, admins):
        return False
    return True


CUSTOM_FILTER = create_filter(custom_filter)

# Commands

_cmd = partial(command, prefixes=os.getenv('CMD_PREFIX', '!'), case_sensitive=True)

START_RADIO_CMD = _cmd('start-radio')
STOP_RADIO_CMD = _cmd('stop-radio')
LIST_CMD = _cmd('list')
ADD_TRACK_CMD = _cmd('add-track')
ADD_INSERT_CMD = _cmd('add-insert')
ADD_ANNOUNCE_CMD = _cmd('add-announce')
RM_TRACK_CMD = _cmd('rm-track')
RM_INSERT_CMD = _cmd('rm-insert')
RM_ANNOUNCE_CMD = _cmd('rm-announce')
HELP_CMD = _cmd('help')


@app.on_message(filters=CUSTOM_FILTER & START_RADIO_CMD & filters.text)
@in_background
async def start_radio(_app, message):
    _gen = GENERATORS.get(message.chat.id)
    if _gen is None:
        _gen = playlist_generator(message)
        GENERATORS[message.chat.id] = _gen

    _group_call = GROUP_CALLS.get(message.chat.id)
    if _group_call is None:
        _group_call = GroupCall(
            _app,
            next(_gen),
            path_to_log_file='',
            enable_logs_to_console=True,
            play_on_repeat=False
        )
        _group_call.on_playout_ended(on_playout_ended)
        _group_call.on_network_status_changed(on_network_changed)
        GROUP_CALLS[message.chat.id] = _group_call

    if not _group_call.is_connected:
        try:
            await _group_call.start(message.chat.id)
        except RuntimeError as e:
            _l(f'ID:{message.chat.id} starting radio runtime error: {e}')
            if str(e) == 'Chat without a voice chat':
                await app.send_message(message.chat.id, '🚧 Start voice chat to be able to start radio.')
                return
        except Exception as e:
            _l(f'ID:{message.chat.id} starting radio error: {e}')
            await app.send_message(message.chat.id, '🚧 When starting radio, something went wrong.')


@app.on_message(filters=CUSTOM_FILTER & STOP_RADIO_CMD & filters.text)
@in_background
async def stop_radio(_, message):
    _group_call = GROUP_CALLS.get(message.chat.id)
    if _group_call is None:
        return

    if _group_call.is_connected:
        await _group_call.stop()


@app.on_message(filters=CUSTOM_FILTER & LIST_CMD & filters.text)
@in_background
async def get_list(_, message):
    f_mp3 = lambda x: x.endswith('.raw')
    ftm = lambda x: f'  - {x[:-4]}'
    result = "Tracks:\n"
    try:
        result += '\n'.join(map(ftm, filter(f_mp3, os.listdir(get_track_path(message)))))
    except Exception as e:
        pass
    result += "\nAnnounces:\n"
    try:
        result += '\n'.join(map(ftm, filter(f_mp3, os.listdir(get_announce_path(message)))))
    except Exception as e:
        pass
    result += "\nInserts:\n"
    try:
        result += '\n'.join(map(ftm, filter(f_mp3, os.listdir(get_insert_path(message)))))
    except Exception as e:
        pass
    await app.send_message(message.chat.id, result)


@app.on_message(filters=CUSTOM_FILTER & ADD_TRACK_CMD & filters.audio)
@in_background
async def add_track(_, message):
    path = os.path.join(get_track_path(message), normalize_file_name(message.audio.file_name))
    if await processing_audio(message, path):
        GENERATORS[message.chat.id] = playlist_generator(message)


@app.on_message(filters=CUSTOM_FILTER & ADD_INSERT_CMD & filters.audio)
@in_background
async def add_insert(_, message):
    path = os.path.join(get_insert_path(message), normalize_file_name(message.audio.file_name))
    if await processing_audio(message, path):
        GENERATORS[message.chat.id] = playlist_generator(message)


@app.on_message(filters=CUSTOM_FILTER & ADD_ANNOUNCE_CMD & filters.audio)
@in_background
async def add_announce(_, message):
    path = os.path.join(get_announce_path(message), normalize_file_name(message.audio.file_name))
    if await processing_audio(message, path):
        GENERATORS[message.chat.id] = playlist_generator(message)


@app.on_message(filters=CUSTOM_FILTER & RM_TRACK_CMD & filters.text)
@in_background
async def rm_track(_, message):
    if len(message.command) != 2:
        await app.send_message(message.chat.id, '🚧 Incorrect command. Example: !rm-track <file name from !list>')
        return

    track_name = message.command[1]
    result = rm(message, track_name, get_track_path(message))
    await app.send_message(message.chat.id, result)


@app.on_message(filters=CUSTOM_FILTER & RM_INSERT_CMD & filters.text)
@in_background
async def rm_insert(_, message):
    if len(message.command) != 2:
        await app.send_message(message.chat.id, '🚧 Incorrect command. Example: !rm-insert <file name from !list>')
        return

    insert_name = message.command[1]
    result = rm(message, insert_name, get_insert_path(message))
    await app.send_message(message.chat.id, result)


@app.on_message(filters=CUSTOM_FILTER & RM_ANNOUNCE_CMD & filters.text)
@in_background
async def rm_announce(_, message):
    if len(message.command) != 2:
        await app.send_message(message.chat.id, '🚧 Incorrect command. Example: !rm-announce <file name from !list>')
        return

    announce_name = message.command[1]
    result = rm(message, announce_name, get_announce_path(message))
    await app.send_message(message.chat.id, result)


@app.on_message(filters=CUSTOM_FILTER & HELP_CMD & filters.text)
@in_background
async def help_cmd(_, message):
    def _s(cmd):
        return f"{next(iter(cmd.prefixes))}{next(iter(cmd.commands))}"

    _msg = f"""
TgRadio bot welcomes you.
Following commands are available:
 - **{_s(START_RADIO_CMD)}** - Go on air.
 - **{_s(STOP_RADIO_CMD)}** - Stop a radio broadcast.
 - **{_s(LIST_CMD)}** - Get a playlist.
 - **{_s(ADD_TRACK_CMD)}** - Append track in playlist.
 - **{_s(ADD_ANNOUNCE_CMD)}** - Append announce in playlist. Just send mp3 audio file with command.
 - **{_s(ADD_INSERT_CMD)}** - Append insert in playlist. Just send mp3 audio file with command.
 - **{_s(RM_TRACK_CMD)} <filename>** - Remove track from playlist. Just send mp3 audio file with command.
 - **{_s(RM_ANNOUNCE_CMD)} <filename>** - Remove announce from playlist.
 - **{_s(RM_INSERT_CMD)} <filename>** - Remove insert from playlist.
"""
    await app.send_message(message.chat.id, _msg)


# Auxiliary functions


def rm(message, file_name, path):
    _l(f'ID:{message.chat.id} rm {file_name} from {os.path.basename(path)}')
    f_mp3 = lambda x: x.endswith('.raw')
    ftm = lambda x: x[:-4]
    try:
        items = list(map(ftm, filter(f_mp3, os.listdir(path))))
        if file_name not in items:
            return '🚧 File not found'
        os.remove(os.path.join(path, file_name))
        os.remove(os.path.join(path, file_name + '.raw'))
        GENERATORS[message.chat.id] = playlist_generator(message)
        return '🤙 File was deleted successfully'
    except Exception as e:
        _l(f'ID:{message.chat.id} rm {file_name} error: {e}')
        return '🚧 An error occurred while deleting the file. Please check !list.'


async def processing_audio(message, path) -> bool:
    _l(f'ID:{message.chat.id} ')
    if os.path.isfile(path):
        _l(f'ID:{message.chat.id} File with {os.path.basename(path)} name exist.')
        await app.send_message(message.chat.id, '🚧 File with this name exists')
        return False

    def _progress(current, total):
        _dl(f"ID:{message.chat.id} Download {os.path.basename(path)} file progress {current * 100 / total:.1f}%")

    try:
        await app.download_media(message, file_name=path, progress=_progress)
    except Exception as e:
        _l(f'ID:{message.chat.id} download audio {os.path.basename(path)} error: {e}')
        await app.send_message(message.chat.id, '🚧 File download error. Repeat later.')
        return False

    try:
        await convert_audio(path)
    except Exception as e:
        _l(f'ID:{message.chat.id} convert audio {os.path.basename(path)} error: {e}')
        await app.send_message(message.chat.id, '🚧 File convert error. Something went wrong.')
        return False

    _l(f'ID:{message.chat.id} File with {os.path.basename(path)} name added successfully.')
    await app.send_message(message.chat.id, '🤌 File was added successfully')
    return True


async def on_playout_ended(gc: GroupCall, file_name):
    chat_id = MAX_CHANNEL_ID - gc.full_chat.id
    _gen = GENERATORS.get(chat_id)
    if _gen:
        _l(f'ID:{chat_id} Playout ended. Get next from generator.')
        gc.input_filename = next(_gen)
    gc.restart_playout()
    await asyncio.sleep(0.1)


async def on_network_changed(gc: GroupCall, is_connected: bool):
    chat_id = MAX_CHANNEL_ID - gc.full_chat.id
    if is_connected:
        _l(f'Start radio for ID:{chat_id}')
        await app.send_message(chat_id, '🎉 We are on the air!1')
    else:
        _l(f'Stop radio for ID:{chat_id}')
        await app.send_message(chat_id, '🤙 Broadcast is over, thank you all.')


def normalize_file_name(filename: str):
    return filename.strip().replace(' ', '_')


def get_track_path(message):
    return os.path.join(os.getcwd(), 'data', str(message.chat.id), 'tracks')


def get_insert_path(message):
    return os.path.join(os.getcwd(), 'data', str(message.chat.id), 'inserts')


def get_announce_path(message):
    return os.path.join(os.getcwd(), 'data', str(message.chat.id), 'announces')


def playlist_generator(message):
    as_track_path = lambda x: os.path.join(get_track_path(message), x)
    as_insert_path = lambda x: os.path.join(get_insert_path(message), x)
    as_announce_path = lambda x: os.path.join(get_announce_path(message), x)
    f_mp3 = lambda x: x.endswith('.raw')

    tracks = []
    try:
        tracks = list(map(as_track_path, filter(f_mp3, os.listdir(get_track_path(message)))))
    except Exception as e:
        pass

    inserts = []
    try:
        inserts = list(map(as_insert_path, filter(f_mp3, os.listdir(get_insert_path(message)))))
    except Exception as e:
        pass

    announces = []
    try:
        announces = list(map(as_announce_path, filter(f_mp3, os.listdir(get_announce_path(message)))))
    except Exception as e:
        pass

    _l(f'(Re)Create generator for ID:{message.chat.id} with T:{len(tracks)} A:{len(announces)} Z:{len(inserts)}')

    if not tracks:
        while True:
            yield os.path.join(os.getcwd(), 'default.raw')

    if not inserts:
        while True:
            random.shuffle(tracks)
            for track in tracks:
                yield track

    track_counter = 0
    while True:
        for track in tracks:
            if track_counter == 3:
                track_counter = 0
                if len(announces) > 0:
                    yield random.choice(announces)
                yield random.choice(inserts)
            if len(announces) > 0:
                yield random.choice(announces)
            yield track
            track_counter += 1


async def convert_audio(path):
    await _run_ffmpeg(ffmpeg.input(path).output(
        f'{path}.raw',
        format='s16le',
        acodec='pcm_s16le',
        ac=2,
        ar='48k'
    ).overwrite_output())


async def _run_ffmpeg(stream_spec,
                      cmd='ffmpeg',
                      pipe_stdin=False,
                      pipe_stdout=False,
                      pipe_stderr=False,
                      input=None,
                      quiet=False,
                      overwrite_output=False):
    args = compile(stream_spec, cmd, overwrite_output=overwrite_output)
    stdin_stream = subprocess.PIPE if pipe_stdin else None
    stdout_stream = subprocess.PIPE if pipe_stdout or quiet else None
    stderr_stream = subprocess.PIPE if pipe_stderr or quiet else None
    p = await create_subprocess_exec(
        *args, stdin=stdin_stream, stdout=stdout_stream, stderr=stderr_stream
    )
    out, err = await p.communicate(input)
    if p.returncode != 0:
        raise Error('ffmpeg', out, err)
    return out, err


app.run()
