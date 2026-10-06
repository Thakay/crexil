"""The audio contract: engines, routing, review, landing, blind runs and the v13 store."""

import asyncio
import json
import math
import shutil
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from app import audio, blind, config, main, wiring
from app.audio_executors import NotBuiltExecutor
from app.db import Database
from app.executors import EngineFailure
from fakes import FakeAudioExecutor, tone_wav
from test_api_v4 import hermetic_env, make_fakes, settle_turn, store
from test_crexil import command, imported, project
from test_crexil_execution import reviewed

pytestmark = pytest.mark.anyio

REPO = Path(__file__).resolve().parents[3]
KEY = 'test-elevenlabs-key-0123456789'
PREVIEW = 'Steel remembers every strike, and so do I. ' * 3


def free_design_row():
    """A test-only copy of ElevenLabs Voice Design with no price, so takes that
    carry ElevenLabs' terms can land before audio prices are computed."""
    matrix = json.loads((REPO / 'configs/engines.json').read_text())
    row = dict(next(r for r in matrix['engines'] if r['id'] == 'elevenlabs-voice-design'))
    row.update(id='test-elevenlabs-design', name='Test ElevenLabs design',
               tariffs={'voice-design': {'unit': 'attempt', 'micro_usd': 0}})
    return row


@asynccontextmanager
async def booted_audio(tmp_path, monkeypatch, *, enable=True, rows=(), key=KEY, executors=None,
                       raise_app_exceptions=True):
    hermetic_env(tmp_path, monkeypatch)
    configs = tmp_path / 'configs'
    shutil.copytree(REPO / 'configs', configs)
    matrix = json.loads((configs / 'engines.json').read_text())
    for row in matrix['engines']:
        if row.get('payload') == 'audio':
            row['enabled'] = enable
    matrix['engines'] += [dict(row, enabled=True) for row in rows]
    (configs / 'engines.json').write_text(json.dumps(matrix))
    monkeypatch.setattr(config, 'CONFIGS_DIR', configs)
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', key)
    monkeypatch.setattr(config, 'VOICE_RUNNER_URL', 'http://127.0.0.1:9')
    fakes = make_fakes()
    fakes['voice-runner'] = FakeAudioExecutor('voice-runner', 'local')
    fakes['elevenlabs'] = FakeAudioExecutor('elevenlabs', 'external')
    fakes.update(executors or {})
    monkeypatch.setattr(wiring, 'EXECUTOR_FACTORY', lambda orc: list(fakes.values()))
    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=raise_app_exceptions)
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as client:
            yield client, fakes


async def operation(client, op_id):
    ops = (await client.get('/api/crexil/operations')).json()
    return next(o for o in ops if o['id'] == op_id)


async def draft(client, wid, op_id, prompt, *, controls=None, refs=(), count=1, policy='hard_cap', blind_run=False):
    op = await operation(client, op_id)
    setup = {'action': op['actions'][0], 'prompt': prompt, 'references': [{'node_id': n} for n in refs],
             'operations': [{'operation_id': op_id, 'revision': op['revision'], 'count': count,
                             'controls': controls or {}}],
             'spending_policy': policy, 'blind': blind_run}
    response = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title=op['label'], setup=setup))
    assert response.status_code == 201, response.text
    return response.json()


async def submit(client, direction, until=frozenset({'landed'})):
    check = await reviewed(client, direction)
    assert check['ready'], check['errors']
    response = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                 json=command(revision=check['revision'], review_token=check['token']))
    assert response.status_code == 201, response.text
    turn = await settle_turn(response.json()['turn_id'], until=set(until))
    nodes = [n for n in await store().list_nodes(direction['workspace_id']) if n['turn_id'] == turn['id']]
    return turn, nodes


async def designed(client, wid, engine='qwen3-tts', **fields):
    direction = await draft(client, wid, f'audio:voice-design:{engine}', 'A gruff dwarf blacksmith, low and warm',
                            controls={'preview_text': PREVIEW}, **fields)
    return await submit(client, direction)


# --- engines and routing ---

def test_audio_rows_are_well_formed_disabled_and_route_to_their_provider():
    rows = {r['id']: r for r in json.loads((REPO / 'configs/engines.json').read_text())['engines']
            if r.get('payload') == 'audio'}
    assert set(rows) == {'elevenlabs-voice-design', 'elevenlabs-v4', 'elevenlabs-sfx', 'qwen3-tts'}
    for row in rows.values():
        assert row['enabled'] is False                       # the integrator enables an engine
        assert audio.PROVIDERS[row['provider']] == row['lane']
        assert set(row['verbs']) <= set(audio.VERBS)
        assert set(row.get('params', {})) <= set(row['verbs'])
        for verb in row['verbs']:
            price = row['tariffs'][verb]
            assert price['unit'] in ('attempt', '1k_characters', 'second', 'generation')
            assert type(price['micro_usd']) is int and price['micro_usd'] >= 0
            assert row['models'][verb]
    assert all(rows[i]['terms']['allows'] == ['elevenlabs'] for i in rows if i.startswith('elevenlabs'))


def test_every_engine_kind_routes_by_its_declared_source(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIGS_DIR', REPO / 'configs')
    expected = {'elevenlabs-v4': ('elevenlabs', 'external'), 'elevenlabs-sfx': ('elevenlabs', 'external'),
                'qwen3-tts': ('voice-runner', 'local'), 'google/gemini-2.5-flash-image': ('openrouter', 'external'),
                'trellis2-4b': ('mesh', 'local'), 'gltfpack': ('mesh', 'local'), 'raw-sdxl': ('comfy', 'local')}
    assert {e: (wiring.route_engine(e), wiring.lane_of(e)) for e in expected} == expected


# --- the operation catalogue ---

async def test_operations_publish_typed_audio_contracts(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
        design, speak, sfx = (ops['audio:voice-design:qwen3-tts'], ops['audio:speak:elevenlabs-v4'],
                              ops['audio:sfx:elevenlabs-sfx'])
        assert design['availability'] == 'unverified' and design['reasons'] == []
        assert design['output_payload'] == 'audio' and design['takes_per_attempt'] == 3
        assert design['price_unit'] == 'attempt' and design['tariff']['micro_usd'] == 0
        assert design['maximum_usd_per_attempt'] == 0 and design['provider'] == 'voice-runner'
        assert [i['key'] for i in design['inputs']] == ['preview_text', 'language', 'seed']
        assert speak['tariff'] == {'unit': '1k_characters', 'micro_usd': 80000, 'measure': 'prompt',
                                   'source': 'https://elevenlabs.io/pricing/api', 'read_on': '2026-10-05'}
        assert speak['sources'][0]['role'] == 'voice' and speak['sources'][0]['accepts'] == ['audio']
        assert 'speaks only with voices designed or saved at ElevenLabs' in speak['sources'][0]['restrictions'][0]
        local_speak = ops['audio:speak:qwen3-tts']
        assert local_speak['sources'][0]['restrictions'] == ["ElevenLabs' terms do not allow its audio as input to another AI model."]
        assert sfx['price_unit'] == 'second' and sfx['tariff']['micro_usd'] == 2000
        assert sfx['maximum_usd_per_attempt'] is None and 'not a hard cap' in ' '.join(sfx['limitations'])
        assert 'seed' not in [i['key'] for i in sfx['inputs']]
        assert any("ElevenLabs' terms" in note for note in ops['audio:voice-design:elevenlabs-voice-design']['limitations'])
        hosted = [o for o in ops.values() if o['provider'] == 'openrouter']
        assert hosted and all(o['price_unit'] == 'image' and isinstance(o['pinned'], bool) for o in hosted)
        mesh = ops['mesh:texture:hunyuan3d-21']
        assert mesh['sources'] == [{'role': 'shape', 'accepts': ['3d'], 'restrictions': []},
                                   {'role': 'appearance', 'accepts': ['image'], 'restrictions': []}]


async def test_audio_availability_says_why_in_plain_words(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch, enable=False) as (client, _):
        op = await operation(client, 'audio:speak:elevenlabs-v4')
        assert op['availability'] == 'unavailable'
        assert op['reasons'] == ['This engine is not enabled in configs/engines.json.']
    (tmp_path / 'b').mkdir()
    (tmp_path / 'c').mkdir()
    async with booted_audio(tmp_path / 'b', monkeypatch, key='') as (client, _):
        op = await operation(client, 'audio:speak:elevenlabs-v4')
        assert op['reasons'] == ['Needs an ElevenLabs key. Set ELEVENLABS_API_KEY in .env, then restart.']
    unbuilt = {'voice-runner': NotBuiltExecutor('voice-runner', 'local')}
    async with booted_audio(tmp_path / 'c', monkeypatch, executors=unbuilt) as (client, _):
        op = await operation(client, 'audio:voice-design:qwen3-tts')
        assert op['reasons'] == ['The local voice runner adapter is not built yet. Nothing was sent.']


# --- landing measured audio ---

async def test_local_voice_design_lands_three_measured_takes(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', 'A gruff dwarf blacksmith',
                                controls={'preview_text': PREVIEW, 'seed': {'mode': 'fixed', 'value': 7}})
        check = await reviewed(client, direction)
        assert check['ready'] and check['maximum_usd'] == 0 and check['reserved_micro_usd'] == 0
        turn, nodes = await submit(client, direction)
        assert len(nodes) == 3 and {n['payload'] for n in nodes} == {'audio'}
        assert {n['verb'] for n in nodes} == {'voice-design'} and sorted(n['output_index'] for n in nodes) == [0, 1, 2]
        slot = turn['slots'][0]
        assert slot['dials']['crexil']['spending_policy'] == 'hard_cap'
        assert slot['dials']['crexil']['output_payload'] == 'audio'
        envelope = fakes['voice-runner'].requests[0].external
        assert envelope['transport'] == 'audio' and envelope['seed'] == 7 and envelope['takes'] == 3
        assert envelope['model_id'] == 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
        assert envelope['controls'] == {'preview_text': PREVIEW, 'language': 'English'}
        assert envelope['reserved_micro_usd'] == 0 and envelope['measures'] == {'attempts': 1}
        prepared = [e for e in await store().slot_events(slot['id']) if e['type'] == 'slot.prepared']
        assert prepared[-1]['payload']['payload'] == 'audio'
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        takes = [n for n in snapshot['nodes'] if n['turn_id'] == turn['id']]
        for node in takes:
            facts = node['audio']
            assert facts['container'] == 'wav' and facts['codec'] == 'pcm_s16le'
            assert facts['sample_rate'] == 24000 and facts['channels'] == 1 and facts['duration_s'] == 0.5
            assert 0 < len(facts['peaks']) <= audio.PEAK_BINS and max(facts['peaks']) == pytest.approx(0.3, abs=0.01)
            assert facts['loudness_lufs'] == pytest.approx(-13.5, abs=1.5)
            assert facts['text'] == PREVIEW and facts['voice']['kind'] == 'designed'
            assert node['material']['media_type'] == 'audio/wav' and 'terms' not in node['material']['origin']
            assert node['identity_hidden'] is None
        played = await client.get(f"/api/crexil/material/{takes[0]['id']}/audio")
        assert played.status_code == 200 and played.headers['content-type'] == 'audio/wav'
        assert played.content == Path(next(n for n in nodes if n['id'] == takes[0]['id'])['output_path']).read_bytes()
        image = (await imported(client, p['id']))['node']
        assert (await client.get(f"/api/crexil/material/{image['id']}/audio")).status_code == 404


async def test_audio_that_cannot_be_measured_fails_collection_and_keeps_its_bytes(tmp_path, monkeypatch):
    broken = {'voice-runner': FakeAudioExecutor('voice-runner', 'local', data=b'RIFF-not-really-a-wav')}
    async with booted_audio(tmp_path, monkeypatch, executors=broken) as (client, _):
        p = await project(client)
        direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', 'A voice',
                                controls={'preview_text': PREVIEW})
        turn, nodes = await submit(client, direction, until={'failed'})
        slot = turn['slots'][0]
        assert nodes == [] and slot['kind'] == 'collection'
        assert 'declared as audio but could not be measured' in slot['detail'] + slot['cause']
        group = (await client.get(f"/api/crexil/projects/{p['id']}")).json()['execution_groups'][0]
        kept = [o for o in group['outputs'] if o['sha256']]
        assert kept and all(o['node_id'] is None and o['error'] for o in kept)   # law 7: the bytes stay


# --- speech, voices and provider terms ---

async def test_speech_takes_a_voice_take_and_refuses_restricted_or_wrong_sources(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch, rows=[free_design_row()]) as (client, fakes):
        p = await project(client)
        _, local_takes = await designed(client, p['id'])
        voice = local_takes[0]
        direction = await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Bring me the ore.', refs=[voice['id']])
        turn, lines = await submit(client, direction)
        assert len(lines) == 1 and lines[0]['parents'] == [voice['id']] and lines[0]['verb'] == 'speak'
        request = fakes['voice-runner'].requests[-1]
        assert request.external['voice']['source_node_id'] == voice['id']
        assert request.external['voice']['text'] == PREVIEW and request.external['voice']['recorded']['kind'] == 'designed'
        assert [s.sha256 for s in request.sources] == [request.external['voice']['sha256']]
        assert request.sources[0].path == voice['output_path']

        _, restricted = await designed(client, p['id'], engine='test-elevenlabs-design')
        origin = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        terms = next(n for n in origin['nodes'] if n['id'] == restricted[0]['id'])['material']['origin']['terms']
        assert terms['allows'] == ['elevenlabs']
        refused = await reviewed(client, await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Bring me the ore.',
                                                     refs=[restricted[0]['id']]))
        assert not refused['ready']
        assert "ElevenLabs' terms do not allow its audio as input to another AI model." in ' '.join(refused['errors'])

        wrong_voice = await reviewed(client, await draft(client, p['id'], 'audio:speak:elevenlabs-v4', 'Ore.',
                                                         refs=[voice['id']], policy='published_tariff'))
        assert 'speaks only with voices designed or saved at ElevenLabs' in ' '.join(wrong_voice['errors'])
        image = (await imported(client, p['id']))['node']
        not_audio = await reviewed(client, await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Ore.', refs=[image['id']]))
        assert 'needs an audio take as its voice' in ' '.join(not_audio['errors'])
        no_voice = await reviewed(client, await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Ore.'))
        assert 'takes one voice take' in ' '.join(no_voice['errors'])
        too_long = await reviewed(client, await draft(client, p['id'], 'audio:speak:elevenlabs-v4', 'x' * 10001,
                                                      refs=[restricted[0]['id']], policy='published_tariff'))
        assert 'at most 10000 characters' in ' '.join(too_long['errors'])


# --- blind runs ---

def masking(seen):
    def mask(kind, item):
        seen.append(kind)
        item = dict(item)
        if kind == 'event':
            item['payload'] = {**item['payload'], 'identity_hidden': True}
        else:
            item['identity_hidden'] = True
            if 'engine' in item:
                item['engine'] = blind.HIDDEN_ENGINE
        return item
    return mask


async def test_blind_runs_are_recorded_arranged_and_every_read_passes_the_mask(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(blind, 'mask', masking(seen))
    monkeypatch.setattr(blind, 'arrange', lambda specs: list(reversed(specs)))
    monkeypatch.setattr(blind, 'selection_facts', lambda *_: asyncio.sleep(0, result={'blind': True}))
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        _, seen_takes = await designed(client, p['id'])
        direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', 'A gruff dwarf blacksmith',
                                controls={'preview_text': PREVIEW, 'seed': {'mode': 'fixed', 'value': 100}},
                                count=2, blind_run=True)
        turn, hidden = await submit(client, direction)
        assert [s['seed'] for s in sorted(turn['slots'], key=lambda s: s['pos'])] == [101, 100]   # arranged once
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        run = next(r for r in snapshot['blind_runs'] if r['turn_id'] == turn['id'])
        assert run['revealed'] is False and run['actor'] == {'kind': 'human', 'name': 'Artist'}
        by_turn = {n['id']: n for n in snapshot['nodes']}
        assert all(by_turn[n['id']]['identity_hidden'] and by_turn[n['id']]['engine'] == 'hidden' for n in hidden)
        assert all(by_turn[n['id']]['identity_hidden'] is None for n in seen_takes)
        slots = next(t for t in snapshot['turns'] if t['id'] == turn['id'])['slots']
        assert all(s['identity_hidden'] and s['engine'] == 'hidden' for s in slots)
        groups = [g for g in snapshot['execution_groups'] if g['slot_id'] in {s['id'] for s in slots}]
        assert groups and all(g['identity_hidden'] for g in groups)
        legacy = (await client.get(f"/api/workspaces/{p['id']}/nodes")).json()
        assert {n['id'] for n in legacy if n['identity_hidden']} == {n['id'] for n in hidden}
        detail = (await client.get(f"/api/workspaces/{p['id']}")).json()
        assert all(s['identity_hidden'] for s in next(t for t in detail['turns'] if t['id'] == turn['id'])['slots'])
        events = (await client.get('/api/events', params={'workspace_id': p['id']})).json()['events']
        assert events and all(bool(e['payload'].get('identity_hidden')) == (e['turn_id'] == turn['id']) for e in events)
        record = (await client.get(f"/api/crexil/material/{hidden[0]['id']}/record")).json()
        assert record['identity_hidden'] is True
        assert (await client.get(f"/api/crexil/material/{hidden[0]['id']}/audio")).status_code == 200
        current = await client.get(f"/api/crexil/projects/{p['id']}")
        chosen = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=next(d for d in current.json()['directions'] if d['id'] == direction['id'])['revision'],
            node_ids=[hidden[0]['id']], reason='Warmest growl'))
        assert chosen.status_code == 200, chosen.text
        changed = [e for e in await store().events_since(0, workspace_id=p['id']) if e['type'] == 'direction.changed']
        assert changed[-1]['payload']['blind'] is True and changed[-1]['payload']['reason'] == 'Warmest growl'
        await store().append_event('run.revealed', workspace_id=p['id'], turn_id=turn['id'],
                                   payload={'actor': {'kind': 'human', 'name': 'Artist'}})
        after = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        assert next(r for r in after['blind_runs'] if r['turn_id'] == turn['id'])['revealed_by'] == {'kind': 'human', 'name': 'Artist'}
        assert not any(n['identity_hidden'] for n in after['nodes'])
        assert {'node', 'slot', 'group', 'event', 'material_record'} <= set(seen)


async def test_blind_queue_reads_pass_the_mask_while_takes_run(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(blind, 'mask', masking(seen))
    gate = asyncio.Event()
    held = {'voice-runner': FakeAudioExecutor('voice-runner', 'local', gate=gate)}
    async with booted_audio(tmp_path, monkeypatch, executors=held) as (client, _):
        p = await project(client)
        direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', 'A voice',
                                controls={'preview_text': PREVIEW}, blind_run=True)
        check = await reviewed(client, direction)
        await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                          json=command(revision=check['revision'], review_token=check['token']))
        for _ in range(300):
            local = (await client.get('/api/queue')).json()['lanes']['local']['slots']
            if local:
                break
            await asyncio.sleep(0.01)
        assert local and all(s['identity_hidden'] and s['engine'] == 'hidden' for s in local)
        gate.set()


# --- refusals and defects ---

async def test_refusals_map_to_plain_statuses_and_defects_reach_the_failure_handler(tmp_path, monkeypatch):
    from app.crexil_store import Conflict
    async with booted_audio(tmp_path, monkeypatch, raise_app_exceptions=False) as (client, _):
        for raised, status in ((LookupError('This run could not be found.'), 404),
                               (Conflict('This run changed. Reload it.'), 409),
                               (ValueError('Write a reason first.'), 422),
                               (KeyError('input_images'), 500)):
            async def failing(*_args, exc=raised):
                raise exc
            monkeypatch.setattr(blind, 'reveal', failing)
            answer = await client.post('/api/crexil/turns/any/reveal', json=command())
            assert answer.status_code == status, answer.text
            detail = answer.json()['detail']
            assert ('internal error' in detail) if status == 500 else detail == str(raised)
            assert "'input_images'" not in detail


# --- setup ---

async def test_setup_lists_audio_connections_without_ever_showing_a_key(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        answer = await client.get('/api/crexil/setup')
        assert answer.status_code == 200 and KEY not in answer.text
        lines = {c['provider']: c for c in answer.json()['connections']}
        assert list(lines) == ['comfyui', 'openrouter', 'elevenlabs', 'voice-runner']
        assert lines['elevenlabs']['ok'] is True and lines['elevenlabs']['key_source'] == 'env'
        assert lines['voice-runner']['ok'] is False and 'Not answering' in lines['voice-runner']['detail']


# --- executor hooks ---

async def test_an_executor_permit_keeps_its_own_takes_queued(tmp_path, monkeypatch):
    capped = {'elevenlabs': FakeAudioExecutor('elevenlabs', 'external', delay_s=0.05, max_concurrency=1)}
    async with booted_audio(tmp_path, monkeypatch, rows=[free_design_row()], executors=capped) as (client, fakes):
        p = await project(client)
        direction = await draft(client, p['id'], 'audio:voice-design:test-elevenlabs-design', 'A voice',
                                controls={'preview_text': PREVIEW}, count=3)
        turn, nodes = await submit(client, direction)
        assert len(nodes) == 9 and fakes['elevenlabs'].probe.max_concurrent == 1


async def test_free_collection_asks_an_executor_that_offers_it(tmp_path, monkeypatch):
    failing = {'voice-runner': FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('error', 'Runner said no.'))}
    async with booted_audio(tmp_path, monkeypatch, executors=failing) as (client, fakes):
        p = await project(client)
        direction = await draft(client, p['id'], 'audio:voice-design:qwen3-tts', 'A voice', controls={'preview_text': PREVIEW})
        turn, _ = await submit(client, direction, until={'failed'})
        slot = turn['slots'][0]
        answer = await client.post(f"/api/crexil/attempts/{slot['id']}/collect", json=command(take=slot['attempt']))
        assert answer.status_code == 200, answer.text
        assert fakes['voice-runner'].collected == [slot['id']]
        assert answer.json()['metadata']['cause'] == 'The scripted provider has nothing to collect.'


# --- backups ---

async def test_backups_keep_audio_facts_and_blind_runs(tmp_path, monkeypatch):
    from test_crexil_restore import backup_upload, restored
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        await designed(client, p['id'], blind_run=True)
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload)
        assert copy['project']['id'] != p['id']
        assert [n['audio']['container'] for n in copy['nodes']] == ['wav'] * 3
        assert len(copy['blind_runs']) == 1 and copy['blind_runs'][0]['turn_id'] == copy['turns'][0]['id']


# --- the v13 store ---

async def test_a_populated_v12_store_widens_node_payloads_and_keeps_every_row(tmp_path):
    path = tmp_path / 'store.sqlite'
    db = Database(path)
    await db.open()
    ws = await db.create_workspace('Forge')
    turn = await db.create_turn_with_slots(ws['id'], 'anvil sparks', {}, [{'engine': 'klein'}, {'engine': 'z-image'}])
    first, second = turn['slots']
    await db.dispatch_slot(first['id'], job_ref='j1', submitted_graph={'g': 1}, seed=1)
    parent = await db.land_node(first['id'], payload='image', verb='run', prompt='anvil sparks',
                                output_path=str(tmp_path / 'a.png'))
    await db.dispatch_slot(second['id'], job_ref='j2', submitted_graph={'g': 2}, seed=2)
    child = await db.land_node(second['id'], payload='3d', verb='to_3d', prompt='anvil mesh',
                               output_path=str(tmp_path / 'b.glb'), parents=[parent['id']])
    gone = await db.import_node(ws['id'], output_path=str(tmp_path / 'c.png'), prompt='hammered brass')
    await db.conn.execute("UPDATE nodes SET purged_at=? WHERE id=?", ('2026-10-05T00:00:00+00:00', gone['id']))
    await db.conn.execute("INSERT INTO material_info(node_id,sha256,filename,media_type,width,height,origin_json) "
                          "VALUES(?,?,?,?,?,?,?)", (parent['id'], 'a' * 64, 'a.png', 'image/png', 8, 8, '{}'))
    await db.close()
    with sqlite3.connect(path) as conn:                       # the imported v12 shape
        conn.execute('PRAGMA foreign_keys=OFF')
        table = conn.execute("SELECT sql FROM sqlite_master WHERE name='nodes'").fetchone()[0]
        extras = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='nodes' "
                                             "AND type IN ('index','trigger') AND sql IS NOT NULL")]
        columns = ','.join(r[1] for r in conn.execute('PRAGMA table_info(nodes)'))
        conn.execute(table.replace("('image','3d','audio')", "('image','3d')").replace('CREATE TABLE nodes', 'CREATE TABLE old_nodes', 1))
        conn.execute(f'INSERT INTO old_nodes({columns}) SELECT {columns} FROM nodes')
        conn.execute('DROP TABLE nodes')
        conn.execute('ALTER TABLE old_nodes RENAME TO nodes')
        for statement in extras:
            conn.execute(statement)
        conn.executescript("DROP TABLE audio_info; DROP TABLE blind_runs; UPDATE meta SET value='12' WHERE key='schema_version';")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                             "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'node_fts%' AND name != 'meta'")]
        before = {t: sorted(conn.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr) for t in tables}
        assert "'audio'" not in conn.execute("SELECT sql FROM sqlite_master WHERE name='nodes'").fetchone()[0]
    for _ in range(2):                                        # the rung is safe to meet twice
        db = Database(path)
        await db.open()
        try:
            assert (await db._one("SELECT value FROM meta WHERE key='schema_version'"))['value'] == '13'
            for name, rows in before.items():
                assert sorted([tuple(r) for r in await db._all(f'SELECT * FROM "{name}"')], key=repr) == rows
            assert (await db._one('PRAGMA foreign_keys'))[0] == 1
            assert await db._all('PRAGMA foreign_key_check') == []
            found = {r['node_id'] for r in await db._all("SELECT node_id FROM node_fts WHERE node_fts MATCH 'anvil'")}
            assert found == {parent['id'], child['id']}
            assert await db._all("SELECT 1 FROM node_fts WHERE node_id=?", (gone['id'],)) == []
            names = {r['name'] for r in await db._all("SELECT name FROM sqlite_master WHERE tbl_name='nodes'")}
            assert {'trg_nodes_fts_insert', 'trg_nodes_fts_update', 'trg_nodes_fts_purge', 'trg_nodes_no_delete',
                    'idx_nodes_workspace', 'idx_nodes_tray', 'idx_nodes_slot_take'} <= names
            with pytest.raises(sqlite3.DatabaseError, match='never deleted'):
                await db.conn.execute('DELETE FROM nodes WHERE id=?', (parent['id'],))
        finally:
            await db.close()
    db = Database(path)
    await db.open()
    try:
        await db.conn.execute("UPDATE nodes SET prompt='glowing coals' WHERE id=?", (child['id'],))
        assert [r['node_id'] for r in await db._all("SELECT node_id FROM node_fts WHERE node_fts MATCH 'coals'")] == [child['id']]
        later = await db.create_turn_with_slots(ws['id'], 'a voice', {}, [{'engine': 'qwen3-tts'}])
        await db.dispatch_slot(later['slots'][0]['id'], job_ref='j3', seed=3)
        sound = await db.land_node(later['slots'][0]['id'], payload='audio', verb='voice-design',
                                   output_path=str(tmp_path / 'd.wav'))
        assert sound['payload'] == 'audio'
    finally:
        await db.close()


# --- measurement ---

def test_loudness_follows_bs1770_on_reference_tones():
    def tone(**kw):
        return audio.inspect(tone_wav(pitch=1000.0, amplitude=0.5, **kw))
    assert tone(seconds=5.0)['loudness_lufs'] == pytest.approx(-9.03, abs=0.1)        # -3.01 LKFS at 0 dBFS, less 6.02
    assert tone(seconds=5.0, channels=2)['loudness_lufs'] == pytest.approx(-6.02, abs=0.1)
    assert tone(seconds=5.0, rate=48000)['loudness_lufs'] == pytest.approx(-9.03, abs=0.1)
    assert tone(seconds=5.0, width=3)['loudness_lufs'] == pytest.approx(-9.03, abs=0.1)
    assert tone(seconds=5.0, width=1)['loudness_lufs'] == pytest.approx(-9.03, abs=0.2)
    assert tone(seconds=0.2)['loudness_lufs'] is None                                 # no whole 400 ms block
    facts = tone(seconds=5.0)
    assert facts['peak_dbfs'] == pytest.approx(20 * math.log10(0.5), abs=0.05) and len(facts['peaks']) == audio.PEAK_BINS
    silent = audio.inspect(audio.wrap_pcm(b'\0\0' * 24000, sample_rate=24000))
    assert silent['loudness_lufs'] is None and silent['peak_dbfs'] is None and silent['duration_s'] == 1.0


def test_measurement_refuses_what_it_cannot_read_in_plain_words():
    with pytest.raises(ValueError, match='not a PCM WAV file'):
        audio.inspect(b'ID3 an mp3 header')
    with pytest.raises(ValueError, match='partway through a sample'):
        audio.wrap_pcm(b'\0\0\0', sample_rate=24000)
    raw = bytes(range(200))
    assert audio.wrap_pcm(raw, sample_rate=24000)[-200:] == raw
