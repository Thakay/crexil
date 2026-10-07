"""The seal every test session runs under. `conftest.py` here and the agent door's own
conftest apply it when pytest loads them, before any test module imports the app.

A sealed session reads no `.env`: python-dotenv is switched off (`PYTHON_DOTENV_DISABLED`)
in this process and in every process a test starts, so a checkout's settings never reach a
test, whatever its `.env` holds. The store, its staging folder and the hand-off folders
default to the session's own temporary folder (`SESSION`), so a test that names none of its
own never opens the checkout's store, the key saved beside it or a real hand-off folder.
Every runtime address points at a closed port and every key and agent id is blank, whatever
the shell exported, so no test holds a key or reaches a live engine or a paid API. A test
that needs an engine, a key or a store points `config` at its own.

The opt-in live checks (`LIVE_CATALOG=1`, `LIVE_ELEVENLABS=1`) name their service's public
address themselves; under `LIVE_ELEVENLABS=1` the seal keeps the ElevenLabs key the shell
exported in `EXPORTED`, which holds nothing otherwise.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# A privileged port: nothing in a user session listens on it.
CLOSED = 'http://127.0.0.1:9'
# The runtime addresses the app and the 3D runner read.
ADDRESSES = ('COMFYUI_URL', 'THREED_URL', 'THREED_COMFY_URL', 'VOICE_RUNNER_URL', 'OPENROUTER_URL', 'ELEVENLABS_URL')
SECRETS = ('OPENROUTER_API_KEY', 'ELEVENLABS_API_KEY', 'ELEVENLABS_VOICE_AGENT_ID')
# A setting whose name ends so holds a secret; a string with such a scheme is an address.
SECRET_SUFFIXES = ('_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_AGENT_ID')
SCHEMES = ('http://', 'https://', 'ws://', 'wss://')
# The key the shell exported for the opt-in live ElevenLabs check, and only for it.
EXPORTED: dict[str, str] = {}
# The session's own folder for its store, staging and hand-off folders, made by `seal`.
SESSION: Path | None = None
# The mark the guard tests plant in a `.env`, a saved key and a shell; no real value carries it.
PLANTED = 'planted-sentinel'


def seal() -> None:
    """Seals this process's environment; once sealed, a second call changes nothing."""
    global SESSION
    if SESSION is not None:
        return
    if os.environ.get('LIVE_ELEVENLABS') == '1':
        EXPORTED['ELEVENLABS_API_KEY'] = os.environ.get('ELEVENLABS_API_KEY', '')
    SESSION = Path(tempfile.mkdtemp(prefix='crexil-tests-')).resolve()
    atexit.register(shutil.rmtree, SESSION, True)
    os.environ.update(PYTHON_DOTENV_DISABLED='1', COCKPIT_DB=str(SESSION / 'cockpit-v4.sqlite'),
                      COCKPIT_STAGING_DIR=str(SESSION / 'staging'), PROMOTE_DIR=str(SESSION / 'handoff'),
                      PROMOTE_ROOTS=str(SESSION))
    os.environ.update(dict.fromkeys(ADDRESSES, CLOSED))
    os.environ.update(dict.fromkeys(SECRETS, ''))


def breaches(config) -> list[str]:
    """What escapes the seal in this session, in plain words; empty while it holds. It names
    a secret without its value. A runtime address or a secret `config` holds that the seal
    does not cover counts, so a new one joins `ADDRESSES` or `SECRETS`."""
    values = {name: value for name, value in vars(config).items() if name.isupper()}
    addresses = {name: value for name, value in values.items()
                 if name.endswith('_URL') or isinstance(value, str) and value.startswith(SCHEMES)}
    secrets = {name: value for name, value in values.items() if name.endswith(SECRET_SUFFIXES)}
    found = [f'{name} is not one the seal closes' for name in sorted(set(addresses) - set(ADDRESSES))]
    found += [f'{name} reads {value}, not the closed port' for name, value in addresses.items() if value != CLOSED]
    found += [f'the environment variable {name} is not the closed port'
              for name in ADDRESSES if os.environ.get(name) != CLOSED]
    found += [f'{name} is not one the seal blanks' for name in sorted(set(secrets) - set(SECRETS))]
    found += [f'{name} holds a value' for name, value in secrets.items() if value]
    folders = {name: [values.get(name)] for name in ('DB_PATH', 'STAGING_DIR', 'PROMOTE_DIR')}
    folders['PROMOTE_ROOTS'] = list(values.get('PROMOTE_ROOTS') or [None])
    found += [f'{name} lies outside the session folder' for name, paths in folders.items()
              if SESSION is None or not all(path and Path(path).resolve().is_relative_to(SESSION) for path in paths)]
    if EXPORTED and os.environ.get('LIVE_ELEVENLABS') != '1':
        found.append('A key is kept for a live check no one asked for.')
    found += [f'the environment variable {name} holds a planted value'
              for name, value in os.environ.items() if PLANTED in value]
    found += [f'config.{name} holds a planted value' for name, value in values.items() if PLANTED in str(value)]
    return found
