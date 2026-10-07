"""The CLI sends shared read parameters while keeping action paths local, names a
participant for every change, and reaches the address CREXIL_URL names."""

import importlib.util
import io
import json
import sys
from email.message import Message
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('crexil_cli', Path(__file__).resolve().parents[3] / 'scripts/crexil.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture(autouse=True)
def no_ambient_target(monkeypatch):
    monkeypatch.delenv('CREXIL_URL', raising=False)
    monkeypatch.delenv('CREXIL_ACTOR', raising=False)


def transport(monkeypatch, body=b'{"state":"unknown"}', content_type='application/json'):
    requests = []

    def send(request, **_):
        requests.append(request)
        response = io.BytesIO(body)
        response.headers = Message()
        response.headers['Content-Type'] = content_type
        return response

    monkeypatch.setattr(cli.urllib.request, 'urlopen', send)
    return requests


def test_read_query_reaches_shared_endpoint_without_a_command(monkeypatch, capsys):
    requests = transport(monkeypatch)
    path = 'operation-requirements?operation_id=custom-example&revision=recorded'
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'get', path])
    assert cli.main() == 0
    assert requests[0].full_url == 'http://127.0.0.1:8000/api/crexil/' + path
    assert requests[0].get_method() == 'GET' and requests[0].data is None
    assert not capsys.readouterr().err


@pytest.mark.parametrize('path', ['/projects', '../health', '%2e%2e/health', '%2fhealth',
                                  'https://example.invalid/projects', '//example.invalid/projects',
                                  'projects#fragment'])
def test_read_path_cannot_escape_shared_api(path, monkeypatch):
    def send(*_, **__):
        pytest.fail('Invalid action path reached the transport')
    monkeypatch.setattr(cli.urllib.request, 'urlopen', send)
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'get', path])
    with pytest.raises(SystemExit, match='2'):
        cli.main()


def test_the_target_and_participant_come_from_the_environment(monkeypatch):
    requests = transport(monkeypatch)
    monkeypatch.setenv('CREXIL_URL', 'http://127.0.0.1:8123')
    monkeypatch.setenv('CREXIL_ACTOR', 'agent:Scout')
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'post', 'projects', '--json', '{"name":"Dwarf"}'])
    assert cli.main() == 0
    sent = json.loads(requests[0].data)
    assert requests[0].full_url == 'http://127.0.0.1:8123/api/crexil/projects'
    assert sent['actor'] == {'kind': 'agent', 'name': 'Scout'} and sent['name'] == 'Dwarf' and sent['request_id']


@pytest.mark.parametrize('argv', [['post', 'projects', '--json', '{}'], ['reveal', 'run1'],
                                  ['--actor', 'Artist', 'post', 'projects', '--json', '{}']])
def test_every_change_names_its_participant(argv, monkeypatch, capsys):
    def send(*_, **__):
        pytest.fail('A change without a participant reached the transport')
    monkeypatch.setattr(cli.urllib.request, 'urlopen', send)
    monkeypatch.setattr(sys, 'argv', ['crexil.py', *argv])
    with pytest.raises(SystemExit, match='2'):
        cli.main()
    assert 'human:NAME or agent:NAME' in capsys.readouterr().err


def test_a_review_reads_without_a_participant(monkeypatch):
    requests = transport(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'post', 'projects/p1/deliveries/review', '--json', '{"node_ids":["n"]}'])
    assert cli.main() == 0
    assert 'actor' not in json.loads(requests[0].data)


def test_decisions_prints_the_decision_record_as_lines(monkeypatch, capsys):
    requests = transport(monkeypatch, b'{"decision_id":"a"}\n', 'application/x-ndjson')
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'decisions', 'p1'])
    assert cli.main() == 0
    assert requests[0].full_url == 'http://127.0.0.1:8000/api/crexil/projects/p1/decisions.jsonl'
    assert capsys.readouterr().out == '{"decision_id":"a"}\n'


def test_reveal_sends_an_attributed_command(monkeypatch):
    requests = transport(monkeypatch)
    monkeypatch.setattr(sys, 'argv', ['crexil.py', '--actor', 'human:Ada', 'reveal', 'run1', '--reason', 'Done listening'])
    assert cli.main() == 0
    sent = json.loads(requests[0].data)
    assert requests[0].full_url == 'http://127.0.0.1:8000/api/crexil/turns/run1/reveal'
    assert requests[0].get_method() == 'POST'
    assert sent['reason'] == 'Done listening' and sent['actor'] == {'kind': 'human', 'name': 'Ada'}
