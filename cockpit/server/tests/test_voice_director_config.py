"""The voice director's configuration and its apply script, checked without a key or a
network: the file names exactly the client tools the panel handles, ends a session at the
installation's session maximum, requires a server-minted token, records no voice, and holds
no secret. A dry run validates and prints its plan, and never reads a key or opens a
connection."""

import importlib.util
import json
import re
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOLS_TS = REPO / 'cockpit/web/src/app/voice-director/tools.ts'
CONFIG_PY = REPO / 'cockpit/server/app/config.py'


def load_apply():
    spec = importlib.util.spec_from_file_location('voice_director_apply', REPO / 'tools/voice-director/apply.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apply = load_apply()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError('the voice director config tests open no connection')
    monkeypatch.setattr(urllib.request, 'urlopen', refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', refuse)


def config() -> dict:
    return apply.load()


def test_the_shipped_configuration_is_valid():
    assert apply.problems(config()) == []


def test_the_tools_are_exactly_the_ones_the_panel_handles():
    panel = re.search(r'TOOL_NAMES = \[([^\]]*)\]', TOOLS_TS.read_text(encoding='utf-8')).group(1)
    handled = sorted(re.findall(r"'([a-z_]+)'", panel))
    assert sorted(tool['name'] for tool in config()['client_tools']) == handled == sorted(apply.TOOLS)


def test_a_session_ends_at_the_installations_session_maximum():
    default = re.search(r"CREXIL_VOICE_SESSION_MAX_S['\"],\s*['\"]?(\d+)", CONFIG_PY.read_text(encoding='utf-8'))
    assert default, 'config.py names the session maximum default'
    assert config()['conversation_config']['conversation']['max_duration_seconds'] == int(default.group(1)) == apply.SESSION_MAX_S


def test_only_a_server_minted_token_starts_it_and_no_voice_is_recorded():
    agent = config()
    assert agent['platform_settings']['auth']['enable_auth'] is True
    assert agent['platform_settings']['privacy']['record_voice'] is False
    assert agent['conversation_config']['agent']['prompt']['enable_parallel_tool_calls'] is False


def test_the_providers_call_limits_back_the_installations_minutes_cap():
    # Crexil's minutes cap stays the guard. ElevenLabs' own limits back it: no burst
    # conversations past the concurrency limit at the higher rate, and ten a day.
    limits = {'daily_limit': 10, 'agent_concurrency_limit': 1, 'bursting_enabled': False}
    assert config()['platform_settings']['call_limits'] == limits
    assert apply.agent_body(config(), ['tool'] * len(apply.TOOLS))['platform_settings']['call_limits'] == limits


def test_the_file_holds_no_secret_and_no_account_id():
    text = (REPO / 'tools/voice-director/agent.json').read_text(encoding='utf-8')
    assert 'xi-api-key' not in text and 'agent_id' not in text and 'tool_ids' not in text
    assert not re.search(r'\bsk_[A-Za-z0-9]{8,}', text)


def test_every_tool_waits_for_its_answer_and_describes_every_parameter():
    for tool in config()['client_tools']:
        assert tool['type'] == 'client' and tool['expects_response'] is True, tool['name']
        properties = tool['parameters']['properties']
        assert set(tool['parameters']['required']) <= set(properties), tool['name']
        assert all(spec.get('description') for spec in properties.values()), tool['name']


@pytest.mark.parametrize('change, words', [
    (lambda c: c['client_tools'].pop(), 'The client tools must be exactly'),
    (lambda c: c['conversation_config']['conversation'].update(max_duration_seconds=900), 'max_duration_seconds is 900'),
    (lambda c: c['platform_settings']['auth'].update(enable_auth=False), 'must require authentication'),
    (lambda c: c['platform_settings']['privacy'].update(record_voice=True), 'Voice recording must be off'),
    (lambda c: c['conversation_config']['agent']['prompt'].update(enable_parallel_tool_calls=True), 'Parallel tool calls must be off'),
    (lambda c: c['conversation_config']['agent']['prompt'].update(tool_ids=['t1']), 'tool_ids belong to an installation'),
    (lambda c: c.update(note='sk_live0123456789abcdef'), 'no key and no account id'),
])
def test_a_configuration_that_breaks_a_rule_is_refused(change, words):
    broken = config()
    change(broken)
    assert any(words in problem for problem in apply.problems(broken))


def test_a_dry_run_validates_and_prints_its_plan_without_a_key_or_a_connection(monkeypatch, capsys):
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'sk_never0read0by0a0dry0run')
    assert apply.main(['--dry-run']) == 0
    printed = capsys.readouterr().out
    plan = json.loads(printed)
    assert plan['valid'] is True
    assert 'sk_never0read0by0a0dry0run' not in printed and 'xi-api-key' not in printed
    steps = plan['plan']
    assert steps[0]['path'] == '/v1/convai/llm/list'
    assert [step['body']['tool_config']['name'] for step in steps[1:-1]] == [tool['name'] for tool in config()['client_tools']]
    agent = steps[-1]
    assert (agent['method'], agent['path']) == ('POST', '/v1/convai/agents/create')
    assert 'client_tools' not in agent['body']
    assert len(agent['body']['conversation_config']['agent']['prompt']['tool_ids']) == len(apply.TOOLS)


def test_a_dry_run_for_an_existing_agent_plans_an_update(capsys):
    assert apply.main(['--dry-run', '--agent-id', 'agent-123', '--voice-id', 'voice-9']) == 0
    steps = json.loads(capsys.readouterr().out)['plan']
    assert steps[1] == {'method': 'GET', 'path': '/v1/convai/agents/agent-123', 'why': 'read its current tool ids'}
    assert (steps[-1]['method'], steps[-1]['path']) == ('PATCH', '/v1/convai/agents/agent-123')
    assert steps[-1]['body']['conversation_config']['tts']['voice_id'] == 'voice-9'


def test_a_dry_run_against_a_different_session_maximum_is_refused(capsys):
    assert apply.main(['--dry-run', '--session-max', '120']) == 1
    assert 'max_duration_seconds is 300' in capsys.readouterr().err
