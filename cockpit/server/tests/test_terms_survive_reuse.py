"""A copy keeps its source's provider terms and audio facts, so no path into a
project widens what a take may feed (laws 12, 21; ElevenLabs' use policy)."""

import json

import pytest

from app.crexil_reuse import _portable_facts
from fakes import FakeAudioExecutor, tone_wav
from test_api_v4 import settle_turn, store
from test_audio_contract import draft, submit
from test_blind_runs import HOSTED, LOCAL, casting, prepared
from test_crexil import command, imported, project
from test_crexil_execution import reviewed
from test_crexil_restore import backup_upload, restored

pytestmark = pytest.mark.anyio

TERMS = "ElevenLabs' terms do not allow its audio as input to another AI model."


def long_takes():
    """Takes long enough to clone, so only terms can refuse a local voice."""
    return {'elevenlabs': FakeAudioExecutor('elevenlabs', 'external', data=tone_wav(seconds=3.5)),
            'voice-runner': FakeAudioExecutor('voice-runner', 'local', data=tone_wav(seconds=3.5))}


async def designed_on(client, wid, engine):
    """One landed voice design on one engine, in a run that is not blind."""
    direction = await prepared(client, wid, blind_run=False, engines=(engine,))
    check = await reviewed(client, direction)
    assert check['ready'], check['errors']
    response = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                                 json=command(revision=check['revision'], review_token=check['token']))
    assert response.status_code == 201, response.text
    turn = await settle_turn(response.json()['turn_id'], until={'landed'})
    return [n for n in await store().list_nodes(wid) if n['turn_id'] == turn['id']]


async def reuse(client, target, source_project, node_id):
    selection = {'source_project_id': source_project, 'node_id': node_id}
    review = await client.post(f'/api/crexil/projects/{target}/reuse/review', json=selection)
    assert review.status_code == 200 and review.json()['ready'], review.text
    made = await client.post(f'/api/crexil/projects/{target}/reuse', json=command(
        **selection, review_token=review.json()['token'], explanation='Cast this voice again'))
    assert made.status_code == 201, made.text
    return made.json()['node']


async def local_voice_check(client, wid, voice_id):
    check = await reviewed(client, await draft(client, wid, f'audio:speak:{LOCAL}', 'Bring me the ore.',
                                               refs=[voice_id]))
    return check['ready'], ' '.join(check['errors'])


async def test_a_reused_elevenlabs_take_keeps_its_terms_and_never_voices_a_local_engine(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a, b = await project(client), await project(client)
        take = (await designed_on(client, a['id'], HOSTED))[0]
        source_terms = (await store().material_origin(take['id']))['terms']
        copy = await reuse(client, b['id'], a['id'], take['id'])
        copied_terms = (await store().material_origin(copy['id'])).get('terms')
        ready, errors = await local_voice_check(client, b['id'], copy['id'])
    assert source_terms['no_ai_input'] is True and copied_terms == source_terms
    assert not ready and TERMS in errors
    assert 'seconds' not in errors               # refused for its terms, not for a missing length


async def test_a_copy_of_a_copy_keeps_the_terms(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a, b, c = await project(client), await project(client), await project(client)
        take = (await designed_on(client, a['id'], HOSTED))[0]
        first = await reuse(client, b['id'], a['id'], take['id'])
        second = await reuse(client, c['id'], b['id'], first['id'])
        terms = (await store().material_origin(second['id'])).get('terms')
        ready, errors = await local_voice_check(client, c['id'], second['id'])
    assert terms and terms['allows'] == ['elevenlabs']
    assert not ready and TERMS in errors


async def test_a_take_recorded_before_terms_were_kept_gets_its_engines_terms_on_reuse(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a, b = await project(client), await project(client)
        take = (await designed_on(client, a['id'], HOSTED))[0]
        db = store()
        origin = await db.material_origin(take['id'])
        origin.pop('terms')
        async with db._locked():                 # an older record that never kept the terms
            await db.conn.execute('UPDATE material_info SET origin_json=? WHERE node_id=?',
                                  (json.dumps(origin), take['id']))
            await db.conn.commit()
        copy = await reuse(client, b['id'], a['id'], take['id'])
        terms = (await store().material_origin(copy['id'])).get('terms')
        ready, errors = await local_voice_check(client, b['id'], copy['id'])
    assert terms and terms['provider'] == 'elevenlabs' and terms['no_ai_input'] is True
    assert not ready and TERMS in errors


async def test_a_reused_take_keeps_its_audio_facts_and_a_local_voice_stays_usable(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a, b = await project(client), await project(client)
        voice = (await designed_on(client, a['id'], LOCAL))[0]
        _, lines = await submit(client, await draft(client, a['id'], f'audio:speak:{LOCAL}', 'Bring me the ore.',
                                                    refs=[voice['id']]))
        line = lines[0]
        source = await store().audio_fact(line['id'])
        copy = await reuse(client, b['id'], a['id'], line['id'])
        copied = await store().audio_fact(copy['id'])
        voice_copy = await reuse(client, b['id'], a['id'], voice['id'])
        ready, errors = await local_voice_check(client, b['id'], voice_copy['id'])
    measured = ('container', 'codec', 'sample_rate', 'channels', 'duration_s', 'loudness_lufs',
                'peak_dbfs', 'peaks', 'text', 'alignment')
    assert {k: copied[k] for k in measured} == {k: source[k] for k in measured}
    assert copied['duration_s'] == pytest.approx(3.5) and copied['peaks'] and copied['text'] == 'Bring me the ore.'
    assert source['voice']['reference_node_id'] == voice['id']
    assert copied['voice'] == {k: v for k, v in source['voice'].items() if k != 'reference_node_id'}
    assert ready, errors                         # a copy of a local take is no more restricted than its source


async def test_a_restored_project_keeps_the_terms_on_its_takes(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a = await project(client)
        take = (await designed_on(client, a['id'], HOSTED))[0]
        source_terms = (await store().material_origin(take['id']))['terms']
        upload, _ = await backup_upload(client, a['id'])
        copy = await restored(client, upload)
        node = copy['nodes'][0]
        restored_terms = (await store().material_origin(node['id'])).get('terms')
        ready, errors = await local_voice_check(client, copy['project']['id'], node['id'])
    assert restored_terms == source_terms
    assert not ready and TERMS in errors


async def test_returned_external_work_records_its_lineage_but_claims_no_provider_terms(tmp_path, monkeypatch):
    """The import door takes images and GLB files made by an outside tool: that work
    is not a provider's output, so it carries no provider terms. Its declared parent
    keeps the lineage visible."""
    async with casting(tmp_path, monkeypatch, executors=long_takes()) as (client, _):
        a = await project(client)
        take = (await designed_on(client, a['id'], HOSTED))[0]
        made = (await imported(client, a['id'], tool='Krita', explanation='Cover art for the smith',
                               parents=[take['id']]))['node']
        origin = await store().material_origin(made['id'])
        node = await store().get_node(made['id'])
    assert origin['kind'] == 'reported_external' and 'terms' not in origin
    assert node['parents'] == [take['id']]


def test_portable_facts_drop_links_into_the_source_project_and_keep_the_voice_identity():
    facts = {'container': 'wav', 'peaks': [0.1], 'text': 'Ore.',
             'voice': {'provider': 'elevenlabs', 'kind': 'saved', 'voice_id': 'v1',
                       'from_generated_voice_id': 'g1', 'reference_node_id': 'n-elsewhere'}}
    portable = _portable_facts(facts)
    assert portable['voice'] == {'provider': 'elevenlabs', 'kind': 'saved', 'voice_id': 'v1',
                                 'from_generated_voice_id': 'g1'}
    assert portable['container'] == 'wav' and facts['voice']['reference_node_id'] == 'n-elsewhere'
    assert _portable_facts({'voice': None})['voice'] is None
