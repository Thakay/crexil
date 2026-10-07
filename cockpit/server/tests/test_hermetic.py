"""Every test session is sealed before any test module imports the app (hermetic.py): no
test sees a value from a checkout's `.env`, whatever it holds, no test holds a key or
reaches a live runtime, and neither does a process a test starts. Proven with sentinel
values in a planted checkout and a planted shell, never with a real `.env`."""

import os
import re
import subprocess
import sys
from pathlib import Path

from app import config, main
from hermetic import ADDRESSES, PLANTED, SECRETS, breaches

SERVER = Path(__file__).resolve().parents[1]
PROBE = f'{Path(__file__).resolve()}::test_this_session_reads_no_env_holds_no_key_and_reaches_no_live_runtime'


def planted_checkout(root: Path) -> Path:
    """A checkout whose `.env` sets every variable the app reads, and one no code reads,
    to a sentinel."""
    names = {*ADDRESSES, *SECRETS, 'HERMETIC_MARKER'}
    for module in (SERVER / 'app').glob('*.py'):
        names.update(re.findall(r'''os\.environ(?:\.get)?[(\[]\s*["']([A-Z][A-Z0-9_]*)["']''',
                                module.read_text(encoding='utf-8')))
    root.mkdir()
    (root / '.env').write_text(''.join(f'{name}={PLANTED}-{name.lower()}\n' for name in sorted(names)))
    return root


def test_this_session_reads_no_env_holds_no_key_and_reaches_no_live_runtime():
    # The whole app is imported, as a session imports it, before the seal is read.
    assert main.app.title
    assert breaches(config) == []


def test_a_session_over_a_planted_env_and_a_planted_shell_is_sealed_too(tmp_path):
    # The child session starts unsealed, from a shell that exported a sentinel for every
    # runtime address and secret, in a checkout whose .env sets everything the app reads.
    checkout = planted_checkout(tmp_path / 'checkout')
    shell = {name: value for name, value in os.environ.items() if name != 'PYTHON_DOTENV_DISABLED'}
    shell.update({name: f'{PLANTED}-shell' for name in (*ADDRESSES, *SECRETS)}, COCKPIT_REPO_ROOT=str(checkout))
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
