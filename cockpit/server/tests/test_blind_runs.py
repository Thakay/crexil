"""Blind runs keep every engine out of every read until an authored reveal, and the
decision record exports choices with their authors and never any audio."""

import asyncio
import json

import pytest

from app import blind, config, preferences
from fakes import FakeAudioExecutor
from test_api_v4 import store
from test_audio_contract import PREVIEW, booted_audio, free_design_row, operation, submit
from test_crexil import command, project
from test_crexil_execution import reviewed

pytestmark = pytest.mark.anyio

LOCAL, HOSTED = 'qwen3-tts', 'test-elevenlabs-design'
PROMPT = 'A gruff dwarf blacksmith, low and warm'
MODELS = ('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign', 'eleven_multilingual_ttv_v2')


@pytest.fixture(autouse=True)
def isolated_delivery_target(tmp_path, monkeypatch):
    target = tmp_path / 'promote'
    monkeypatch.setattr(config, 'PROMOTE_DIR', target)
    monkeypatch.setattr(config, 'PROMOTE_ROOTS', [target])


def casting(tmp_path, monkeypatch, **kw):
    return booted_audio(tmp_path, monkeypatch, rows=(free_design_row(),), **kw)


async def prepared(client, wid, *, blind_run=True, engines=(LOCAL, HOSTED)):
    """One voice-design direction across engines, with one attempt each."""
    operations = []
    for engine in engines:
        op = await operation(client, f'audio:voice-design:{engine}')
        operations.append({'operation_id': op['id'], 'revision': op['revision'], 'count': 1,
                           'controls': {'preview_text': PREVIEW}})
    action = (await operation(client, f'audio:voice-design:{engines[0]}'))['actions'][0]
    setup = {'action': action, 'prompt': PROMPT, 'references': [], 'operations': operations,
             'spending_policy': 'hard_cap', 'blind': blind_run}
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title='Casting', setup=setup))
    assert response.status_code == 201, response.text
    return response.json()


async def cast(client, wid, **kw):
    direction = await prepared(client, wid, **kw)
    turn, takes = await submit(client, direction)
    return direction, turn, takes


async def identity_terms(turn):
    """Every string that would tell a hidden take's engine: engine ids, providers,
    models, and the job and voice references the engines reported."""
    terms = {LOCAL, HOSTED, 'elevenlabs', 'voice-runner', *MODELS}
    terms |= {slot['job_ref'] for slot in turn['slots'] if slot['job_ref']}
    for facts in (await store().audio_facts(turn['workspace_id'])).values():
        voice = facts.get('voice') or {}
        terms |= {value for key, value in voice.items() if key.endswith('_id') and value}
    return terms


def leaks(value, terms) -> set[str]:
    text = json.dumps(value)
    return {term for term in terms if term in text}


async def revision_of(client, wid, did):
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    return next(d for d in snapshot['directions'] if d['id'] == did)['revision']


async def test_a_blind_run_names_no_engine_on_any_read_until_its_authored_reveal(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'])
        hidden = {take['id'] for take in takes}
        slots = {slot['id'] for slot in turn['slots']}
        terms = await identity_terms(turn)
        assert len(hidden) == 6 and len(terms) > 6

        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        nodes = [n for n in snapshot['nodes'] if n['id'] in hidden]
        assert nodes and not leaks(nodes, terms)
        for node in nodes:
            assert node['identity_hidden'] is True and node['engine'] == blind.HIDDEN_ENGINE
            assert node['seed'] is node['committed_usd'] is node['charged_usd'] is node['duration_ms'] is None
            assert node['material']['filename'] == 'hidden-take.wav' and 'terms' not in node['material']['origin']
            assert node['retained_files'] == []
            sound = node['audio']
            assert sound['voice'] is sound['alignment'] is sound['sample_rate'] is sound['container'] is None
            assert sound['duration_s'] > 0 and sound['peaks'] and sound['text'] == PREVIEW
        cells = next(t for t in snapshot['turns'] if t['id'] == turn['id'])['slots']
        assert sorted(cell['pos'] for cell in cells) == list(range(len(cells)))
        assert not leaks(cells, terms) and all(cell['identity_hidden'] and cell['committed_usd'] is None
                                               and cell['started_at'] is None for cell in cells)
        groups = [g for g in snapshot['execution_groups'] if g['slot_id'] in slots]
        assert groups and not leaks(groups, terms) and all(g['identity_hidden'] and g['job_ref'] is None for g in groups)
        assert all(member['filename'].startswith('output-') for g in groups for member in g['outputs'])
        assert next(r for r in snapshot['blind_runs'] if r['turn_id'] == turn['id'])['revealed'] is False
        assert snapshot['charged_usd'] is not None and snapshot['accounting']          # totals stay (law 22)

        legacy = (await client.get(f"/api/workspaces/{p['id']}/nodes")).json()
        assert not leaks([n for n in legacy if n['id'] in hidden], terms)
        detail = (await client.get(f"/api/workspaces/{p['id']}")).json()
        assert not leaks(next(t for t in detail['turns'] if t['id'] == turn['id'])['slots'], terms)
        events = (await client.get('/api/events', params={'workspace_id': p['id']})).json()['events']
        per_take = [e for e in events if e['turn_id'] == turn['id'] and (e['slot_id'] or e['node_id'])]
        assert per_take and not leaks(per_take, terms)
        assert all(e['payload']['identity_hidden'] and e['charged_usd'] is None for e in per_take)
        for nid in hidden:
            found = (await client.get(f'/api/crexil/material/{nid}/record')).json()
            assert found['identity_hidden'] is True and found['request'] is found['submitted'] is None
            assert found['controls'] == {} and not leaks(found, terms)
            assert (await client.get(f'/api/crexil/material/{nid}/audio')).status_code == 200   # still playable
        for term in (LOCAL, HOSTED, 'elevenlabs', 'voice-runner', 'take-1'):
            searched = (await client.get(f"/api/crexil/projects/{p['id']}/record", params={'q': term})).json()
            assert not hidden & set(searched['material_ids']), term

        some = sorted(hidden)[0]
        aside = (await client.post(f'/api/nodes/{some}/set-aside')).json()
        assert aside['identity_hidden'] is True and not leaks(aside, terms)
        tray = (await client.get(f"/api/workspaces/{p['id']}/tray")).json()
        assert tray['nodes'] and not leaks(tray['nodes'], terms)
        restored = (await client.post(f'/api/nodes/{some}/restore')).json()
        assert restored['identity_hidden'] is True and not leaks(restored, terms)
        for answer in (await client.get(f'/api/nodes/{some}/promote-preview'),
                       await client.post(f'/api/nodes/{some}/promote')):
            assert answer.status_code == 409 and 'blind run' in answer.json()['detail']
        config.PROMOTE_DIR.mkdir(parents=True, exist_ok=True)
        review = (await client.post(f"/api/crexil/projects/{p['id']}/deliveries/review",
                                    json={'node_ids': [some], 'destination': str(config.PROMOTE_DIR)})).json()
        assert review['ready'] is False and 'blind run' in review['items'][0]['error']
        assert not leaks(review, terms)
        csv = (await client.get(f"/api/crexil/projects/{p['id']}/deliveries.csv")).text
        assert not hidden & {cell for line in csv.splitlines() for cell in line.split(',')}

        chosen = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=await revision_of(client, p['id'], direction['id']), node_ids=[some], reason='Warmest growl'))
        assert chosen.status_code == 200, chosen.text
        exported = (await client.get(f"/api/crexil/projects/{p['id']}/preferences")).json()
        assert not leaks(exported, terms)
        line = exported['records'][-1]
        assert line['blind'] is True and line['chosen'] == [some] and len(line['candidates']) == 6
        assert {c['engine'] for c in line['candidates']} == {blind.HIDDEN_ENGINE}

        body = command(reason='Done listening')
        revealed = await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=body)
        assert revealed.status_code == 200, revealed.text
        view = revealed.json()
        assert view['revealed'] is True and view['revealed_by'] == {'kind': 'human', 'name': 'Artist'}
        again = await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command(reason='Once more'))
        assert again.status_code == 200 and again.json()['revealed_at'] == view['revealed_at']
        assert (await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=body)).json() == view   # replay
        reveals = [e for e in await store().events_since(0, workspace_id=p['id']) if e['type'] == 'run.revealed']
        assert len(reveals) == 1 and reveals[0]['payload'] == {'actor': {'kind': 'human', 'name': 'Artist'},
                                                               'reason': 'Done listening'}

        after = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        shown = [n for n in after['nodes'] if n['id'] in hidden]
        assert {n['engine'] for n in shown} == {LOCAL, HOSTED} and not any(n['identity_hidden'] for n in shown)
        assert (await client.get(f'/api/crexil/material/{some}/record')).json()['request'] is not None
        line = (await client.get(f"/api/crexil/projects/{p['id']}/preferences")).json()['records'][-1]
        assert line['blind'] is True                       # it was decided blind; that stays true
        assert {c['engine'] for c in line['candidates']} == {LOCAL, HOSTED}
        assert {c['provider'] for c in line['candidates']} == {'voice-runner', 'elevenlabs'}
        assert {c['model'] for c in line['candidates']} == set(MODELS)
        assert all(c['cost_micro_usd'] == 0 for c in line['candidates'])
        preview = await client.get(f'/api/nodes/{some}/promote-preview')
        assert 'blind run' not in preview.text
        review = (await client.post(f"/api/crexil/projects/{p['id']}/deliveries/review",
                                    json={'node_ids': [some], 'destination': str(config.PROMOTE_DIR)})).json()
        assert review['items'][0]['error'] is None
        local = {n['id'] for n in shown if n['engine'] == LOCAL}
        found = (await client.get(f"/api/crexil/projects/{p['id']}/record", params={'q': LOCAL})).json()
        assert local == hidden & set(found['material_ids'])


async def test_reveal_names_its_author_and_refuses_runs_that_are_not_blind(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        _, plain, _ = await cast(client, p['id'], blind_run=False, engines=(LOCAL,))
        for tid in (plain['id'], 'missing'):
            answer = await client.post(f'/api/crexil/turns/{tid}/reveal', json=command())
            assert answer.status_code == 404 and answer.json()['detail'] == 'This run is not a blind run in this project.'
        _, turn, _ = await cast(client, p['id'], engines=(LOCAL,))
        agent = {'kind': 'agent', 'name': 'Scout'}
        view = (await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command(actor=agent))).json()
        assert view['revealed_by'] == agent and view['turn_id'] == turn['id']


async def test_choices_and_assessments_made_while_blind_record_it(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'])
        pick = takes[0]['id']

        async def select(reason):
            answer = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
                revision=await revision_of(client, p['id'], direction['id']), node_ids=[pick], reason=reason))
            assert answer.status_code == 200, answer.text

        await select('Warmest growl')
        assessed = await client.post(f"/api/crexil/projects/{p['id']}/assessments",
                                     json=command(node_ids=[takes[1]['id']], reason='Too bright'))
        assert assessed.status_code == 201, assessed.text
        await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command())
        await select('Still the warmest')
        events = await store().events_since(0, workspace_id=p['id'])
        changed = [e['payload'] for e in events if e['type'] == 'direction.changed' and 'selection' in e['payload']['changes']]
        assert changed[0]['blind'] is True and changed[0]['blind_turn_ids'] == [turn['id']]
        assert 'blind' not in changed[-1]
        recorded = next(e['payload'] for e in events if e['type'] == 'decision.recorded')
        assert recorded['blind'] is True and recorded['blind_turn_ids'] == [turn['id']]


async def test_the_decision_record_is_attributed_and_holds_no_audio(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'], blind_run=False, engines=(LOCAL,))
        agent = {'kind': 'agent', 'name': 'Scout'}
        chosen = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=await revision_of(client, p['id'], direction['id']), node_ids=[takes[2]['id']],
            reason='Warmest growl', actor=agent))
        assert chosen.status_code == 200, chosen.text
        assert (await client.post(f"/api/crexil/projects/{p['id']}/assessments",
                                  json=command(node_ids=[takes[0]['id']], reason='Too bright'))).status_code == 201

        exported = (await client.get(f"/api/crexil/projects/{p['id']}/preferences")).json()
        assert exported['purpose'] == preferences.PURPOSE and exported['project_id'] == p['id']
        selection, assessment = exported['records']
        assert set(selection) == {'decision_id', 'project_id', 'run_id', 'direction_id', 'verb', 'brief', 'candidates',
                                  'chosen', 'reason', 'author', 'blind', 'decided_at'}
        assert selection['author'] == agent and selection['reason'] == 'Warmest growl' and selection['blind'] is False
        assert selection['chosen'] == [takes[2]['id']] and selection['run_id'] == turn['id']
        assert selection['brief'] == PROMPT and selection['verb'] == 'voice-design'
        assert [c['position'] for c in selection['candidates']] == [0, 1, 2]
        assert {c['node_id'] for c in selection['candidates']} == {take['id'] for take in takes}
        assert all(set(c) == {'node_id', 'position', 'engine', 'provider', 'model', 'seed', 'cost_micro_usd'}
                   and c['engine'] == LOCAL and c['provider'] == 'voice-runner' and c['model'] == MODELS[0]
                   for c in selection['candidates'])
        assert assessment['chosen'] == [] and [c['node_id'] for c in assessment['candidates']] == [takes[0]['id']]
        assert assessment['author'] == {'kind': 'human', 'name': 'Artist'} and assessment['reason'] == 'Too bright'
        text = json.dumps(exported)
        assert str(tmp_path) not in text and '.wav' not in text and 'base64' not in text and 'output_path' not in text

        lines = await client.get(f"/api/crexil/projects/{p['id']}/preferences.jsonl")
        assert lines.status_code == 200 and lines.headers['content-type'].startswith('application/x-ndjson')
        assert [json.loads(line) for line in lines.text.splitlines()] == exported['records']
        assert (await client.get('/api/crexil/projects/missing/preferences')).status_code == 404


async def test_record_search_matches_words_never_key_names(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        _, _, takes = await cast(client, p['id'], blind_run=False, engines=(LOCAL,))
        assert (await client.post(f"/api/crexil/projects/{p['id']}/assessments",
                                  json=command(node_ids=[takes[0]['id']], reason='Brass tone'))).status_code == 201

        async def search(q):
            return (await client.get(f"/api/crexil/projects/{p['id']}/record", params={'q': q})).json()

        for key_or_null in ('reason', 'null', 'human', 'actor', 'true'):
            assert (await search(key_or_null))['decisions'] == [], key_or_null
        assert len((await search('artist'))['decisions']) == 1
        assert len((await search('brass tone'))['decisions']) == 1
        assert len((await search('tone brass'))['decisions']) == 1          # every word, any order
        assert {t['id'] for t in takes} <= set((await search('blacksm'))['material_ids'])   # indexed prompt prefix
        assert not {t['id'] for t in takes} & set((await search('lacksmith'))['material_ids'])


async def test_blind_queue_rows_name_no_engine_or_cost_while_takes_run(tmp_path, monkeypatch):
    gate = asyncio.Event()
    held = {'voice-runner': FakeAudioExecutor('voice-runner', 'local', gate=gate),
            'elevenlabs': FakeAudioExecutor('elevenlabs', 'external', gate=gate)}
    async with casting(tmp_path, monkeypatch, executors=held) as (client, _):
        p = await project(client)
        direction = await prepared(client, p['id'])
        check = await reviewed(client, direction)
        submitted = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                      json=command(revision=check['revision'], review_token=check['token']))
        tid = submitted.json()['turn_id']
        for _ in range(300):
            queue = (await client.get('/api/queue')).json()
            rows = [r for lane in queue['lanes'].values() for r in lane['slots']] + queue['held'] + queue['late']
            if len([r for r in rows if r['turn_id'] == tid]) == 2:
                break
            await asyncio.sleep(0.01)
        mine = [r for r in rows if r['turn_id'] == tid]
        assert len(mine) == 2
        assert all(r['identity_hidden'] and r['engine'] == blind.HIDDEN_ENGINE and r['committed_usd'] is None
                   and r['charged_usd'] is None and r['bench'] is None for r in mine)
        assert not leaks(mine, {LOCAL, HOSTED, 'elevenlabs', 'voice-runner'})
        gate.set()


def test_arrange_decides_one_order_from_system_randomness():
    specs = [{'engine': f'e{i}'} for i in range(6)]
    orders = {tuple(spec['engine'] for spec in blind.arrange(specs)) for _ in range(40)}
    assert len(orders) > 1 and all(sorted(order) == [s['engine'] for s in specs] for order in orders)
    assert [s['engine'] for s in specs] == [f'e{i}' for i in range(6)]     # the caller's list is untouched


def test_the_masking_rule_keeps_wire_shapes_and_plain_causes():
    event = blind.mask('event', {'seq': 1, 'charged_usd': 0.25, 'turn_id': 't', 'payload': {
        'engine': 'x', 'cause': 'X refused the key', 'kind': 'dead_key', 'actor': {'kind': 'human', 'name': 'Kay'},
        'nested': [{'provider': 'x', 'model_id': 'm', 'keep': 1}]}})
    assert event['charged_usd'] is None and event['payload']['identity_hidden'] is True
    assert event['payload']['nested'] == [{'keep': 1}] and event['payload']['actor']['name'] == 'Kay'
    assert event['payload']['cause'] == blind.plain_cause('dead_key') and 'X ' not in event['payload']['cause']
    cell = blind.mask('slot', {'engine': 'x', 'cause': 'X timed out', 'kind': 'timeout', 'pos': 3, 'seed': 7})
    assert cell == {'engine': blind.HIDDEN_ENGINE, 'cause': blind.plain_cause('timeout'), 'kind': 'timeout',
                    'pos': 3, 'seed': None, 'identity_hidden': True}
    assert blind.may_serve({'node_id': 'n'}) and not blind.may_serve({'node_id': None})
    assert blind.mask('queue', {'engine': 'x', 'bench': 'engine:x'})['bench'] is None
    assert blind.mask('queue', {'engine': 'x', 'bench': 'lane:local'})['bench'] == 'lane:local'
