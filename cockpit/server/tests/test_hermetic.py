"""Every test session is sealed before any test module imports the app (hermetic.py): no
test sees a value from a checkout's `.env`, whatever it holds, nor the checkout's store or the
key saved beside it; no test holds a key or reaches a live runtime, and neither does a process
a test starts. Proven with sentinel values in a planted checkout, a planted saved key and a
planted shell, never with a real `.env`."""

import json
import os
import re
import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config, main, secrets_store
from hermetic import ADDRESSES, CLOSED, PLANTED, SECRETS, breaches
from test_runner_guards import threed

SERVER = Path(__file__).resolve().parents[1]
PROBE = f'{Path(__file__).resolve()}::test_this_session_reads_no_env_holds_no_key_and_reaches_no_live_runtime'
# What the seal points at the session's own folder.
STORE = ('COCKPIT_DB', 'COCKPIT_STAGING_DIR', 'PROMOTE_DIR', 'PROMOTE_ROOTS')


def planted_checkout(root: Path) -> Path:
    """A checkout whose `.env` sets every variable the app reads, and one no code reads, to a
    sentinel, and whose default store keeps a saved key holding one."""
    names = {*ADDRESSES, *SECRETS, 'HERMETIC_MARKER'}
    for module in (SERVER / 'app').glob('*.py'):
        names.update(re.findall(r'''os\.environ(?:\.get)?[(\[]\s*["']([A-Z][A-Z0-9_]*)["']''',
                                module.read_text(encoding='utf-8')))
    (root / 'cockpit/server').mkdir(parents=True)
    (root / '.env').write_text(''.join(f'{name}={PLANTED}-{name.lower()}\n' for name in sorted(names)))
    (root / 'cockpit/server/secrets.json').write_text(json.dumps({'openrouter_api_key': f'{PLANTED}-saved-key'}))
    return root


def test_this_session_reads_no_env_holds_no_key_and_reaches_no_live_runtime():
    # The whole app is imported, as a session imports it, before the seal is read.
    assert main.app.title
    assert breaches(config) == []
    assert PLANTED not in (secrets_store.get_secret('openrouter_api_key') or '')


@pytest.mark.parametrize('store_in_shell', [False, True], ids=['store by default', 'store exported'])
def test_a_session_over_a_planted_env_and_a_planted_shell_is_sealed_too(tmp_path, store_in_shell):
    # The child session starts unsealed, from a shell that exported a sentinel for every
    # runtime address and secret, in a checkout whose .env sets everything the app reads and
    # whose default store keeps a planted saved key; the shell names a store of its own, or none.
    checkout = planted_checkout(tmp_path / 'checkout')
    shell = {name: value for name, value in os.environ.items() if name not in ('PYTHON_DOTENV_DISABLED', *STORE)}
    shell.update({name: f'{PLANTED}-shell' for name in (*ADDRESSES, *SECRETS)}, COCKPIT_REPO_ROOT=str(checkout))
    if store_in_shell:
        shell.update({name: str(tmp_path / f'{PLANTED}-{name.lower()}') for name in STORE})
    child = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', PROBE],
                           cwd=SERVER, env=shell, capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, child.stdout[-4000:] + child.stderr[-2000:]
    assert '1 passed' in child.stdout


def test_a_process_a_test_starts_reads_no_env_either(tmp_path):
    checkout = planted_checkout(tmp_path / 'checkout')
    code = ('from app import config, main\n'
            f'print([name for name, value in vars(config).items() if {PLANTED!r} in str(value)])\n')
    child = subprocess.run([sys.executable, '-c', code], cwd=SERVER, env={**os.environ, 'COCKPIT_REPO_ROOT': str(checkout)},
                           capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stderr[-2000:]
    assert child.stdout.strip() == '[]'


@pytest.mark.parametrize('setting, value', [
    ('HF_TOKEN', 'exported'), ('WEBHOOK_SECRET', 'exported'), ('SMTP_PASSWORD', 'exported'),
    ('TELEMETRY_ENDPOINT', 'https://telemetry.example'), ('EVENTS_SOCKET', 'wss://events.example'),
    ('DB_PATH', Path('/elsewhere/cockpit-v4.sqlite')), ('PROMOTE_ROOTS', [Path('/elsewhere')]),
])
def test_the_guard_names_any_secret_live_address_or_store_the_seal_does_not_cover(setting, value):
    # A setting config may gain: a secret by its name, an address by its scheme, or a store
    # or hand-off folder outside the session's own.
    settings = {name: value for name, value in vars(config).items() if name.isupper()}
    assert breaches(SimpleNamespace(**settings)) == []
    found = breaches(SimpleNamespace(**{**settings, setting: value}))
    assert any(breach.startswith(setting) for breach in found), found
    assert not any('exported' in breach for breach in found)              # a secret is named, never its value


def test_only_the_live_elevenlabs_check_keeps_a_key_and_only_its_own():
    probe = ('import json, os, hermetic\n'
             'hermetic.seal()\n'
             "print(json.dumps({'kept': sorted(hermetic.EXPORTED),\n"
             f"                  'planted': hermetic.EXPORTED.get('ELEVENLABS_API_KEY') == {PLANTED + '-elevenlabs'!r},\n"
             "                  'blank': [os.environ[name] for name in hermetic.SECRETS]}))\n")
    unsealed = ('PYTHON_DOTENV_DISABLED', 'LIVE_ELEVENLABS', *STORE)
    shell = {name: value for name, value in os.environ.items() if name not in unsealed}
    shell.update(ELEVENLABS_API_KEY=f'{PLANTED}-elevenlabs', OPENROUTER_API_KEY=f'{PLANTED}-openrouter')

    def sealed(**extra):
        child = subprocess.run([sys.executable, '-c', probe], cwd=SERVER / 'tests', env={**shell, **extra},
                               capture_output=True, text=True, timeout=60)
        assert child.returncode == 0, child.stderr[-2000:]
        return json.loads(child.stdout)

    assert sealed() == {'kept': [], 'planted': False, 'blank': ['', '', '']}
    assert sealed(LIVE_ELEVENLABS='1') == {'kept': ['ELEVENLABS_API_KEY'], 'planted': True, 'blank': ['', '', '']}


def test_the_3d_runner_a_test_loads_reads_the_closed_port():
    assert threed.COMFY_URL == CLOSED


def test_python_dotenv_is_new_enough_for_the_seal():
    # PYTHON_DOTENV_DISABLED came with python-dotenv 1.2.0: the project's floor says so.
    dependencies = tomllib.loads((SERVER / 'pyproject.toml').read_text(encoding='utf-8'))['project']['dependencies']
    assert 'python-dotenv>=1.2' in dependencies
    assert tuple(int(part) for part in version('python-dotenv').split('.')[:2]) >= (1, 2)
