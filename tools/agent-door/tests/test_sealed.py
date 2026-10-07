"""The door's test session is sealed as the server's is: it reads no `.env`, holds no key
and reaches no live runtime (cockpit/server/tests/hermetic.py)."""

from app import config, main
from hermetic import breaches


def test_this_session_reads_no_env_holds_no_key_and_reaches_no_live_runtime():
    assert main.app.title
    assert breaches(config) == []
