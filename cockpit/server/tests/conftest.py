"""Shared pytest config. anyio ships via starlette and auto-registers its
pytest plugin; this fixture pins the backend so `pytest.mark.anyio` tests run
on plain asyncio, with zero extra dev dependencies."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
