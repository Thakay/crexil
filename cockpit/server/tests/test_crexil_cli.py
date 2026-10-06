"""The CLI sends shared read parameters while keeping action paths local."""

import importlib.util
import io
import sys
from email.message import Message
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('crexil_cli', Path(__file__).resolve().parents[3] / 'scripts/crexil.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_read_query_reaches_shared_endpoint_without_a_command(monkeypatch, capsys):
    requests = []

    def send(request, **_):
        requests.append(request)
        response = io.BytesIO(b'{"state":"unknown"}')
        response.headers = Message()
        response.headers['Content-Type'] = 'application/json'
        return response

    monkeypatch.setattr(cli.urllib.request, 'urlopen', send)
    path = 'operation-requirements?operation_id=custom-example&revision=recorded'
    monkeypatch.setattr(sys, 'argv', ['crexil.py', 'get', path])
    assert cli.main() == 0
    assert requests[0].full_url == 'http://127.0.0.1:8010/api/crexil/' + path
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
