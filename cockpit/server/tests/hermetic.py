"""The seal every test session runs under. `conftest.py` here and the agent door's own
conftest apply it when pytest loads them, before any test module imports the app.

A sealed session reads no `.env`: python-dotenv is switched off (`PYTHON_DOTENV_DISABLED`)
in this process and in every process a test starts, so a checkout's keys, store and
engines never reach a test, whatever its `.env` holds. Every runtime address `app.config`
reads points at a closed port and every key and agent id is blank, whatever the shell
exported, so no test holds a key or reaches a live engine or a paid API. A test that needs
an engine or a key points `config` at its own fake.

The opt-in live checks (`LIVE_CATALOG=1`, `LIVE_ELEVENLABS=1`) name their service's public
address themselves, and the ElevenLabs one takes the key the shell exported from
`EXPORTED`, which no other test reads.
"""

import os

# A privileged port: nothing in a user session listens on it.
CLOSED = 'http://127.0.0.1:9'
ADDRESSES = ('COMFYUI_URL', 'THREED_URL', 'VOICE_RUNNER_URL', 'OPENROUTER_URL', 'ELEVENLABS_URL')
SECRETS = ('OPENROUTER_API_KEY', 'ELEVENLABS_API_KEY', 'ELEVENLABS_VOICE_AGENT_ID')
# What the shell exported for each secret before the seal blanked it.
EXPORTED: dict[str, str] = {}
# The mark the guard tests plant in a `.env` and a shell; no real value carries it.
PLANTED = 'planted-sentinel'


def seal() -> None:
    """Seals this process's environment; once sealed, a second call changes nothing."""
    if EXPORTED:
        return
    EXPORTED.update({name: os.environ.get(name, '') for name in SECRETS})
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ.update(dict.fromkeys(ADDRESSES, CLOSED))
    os.environ.update(dict.fromkeys(SECRETS, ''))


def breaches(config) -> list[str]:
    """What escapes the seal in this session, in plain words; empty while it holds. It
    names a secret without its value. A runtime address `config` reads that the seal does
    not close counts, so a new one joins `ADDRESSES`."""
    values = vars(config)
    addresses = {name: value for name, value in values.items() if name.endswith('_URL')}
    secrets = {name: value for name, value in values.items() if name.endswith(('_KEY', '_AGENT_ID'))}
    found = [f'{name} is not one the seal closes' for name in sorted(set(addresses) - set(ADDRESSES))]
    found += [f'{name} reads {value}, not the closed port' for name, value in addresses.items() if value != CLOSED]
    found += [f'{name} is not one the seal blanks' for name in sorted(set(secrets) - set(SECRETS))]
    found += [f'{name} holds a value' for name, value in secrets.items() if value]
    found += [f'the environment variable {name} holds a planted value'
              for name, value in os.environ.items() if PLANTED in value]
    found += [f'config.{name} holds a planted value' for name, value in values.items() if PLANTED in str(value)]
    return found
