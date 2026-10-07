"""The MCP layer over the door: what a client such as Claude Code sees. Tools and
their schemas, a whole journey through the protocol against the scripted harness,
refusals as tool errors that keep their kind, and the script itself serving over
stdio as Claude Code launches it."""

import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

import crexil_mcp
from test_audio_contract import PREVIEW, booted_audio
from test_blind_runs import LOCAL

pytestmark = pytest.mark.anyio

SCRIPT = Path(crexil_mcp.__file__).resolve()
TOOLS = {'crexil_status', 'crexil_list_engines', 'crexil_list_projects', 'crexil_get_project', 'crexil_create_project',
         'crexil_prepare_run', 'crexil_review_run', 'crexil_submit_run', 'crexil_get_run', 'crexil_propose',
         'crexil_get_decisions', 'crexil_search_record'}
READS = {'crexil_status', 'crexil_list_engines', 'crexil_list_projects', 'crexil_get_project', 'crexil_review_run',
         'crexil_get_run', 'crexil_get_decisions', 'crexil_search_record'}
# Bytes, routes to a take's file, media file names and machine paths never reach the model.
MATERIAL_MARKS = ('data:', 'base64', '/api/crexil/material/', '/api/nodes/', '/outputs/', '/files/', '/thumb',
                  'http://', 'https://', '.wav', '.mp3', '.png', '.glb', '/home/', '/tmp/', '/mnt/')


def server_over(client=None, ceiling=0):
    door = crexil_mcp.Door('http://127.0.0.1', 'Tester', ceiling, client=client, poll_s=0.01)
    return crexil_mcp.build_server(door)


async def ok(session, name, arguments=None):
    result = await session.call_tool(name, arguments or {})
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_the_tools_are_the_agents_actions_only_with_their_schemas_and_hints():
    async with Client(server_over()) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == TOOLS                       # no pick, reveal, delivery, limits or voice session
    for name, tool in tools.items():
        assert tool.annotations.read_only_hint is (name in READS)
        assert tool.output_schema and tool.description
    assert tools['crexil_submit_run'].annotations.open_world_hint is True
    prepare = tools['crexil_prepare_run'].input_schema
    assert set(prepare['required']) >= {'project_id', 'action', 'prompt', 'engines', 'spending_limit_usd'}
    assert 'confirm_reserved_usd' in tools['crexil_submit_run'].input_schema['required']


async def test_a_client_runs_the_whole_voice_journey_through_the_protocol(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _), Client(server_over(client)) as session:
        engines = await ok(session, 'crexil_list_engines', {'payload': 'audio', 'action': 'voice-design'})
        engine = next(e['id'] for e in engines['engines'] if e['id'] == f'audio:voice-design:{LOCAL}')
        made = await ok(session, 'crexil_create_project', {'name': 'Dwarf blacksmith', 'brief': 'A forge-level smith'})
        prepared = await ok(session, 'crexil_prepare_run', {
            'project_id': made['project_id'], 'action': 'voice-design', 'prompt': 'A gruff dwarf blacksmith, low and warm',
            'engines': [{'id': engine, 'count': 2, 'controls': {'preview_text': PREVIEW}}], 'spending_limit_usd': 0})
        review = prepared['review']
        assert review['ready'] and review['reserved_usd'] == 0
        submitted = await ok(session, 'crexil_submit_run', {
            'run_id': prepared['run_id'], 'expected_revision': review['revision'],
            'review_token': review['review_token'], 'confirm_reserved_usd': review['reserved_usd']})
        run = await ok(session, 'crexil_get_run', {'run_id': prepared['run_id'], 'wait_seconds': 10})
        assert run['settled'] and {c['state'] for c in run['cells']} == {'landed'}
        take = run['cells'][0]['takes'][0]['take_id']
        proposed = await ok(session, 'crexil_propose', {
            'project_id': made['project_id'], 'take_ids': [take], 'reason': 'Warmest growl'})
        decisions = await ok(session, 'crexil_get_decisions', {'project_id': made['project_id']})
        project = await ok(session, 'crexil_get_project', {'project_id': made['project_id']})
    results = [engines, made, prepared, submitted, run, proposed, decisions, project]
    assert not {m for m in MATERIAL_MARKS if m in json.dumps(results)}
    assert proposed['author'] == 'agent:Tester' and decisions['records'][0]['author'] == 'agent:Tester'
    assert project['runs'][0]['picked'] == 0                  # a proposal never picks


async def test_the_engine_list_is_compact_unless_the_call_names_a_payload_or_an_action(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _), Client(server_over(client)) as session:
        everything = await ok(session, 'crexil_list_engines')
        voices = await ok(session, 'crexil_list_engines', {'action': 'voice-design'})
        tools = {t.name: t for t in (await session.list_tools()).tools}
    compact = {'id', 'label', 'actions', 'produces', 'availability', 'reasons', 'price_usd', 'price_unit'}
    assert everything['engines'] and all(set(engine) == compact for engine in everything['engines'])
    assert voices['engines'] and all(engine['controls'] and engine['data_destination'] for engine in voices['engines'])
    schema = tools['crexil_list_engines'].output_schema
    engine = schema['$defs']['Engine']
    assert set(engine['required']) == compact and 'controls' in engine['properties']   # details stay optional


async def test_a_refusal_comes_back_as_a_tool_error_that_keeps_its_kind(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _), Client(server_over(client)) as session:
        made = await ok(session, 'crexil_create_project', {'name': 'Dwarf blacksmith'})
        over = await session.call_tool('crexil_prepare_run', {
            'project_id': made['project_id'], 'action': 'sfx', 'prompt': 'Hammer on an anvil',
            'engines': [{'id': 'audio:sfx:elevenlabs-sfx'}], 'spending_limit_usd': 1})
        unknown = await session.call_tool('crexil_get_run', {'run_id': 'nothing-here'})
    assert over.is_error and '(kind: over_door_ceiling)' in over.content[0].text
    assert 'Nothing was sent' in over.content[0].text and '--max-run-usd' in over.content[0].text
    assert unknown.is_error and '(kind: not_found)' in unknown.content[0].text


async def test_the_script_serves_over_stdio_as_claude_code_launches_it():
    params = StdioServerParameters(command=sys.executable,
                                   args=[str(SCRIPT), '--url', 'http://127.0.0.1:9', '--agent-name', 'Stdio'])
    async with Client(params) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
        status = await ok(session, 'crexil_status')
    assert tools == TOOLS
    assert status == {'reachable': False, 'preview': False, 'agent': 'agent:Stdio', 'door_max_run_usd': 0.0,
                      'connections': []}
