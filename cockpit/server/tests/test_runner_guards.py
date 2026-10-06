"""Both local runners answer only this machine's cockpit. A request addressed to
another host (DNS rebinding), any request from a web page (an Origin), a body
that changes state without its declared type (a cross-site form or text post),
and a bind beyond loopback without an explicit allow are refused in plain
words, while the server's own executors' requests still pass."""

import importlib.util
import os
import sys
import threading
from contextlib import contextmanager
from email.message import Message
from pathlib import Path

import httpx
import pytest

from app.executors import EngineFailure
from app.mesh import MeshExecutor
from test_local_voice import Collector, opened, request, served
from test_local_voice import runner as voice

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]


def load_threed():
    """The 3D runner as its own process sees it: tools/3d on the path for its
    funnel import, whose one environment default is undone afterwards."""
    folder, before = str(REPO / 'tools/3d'), os.environ.get('U2NET_HOME')
    spec = importlib.util.spec_from_file_location('threed_runner', REPO / 'tools/3d/runner.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['threed_runner'] = module
    sys.path.insert(0, folder)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(folder)
        if before is None:
            os.environ.pop('U2NET_HOME', None)
    return module


threed = load_threed()
BOTH = pytest.mark.parametrize('runner', [voice, threed], ids=['voice', '3d'])


def headers(**fields):
    message = Message()
    for name, value in fields.items():
        message[name.replace('_', '-')] = value
    return message


def ask(base, method, path, *, host=None, **fields):
    """One request with every header under the test's control (Host included)."""
    sent = dict(fields.pop('headers', {}))
    if host is not None:
        sent['Host'] = host
    with httpx.Client(base_url=base, timeout=5) as client:
        return client.request(method, path, headers=sent, **fields)


# --- the rules, as functions ---

@BOTH
def test_a_host_header_is_read_without_port_case_or_brackets(runner):
    assert runner.host_named('127.0.0.1:8190') == '127.0.0.1'
    assert runner.host_named('LOCALHOST') == 'localhost'
    assert runner.host_named('[::1]:8190') == '::1'
    assert runner.host_named('host.docker.internal:8189') == 'host.docker.internal'
    for unreadable in (None, '', 'localhost:port', '[::1'):
        assert runner.host_named(unreadable) is None


@BOTH
def test_a_request_must_be_addressed_to_this_machine(runner):
    for host in ('127.0.0.1:8190', 'localhost', '[::1]:9', 'host.docker.internal:8189'):
        assert runner.refusal(headers(Host=host), runner.LOCAL_HOSTS) is None
    for host in ('evil.example:8190', 'localhost.evil.example', '192.168.1.5:8190', 'localhost:port', None):
        status, why = runner.refusal(headers(Host=host) if host else headers(), runner.LOCAL_HOSTS)
        assert status == 403 and 'answers requests addressed to' in why
    extended = runner.LOCAL_HOSTS | {'gpu-box.lan'}
    assert runner.refusal(headers(Host='GPU-BOX.lan:8190'), extended) is None


@BOTH
def test_any_origin_is_a_web_page_and_is_refused(runner):
    # No web page is a client of a runner, not even one served from this machine.
    for origin in ('http://evil.example', 'null', 'http://localhost:5173', 'http://127.0.0.1:8000'):
        status, why = runner.refusal(headers(Host='127.0.0.1:8190', Origin=origin), runner.LOCAL_HOSTS)
        assert (status, why) == (403, "Refused: this runner serves the cockpit's server, never a web page.")


@BOTH
def test_a_body_that_changes_state_must_name_its_type(runner):
    assert runner.wrong_type(headers(Content_Type='application/json; charset=utf-8'), 'application/json') is None
    assert runner.wrong_type(headers(Content_Type='Model/GLTF-Binary'), 'model/gltf-binary') is None
    for given in (None, 'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x'):
        found = runner.wrong_type(headers(Content_Type=given) if given else headers(), 'application/json')
        assert found == (415, 'Refused: send the body as application/json.')


@BOTH
def test_listening_beyond_loopback_needs_an_explicit_allow(runner):
    for host in ('127.0.0.1', 'localhost', '::1', '127.0.0.2'):
        runner.check_bind(host, allow_remote=False)
    for host in ('0.0.0.0', '::', '192.168.1.5', 'gpu-box.lan'):
        with pytest.raises(ValueError, match='has no accounts, so it serves this machine only'):
            runner.check_bind(host, allow_remote=False)
        runner.check_bind(host, allow_remote=True)


def test_the_voice_runner_refuses_to_start_on_the_network_unasked():
    with pytest.raises(SystemExit, match='Refusing to listen on 0.0.0.0'):
        voice.main(['--host', '0.0.0.0'])
    with pytest.raises(ValueError, match='Refusing to listen on 0.0.0.0'):
        voice.serve(voice.Runner.__new__(voice.Runner), '0.0.0.0', 0)


def test_the_3d_runner_refuses_to_start_on_the_network_unasked(monkeypatch, capsys):
    monkeypatch.setattr(threed, 'HOST', '0.0.0.0')
    monkeypatch.setattr(threed, 'ALLOW_REMOTE', False)
    assert threed.main() == 2
    assert 'Refusing to listen on 0.0.0.0' in capsys.readouterr().err


# --- the voice runner, served ---

def test_the_voice_runner_answers_this_machine_and_the_container_name(tmp_path):
    with served(tmp_path) as (_, _, base):
        assert ask(base, 'GET', '/health').status_code == 200
        assert ask(base, 'GET', '/health', host='host.docker.internal:8190').status_code == 200
        rebound = ask(base, 'GET', '/health', host='evil.example:8190')
    assert rebound.status_code == 403 and 'addressed to evil.example' in rebound.json()['detail']


def test_the_voice_runner_refuses_a_web_page_on_every_method(tmp_path):
    page = {'Origin': 'http://evil.example'}
    with served(tmp_path) as (backend, door, base):
        answers = [ask(base, 'GET', '/health', headers=page),
                   ask(base, 'POST', '/jobs', headers={**page, 'Content-Type': 'application/json'},
                       content=b'{"kind": "voice-design"}'),
                   ask(base, 'DELETE', '/jobs/anything', headers=page)]
        assert door._records == {} and backend.calls == []
    assert [a.status_code for a in answers] == [403, 403, 403]


def test_the_voice_runner_takes_a_job_only_as_json(tmp_path):
    body = b'{"kind": "voice-design", "text": "Steel remembers.", "instruct": "a gruff dwarf"}'
    with served(tmp_path) as (backend, door, base):
        answers = [ask(base, 'POST', '/jobs', headers={'Content-Type': kind}, content=body)
                   for kind in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data')]
        unnamed = ask(base, 'POST', '/jobs', content=body)
        assert door._records == {} and backend.calls == []
        reached = ask(base, 'POST', '/jobs', headers={'Content-Type': 'application/json'}, content=b'{}')
    assert [a.status_code for a in answers] + [unnamed.status_code] == [415, 415, 415, 415]
    assert answers[0].json()['detail'] == 'Refused: send the body as application/json.'
    assert reached.status_code == 422                     # past the door: the body itself is refused


def test_a_cancel_needs_no_body_type(tmp_path):
    with served(tmp_path) as (_, _, base):
        answer = ask(base, 'DELETE', '/jobs/unknown')
    assert answer.status_code == 404 and answer.json() == {'detail': 'No such job.'}


async def test_the_voice_executors_requests_pass_the_door(tmp_path):
    with served(tmp_path) as (backend, _, base):
        ex, collected = await opened(base), Collector()
        try:
            handle = await ex.start(request(collector=collected))
            result = await ex.wait(handle)
        finally:
            await ex.close()
    assert backend.calls and len(collected.landed()) == 3 and result is not None


# --- the 3D runner, served ---

@contextmanager
def threed_served(tmp_path, monkeypatch):
    for name in ('RUNS', 'JOBS', 'UPLOADS', 'FIELDS', 'SERVE'):
        monkeypatch.setattr(threed, name, tmp_path / name.lower())
    monkeypatch.setitem(threed.INPUTS['mesh'], 'dir', threed.UPLOADS)
    monkeypatch.setitem(threed.INPUTS['fields'], 'dir', threed.FIELDS)
    threed.JOBS.mkdir(parents=True)
    server = threed.ThreadingHTTPServer(('127.0.0.1', 0), threed.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()


def test_the_3d_runner_answers_this_machine_and_the_container_name(tmp_path, monkeypatch):
    with threed_served(tmp_path, monkeypatch) as base:
        local = ask(base, 'GET', '/jobs/unknown')
        container = ask(base, 'GET', '/jobs/unknown', host='host.docker.internal:8189')
        rebound = ask(base, 'GET', '/jobs/unknown', host='evil.example:8189')
        page = ask(base, 'GET', '/jobs/unknown', headers={'Origin': 'http://evil.example'})
    assert (local.status_code, container.status_code) == (404, 404)    # past the door: no such job
    assert (rebound.status_code, page.status_code) == (403, 403)


def test_the_3d_runner_takes_each_body_only_in_its_declared_type(tmp_path, monkeypatch):
    with threed_served(tmp_path, monkeypatch) as base:
        job = ask(base, 'POST', '/jobs', headers={'Content-Type': 'text/plain'}, content=b'{}')
        mesh = ask(base, 'POST', '/meshes', headers={'Content-Type': 'application/octet-stream'},
                   content=b'glTF....')
        fields = ask(base, 'POST', '/fields', headers={'Content-Type': 'model/gltf-binary'},
                     content=b'PK\x03\x04....')
        removal = ask(base, 'DELETE', '/jobs/unknown', headers={'Origin': 'null'})
        uploads = list(threed.UPLOADS.glob('*')) if threed.UPLOADS.exists() else []
    assert [r.status_code for r in (job, mesh, fields, removal)] == [415, 415, 415, 403]
    assert mesh.json()['detail'] == 'Refused: send the body as model/gltf-binary.'
    assert uploads == []


def test_a_refused_body_never_spills_into_the_next_request(tmp_path, monkeypatch):
    # The 3D runner keeps connections alive. A refusal drains a small body, says it
    # closes the connection, and closes it, so the answer always arrives and the
    # body is never read as the next request.
    with threed_served(tmp_path, monkeypatch) as base, httpx.Client(base_url=base, timeout=5) as client:
        refused = client.post('/meshes', headers={'Content-Type': 'text/plain'}, content=b'x' * 4096)
        after = client.get('/jobs/unknown')
    assert (refused.status_code, refused.headers['Connection']) == (415, 'close')
    assert after.status_code == 404


async def test_the_3d_executors_requests_pass_the_door(tmp_path, monkeypatch):
    with threed_served(tmp_path, monkeypatch) as base:
        ex = MeshExecutor(base)
        await ex.open()
        try:
            with pytest.raises(EngineFailure) as mesh:
                await ex._upload_mesh(b'not a mesh')
            with pytest.raises(EngineFailure) as fields:
                await ex._upload_mesh(b'not a field set', 'fields')
            job = await ex._http.post('/jobs', json={'engine': 'nothing'})
        finally:
            await ex.close()
    # each reached its route and was refused there for its content, not at the door
    assert "does not start with 'glTF'" in str(mesh.value) and 'Refused:' not in str(mesh.value)
    assert 'not an npz archive' in str(fields.value) and 'Refused:' not in str(fields.value)
    assert job.status_code == 422
