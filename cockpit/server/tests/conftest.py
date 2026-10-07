"""Shared pytest config. The session is sealed here, before any test module imports the
app (hermetic.py): no `.env` is read, no key is set and every runtime address is closed.
anyio ships via starlette and auto-registers its pytest plugin; this fixture pins the
backend so `pytest.mark.anyio` tests run on plain asyncio, with zero extra dev
dependencies."""

import pytest

from hermetic import seal

seal()


@pytest.fixture
def anyio_backend():
    return "asyncio"
