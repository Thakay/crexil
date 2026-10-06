"""The MCP layer's tests need the mcp package beside the server's own test helpers.
Run them from cockpit/server with mcp layered onto its environment:

    cd cockpit/server && uv run --with mcp==2.3.0 pytest ../../tools/agent-door/tests -q

The server's helpers boot the scripted harness in-process; nothing reaches a
network, an engine or a paid API."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT / 'tools' / 'agent-door', ROOT / 'cockpit' / 'server' / 'tests', ROOT / 'cockpit' / 'server'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture
def anyio_backend():
    return 'asyncio'
