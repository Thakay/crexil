"""Preview preserves real records while every execution/network path refuses."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from fastapi import HTTPException

from app import api_v4, config, main, wiring
from app.executors import DispatchRequest, EngineFailure
from test_api_v4 import hermetic_env

pytestmark = pytest.mark.anyio


async def test_preview_stays_offline_after_refresh_setup_and_unbench(tmp_path, monkeypatch):
    hermetic_env(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "CREXIL_PREVIEW", True)
    monkeypatch.setattr(config, "HARNESS_EXECUTORS", False)
    network_attempts = []

    async def deny_network(self, request):
        network_attempts.append(str(request.url))
        raise AssertionError(f"preview attempted network: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_network)
    # Preview takes precedence even if a test factory or GPU probe is set.
    def forbidden_factory(_client):
        raise AssertionError("preview selected a real or scripted executor factory")

    monkeypatch.setattr(wiring, "EXECUTOR_FACTORY", forbidden_factory)
    monkeypatch.setattr(wiring, "GPU_PROBE", forbidden_factory)
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1") as client:
            health = await client.get("/api/health")
            assert health.status_code == 503
            assert health.json()["preview"] is True
            assert health.json()["openrouter"]["configured"] is False
            assert "preview" in health.json()["comfyui"]["error"]
            assert (await client.get("/api/providers?refresh=1")).status_code == 200
            assert (await client.get("/api/setup/openrouter")).status_code == 200
            connections = await client.get('/api/crexil/setup')
            assert connections.status_code == 200
            assert connections.json()['preview']
            assert 'disconnected' in connections.json()['comfyui']['error']
            assert not connections.json()['openrouter']['configured']
            operations = (await client.get('/api/crexil/operations')).json()
            for operation in operations:
                requirements = await client.get('/api/crexil/operation-requirements', params={
                    'operation_id': operation['id'], 'revision': operation['revision']})
                assert requirements.status_code == 200
                assert requirements.json()['state'] == 'unknown'
                assert requirements.json()['gaps']
            setup = await client.post("/api/setup/openrouter",
                                      headers={"X-Cockpit-Setup": "1"},
                                      json={"api_key": "preview-must-never-send-this-key"})
            assert setup.status_code == 400
            assert not (tmp_path / "secrets.json").exists()
            assert await wiring.probe_mesh_runner() is False
            with pytest.raises(HTTPException, match="preview"):
                await api_v4._fetch_turntable("saved-job")
            ws = await client.post("/api/workspaces", json={"name": "Real preview record"})
            assert ws.status_code == 201
            wid = ws.json()["id"]
            for lane in ("local", "external"):
                kept = await client.post("/api/unbench", json={"scope": f"lane:{lane}"})
                assert kept.status_code == 422 and kept.json()["detail"] == (
                    "This isolated preview runs no engines, so its attempts stay held.")
            run = await client.post(f"/api/workspaces/{wid}/turns", json={
                "prompt": "must remain unavailable",
                "local": [{"preset_id": "testpreset", "takes": 1}],
            })
            assert run.status_code == 201
            for _ in range(100):
                slots = await wiring.current.db.pending_slots()
                if not slots:
                    break
                await asyncio.sleep(0.01)
            assert not await wiring.current.db.pending_slots()
            detail = (await client.get(f"/api/workspaces/{wid}")).json()
            assert detail["name"] == "Real preview record"
            assert (await client.get(f"/api/workspaces/{wid}/nodes")).json() == []
            # All executor routes refuse, including direct access after unbench.
            for executor in wiring.current.scheduler._executors.values():
                assert isinstance(executor, wiring.UnavailableExecutor)
                with pytest.raises(EngineFailure, match="preview"):
                    await executor.start(DispatchRequest(slot_id="test", engine="test"))
                assert (await executor.reconcile({})).kind == "failed"
                assert await executor.verify_charge("test") is None
            assert wiring.current.openrouter.requests_total == 0
    assert network_attempts == []


def test_preview_config_overrides_inherited_live_paths(tmp_path):
    server = Path(__file__).resolve().parents[1]
    env = {**os.environ, "CREXIL_PREVIEW": "1", "COCKPIT_REPO_ROOT": str(tmp_path),
           "COCKPIT_DB": "/live/db.sqlite", "COCKPIT_STAGING_DIR": "/live/staging",
           "COCKPIT_STATIC_DIR": "/live/static", "PROMOTE_DIR": "/live/handoff",
           "PROMOTE_ROOTS": "/live", "OPENROUTER_API_KEY": "inherited-secret",
           "COCKPIT_HARNESS_EXECUTORS": "1"}
    code = """
import json
from app import config as c
print(json.dumps({k: str(getattr(c, k)) for k in (
    'DB_PATH', 'STAGING_DIR', 'STATIC_DIR', 'PROMOTE_DIR',
    'OPENROUTER_API_KEY', 'HARNESS_EXECUTORS')}))
"""
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            cwd=server, capture_output=True, text=True, check=True)
    values = json.loads(result.stdout)
    for key in ("DB_PATH", "STAGING_DIR", "STATIC_DIR", "PROMOTE_DIR"):
        assert Path(values[key]).is_relative_to(tmp_path / ".preview")
    assert values["OPENROUTER_API_KEY"] == ""
    assert values["HARNESS_EXECUTORS"] == "False"


def test_preview_launcher_refuses_production_before_building():
    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(["bash", str(repo / "scripts" / "preview.sh"), "8000"],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert "production :8000" in result.stderr
    assert "Building" not in result.stdout
