"""A blind run keeps each take's engine out of every read until the artist reveals
the run (law 23): continuing from its takes, bench and hold events, a run that
would compare fewer than two engines, the takes' own times, download names,
failure records and reuse. A whole run is refused when a price is unknown
(law 22), and a project's spending limits are the artist's to change. Each test
drives the HTTP API and the real scheduler with fake executors; no network, no
GPU.
"""

import asyncio
import json

import pytest

from app import blind, wiring
from app.executors import EngineFailure
from fakes import FakeAudioExecutor, Step, tone_wav
from test_api_v4 import booted, settle_turn, store
from test_audio_contract import PREVIEW, booted_audio, draft, free_design_row, operation
from test_crexil import command, image_bytes, project
from test_crexil_execution import attest, reviewed

pytestmark = pytest.mark.anyio

LOCAL, HOSTED = 'qwen3-tts', 'test-elevenlabs-design'
LOCAL_LABEL = 'Design voice · Qwen3-TTS 1.7B'
PROMPT = 'A gruff dwarf blacksmith, low and warm'
# What a hidden take's reads must never name, lower-cased: engine ids, providers, labels, models.
IDENTITY = ('elevenlabs', 'eleven_', 'qwen', 'voice-runner', 'voice runner')
CONTINUE = "This take's run is blind. Reveal it to continue from its takes."
REUSE = "This take's run is blind. Reveal the run before reusing its takes."
# Takes long enough to be a voice to speak with.
LONG = {'elevenlabs': FakeAudioExecutor('elevenlabs', 'external', data=tone_wav(seconds=3.5)),
        'voice-runner': FakeAudioExecutor('voice-runner', 'local', data=tone_wav(seconds=3.5))}


def casting(tmp_path, monkeypatch, **executors):
    """The audio stack with a free test copy of ElevenLabs Voice Design beside the local engine."""
    return booted_audio(tmp_path, monkeypatch, rows=(free_design_row(),), executors=executors or None)


async def prepared(client, wid, *cast, blind_run=True):
    """One voice-design preparation; `cast` is (engine, attempts) per engine."""
    operations = []
    for engine, count in cast or ((LOCAL, 1), (HOSTED, 1)):
        op = await operation(client, f'audio:voice-design:{engine}')
        operations.append({'operation_id': op['id'], 'revision': op['revision'], 'count': count,
                           'controls': {'preview_text': PREVIEW}})
    made = await client.post(f'/api/crexil/projects/{wid}/directions', json=command(title='Casting', setup={
        'action': 'voice-design', 'prompt': PROMPT, 'references': [], 'operations': operations,
        'spending_policy': 'hard_cap', 'blind': blind_run}))
    assert made.status_code == 201, made.text
    return made.json()


async def submitted(client, direction):
    """The review and the submit answer, as they are."""
    check = await reviewed(client, direction)
    answer = await client.post(f"/api/crexil/directions/{direction['id']}/submit",
                               json=command(revision=check['revision'], review_token=check['token']))
    return check, answer


async def ran(client, direction, until=('landed',)):
    check, answer = await submitted(client, direction)
    assert check['ready'], check['errors']
    assert answer.status_code == 201, answer.text
    turn = await settle_turn(answer.json()['turn_id'], until=set(until))
    return turn, [n for n in await store().list_nodes(turn['workspace_id']) if n['turn_id'] == turn['id']]


async def reveal(client, turn_id):
    answer = await client.post(f'/api/crexil/turns/{turn_id}/reveal', json=command(reason='Chosen blind'))
    assert answer.status_code == 200, answer.text


async def until(probe, ticks=600):
    for _ in range(ticks):
        found = await probe()
        if found:
            return found
        await asyncio.sleep(0.01)
    raise AssertionError('the condition never held')


async def events(client, **params):
    return (await client.get('/api/events', params={'limit': 500, **params})).json()['events']


def names_an_engine(item) -> bool:
    text = json.dumps(item).lower()
    return any(word in text for word in IDENTITY)


def leaks(reads: dict) -> dict:
    return {name: [item for item in items if names_an_engine(item)] for name, items in reads.items()}


async def every_read(client, wid, turn_id, direction_id) -> dict:
    """Every read that shows one blind run's takes, by name. The run's submission
    event is left out: its request names the engines the artist chose, which
    blindness does not hide."""
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    (turn,) = [t for t in snapshot['turns'] if t['id'] == turn_id]
    slots = {s['id'] for s in turn['slots']}
    nodes = [n for n in snapshot['nodes'] if n['turn_id'] == turn_id]
    takes = {n['id'] for n in nodes}
    groups = [g for g in snapshot['execution_groups'] if g['slot_id'] in slots]

    def about(event):
        return event['type'] != 'direction.submitted' and (
            event['turn_id'] == turn_id or event['slot_id'] in slots or event['node_id'] in takes
            or any(sid in json.dumps(event['payload']) for sid in slots))

    queue = (await client.get('/api/queue')).json()
    legacy = (await client.get(f'/api/workspaces/{wid}')).json()
    run = (await client.get(f'/api/crexil/directions/{direction_id}/run')).json()
    return {
        'snapshot slots': turn['slots'],
        'snapshot nodes': nodes,
        'execution groups': groups,
        'events': [e for e in await events(client) if about(e)],
        'project events': [e for e in await events(client, workspace_id=wid) if about(e)],
        'queue': [r for key in ('held', 'late') for r in queue[key] if r['turn_id'] == turn_id]
                 + [r for lane in queue['lanes'].values() for r in lane['slots'] if r['turn_id'] == turn_id],
        'workspace slots': [s for t in legacy['turns'] if t['id'] == turn_id for s in t['slots']],
        'workspace nodes': [n for n in (await client.get(f'/api/workspaces/{wid}/nodes')).json() if n['id'] in takes],
        'material records': [(await client.get(f'/api/crexil/material/{nid}/record')).json() for nid in takes],
        'run cells': run['cells'] + [run['summary']],
        'file names': [(await client.get(f"/api/crexil/outputs/{o['id']}/file")).headers['content-disposition']
                       for g in groups for o in g['outputs']],
    }


def own_times(reads) -> set[str]:
    """Every time a hidden take's reads carry of its own."""
    found = {n['created_at'] for n in reads['snapshot nodes'] + reads['workspace nodes']}
    for group in reads['execution groups']:
        found |= {group['created_at'], group['updated_at'], *(o['created_at'] for o in group['outputs'])}
    return found | {e['ts'] for e in reads['events'] + reads['project events'] if e['slot_id'] or e['node_id']}


async def run_created(client, wid, turn_id) -> str:
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    return next(r for r in snapshot['blind_runs'] if r['turn_id'] == turn_id)['created_at']


# --- every read, while a blind run lands, fails and is held ---

async def test_every_read_of_a_failing_blind_run_names_no_engine_and_no_time_of_its_own(tmp_path, monkeypatch):
    refused = FakeAudioExecutor('elevenlabs', 'external', failure=EngineFailure(
        'dead_key', 'ElevenLabs refused the key (eleven_multilingual_ttv_v2).', charged_usd=0))
    async with casting(tmp_path, monkeypatch, elevenlabs=refused) as (client, _):
        p = await project(client)
        direction = await prepared(client, p['id'], (LOCAL, 2), (HOSTED, 1))
        turn, _ = await ran(client, direction, until=('landed', 'failed'))
        reads = await every_read(client, p['id'], turn['id'], direction['id'])
        assert len(reads['snapshot nodes']) == 6 and reads['events'] and reads['file names']
        assert leaks(reads) == {name: [] for name in reads}
        # The run's own record of its slots lists no engines (it would map positions to engines).
        (created,) = [e for e in reads['events'] if e['type'] == 'turn.created']
        assert 'engines' not in created['payload'] and created['payload']['slots'] == 3
        # A failed attempt's group keeps its kind and a plain line, never the engine's words.
        (failed,) = [g for g in reads['execution groups'] if g['metadata'].get('failure_kind') == 'dead_key']
        assert failed['metadata']['cause'] == blind.plain_cause('dead_key')
        # Every time a hidden take carries is its run's creation time (V1b S-4).
        assert own_times(reads) == {await run_created(client, p['id'], turn['id'])}
        assert all(r['elapsed_ms'] is None for r in reads['queue'])

        await reveal(client, turn['id'])
        after = await every_read(client, p['id'], turn['id'], direction['id'])
        assert any(leaks(after).values()) and len(own_times(after)) > 1    # the engines and real times return


# --- continuing from a hidden take (V1b S-1) ---

async def test_a_hidden_take_cannot_be_continued_until_its_run_is_revealed(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, **LONG) as (client, fakes):
        p = await project(client)
        turn, takes = await ran(client, await prepared(client, p['id']))
        hosted = next(n for n in takes if n['engine'] == HOSTED)
        local = next(n for n in takes if n['engine'] == LOCAL)
        for engine, source, policy in (('qwen3-tts', hosted, 'hard_cap'), ('elevenlabs-v4', local, 'published_tariff')):
            speak = await draft(client, p['id'], f'audio:speak:{engine}', 'Bring me the ore.',
                                refs=[source['id']], policy=policy)
            check, answer = await submitted(client, speak)
            # One provider-neutral line and the next move: no terms or voice reason tells the engine apart.
            assert not check['ready'] and check['errors'] == [CONTINUE]
            assert answer.status_code == 422 and answer.json()['detail'] == CONTINUE
        assert len(await store().list_turns(p['id'])) == 1                # no child froze a hidden parent
        sent = len(fakes['voice-runner'].requests) + len(fakes['elevenlabs'].requests)
        assert sent == 2                                                  # the blind run's own two attempts

        await reveal(client, turn['id'])
        check = await reviewed(client, await draft(client, p['id'], 'audio:speak:qwen3-tts', 'Bring me the ore.',
                                                   refs=[hosted['id']]))
        assert CONTINUE not in check['errors']
        assert any("ElevenLabs' terms do not allow" in error for error in check['errors'])   # its own reason, now


async def test_a_hidden_image_take_cannot_be_continued_and_files_are_named_by_grid_position(tmp_path, monkeypatch):
    # Arranging reverses the takes, so a file name stamped before arranging would carry the old order.
    monkeypatch.setattr(blind, 'arrange', lambda specs: list(reversed(specs)))
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
        made = await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(title='Lamps', setup={
            'prompt': 'brass lamp', 'blind': True, 'operations': [
                {'operation_id': key, 'revision': ops[key]['revision'], 'count': count}
                for key, count in (('testpreset', 2), ('txtonly', 1))]}))
        turn, takes = await ran(client, made.json())
        for slot in turn['slots']:
            (prefix,) = {n['inputs']['filename_prefix'] for n in slot['submitted_graph'].values()
                         if n['class_type'] == 'SaveImage'}
            assert prefix.endswith(f"/s{slot['pos']}")                     # the frozen position, never the old order
        continued = await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(
            title='Again', setup={'action': 'continue', 'prompt': 'brass lamp', 'references': [{'node_id': takes[0]['id']}],
                                  'operations': [{'operation_id': 'testpreset',
                                                  'revision': ops['testpreset']['revision'], 'count': 1}]}))
        check = await reviewed(client, continued.json())
        assert not check['ready'] and check['errors'] == [CONTINUE]
        names = [(await client.get(f"/api/crexil/outputs/{o['id']}/file")).headers['content-disposition']
                 for g in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['execution_groups']
                 for o in g['outputs']]
        assert names and all('filename="output-' in name for name in names)


# --- bench and hold events (V1b S-2) ---

async def test_a_mid_run_bench_names_no_hidden_take_and_its_events_return_at_the_reveal(tmp_path, monkeypatch):
    down = FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('unreachable', 'runner down'))
    async with casting(tmp_path, monkeypatch, **{'voice-runner': down}) as (client, _):
        p = await project(client)
        direction = await prepared(client, p['id'], (LOCAL, 3), (HOSTED, 1))
        turn, _ = await ran(client, direction, until=('landed', 'held', 'failed'))
        hidden = {s['id'] for s in turn['slots']}
        held = {s['id'] for s in turn['slots'] if s['status'] == 'held'}
        assert len(held) == 2
        await wiring.current.scheduler.unbench('provider:voice-runner')
        await settle_turn(turn['id'], until={'landed', 'held', 'failed'})

        for read in (await events(client), await events(client, workspace_id=p['id'])):
            for event in read:
                listed = {sid for key in ('held', 'resumed') for sid in event['payload'].get(key) or ()}
                assert not listed & hidden, event                       # a bench never lists a hidden take
                if event['slot_id'] in hidden:
                    assert not names_an_engine(event) and {'bench', 'unbench'}.isdisjoint(event['payload']), event
        benched = [e for e in await events(client) if e['type'] == 'engine.benched']
        assert benched and all(e['payload']['scope'] == 'provider:voice-runner' for e in benched)
        queue = (await client.get('/api/queue')).json()
        assert all(r['engine'] == blind.HIDDEN_ENGINE and r['bench'] is None
                   for r in queue['held'] if r['turn_id'] == turn['id'])

        await reveal(client, turn['id'])
        first = next(e for e in await events(client) if e['type'] == 'engine.benched')
        assert set(first['payload']['held']) == held                     # masked while blind, never lost


async def test_an_engine_benched_inside_a_blind_run_reads_masked_until_the_reveal(tmp_path, monkeypatch):
    failing = FakeAudioExecutor('voice-runner', 'local', failure=EngineFailure('error', 'Qwen3-TTS crashed'))
    async with casting(tmp_path, monkeypatch, **{'voice-runner': failing}) as (client, _):
        p = await project(client)
        direction = await prepared(client, p['id'], (LOCAL, 3), (HOSTED, 1))
        turn, _ = await ran(client, direction, until=('landed', 'held', 'failed'))
        assert sorted(s['status'] for s in turn['slots']) == ['failed', 'failed', 'held', 'landed']
        (bench,) = [e for e in await events(client) if e['type'] == 'engine.benched']
        # Law 1's one cause for a repeat failure, without the engine, its scope or the slots it names.
        assert bench['payload'] == {'kind': 'repeat', 'cause': blind.plain_cause('repeat'), 'held': [],
                                    'identity_hidden': True}
        assert bench['ts'] == await run_created(client, p['id'], turn['id'])

        await reveal(client, turn['id'])
        (bench,) = [e for e in await events(client) if e['type'] == 'engine.benched']
        assert bench['payload']['scope'] == f"engine:{LOCAL}@turn:{turn['id']}" and len(bench['payload']['held']) == 1


# --- a blind run compares at least two engines, sent whole (V1b S-3) ---

async def test_a_blind_run_needs_two_engines_ready_and_is_never_narrowed(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, fakes):
        p = await project(client)
        alone = await prepared(client, p['id'], (LOCAL, 2))
        check, answer = await submitted(client, alone)
        one = 'A blind run compares at least two engines. Add another engine, or turn blind off.'
        assert not check['ready'] and one in check['errors']
        assert answer.status_code == 422 and one in answer.json()['detail']

        await wiring.current.scheduler.bench('provider:voice-runner', kind='unreachable', cause='runner down')
        pair = await prepared(client, p['id'])
        check, answer = await submitted(client, pair)
        waiting = (f'A blind run sends all of its engines or none, and {LOCAL_LABEL} is not ready. '
                   'Make it ready, or remove it from the run.')
        assert not check['ready'] and waiting in check['errors'] and check['ready_attempts'] == 0
        assert answer.status_code == 422 and waiting in answer.json()['detail']
        # Nothing was sent, not even to the engine that was ready: no partial blind run.
        assert fakes['elevenlabs'].requests == fakes['voice-runner'].requests == []
        assert await store().list_turns(p['id']) == []

        await wiring.current.scheduler.unbench('provider:voice-runner')
        assert (await reviewed(client, pair))['ready']


# --- failure records (V1b S-9) ---

async def test_a_hidden_takes_internal_failure_keeps_only_its_kind_and_plain_line(tmp_path, monkeypatch):
    crash = FakeAudioExecutor('elevenlabs', 'external',
                              failure=RuntimeError('eleven_multilingual_ttv_v2 failed in app/elevenlabs.py'))
    async with casting(tmp_path, monkeypatch, elevenlabs=crash) as (client, _):
        p = await project(client)
        direction = await prepared(client, p['id'])
        turn, _ = await ran(client, direction, until=('landed', 'failed'))
        (record,) = [e for e in await events(client, workspace_id=p['id']) if e['type'] == 'cockpit.failure']
        assert record['payload']['kind'] == 'internal' and record['payload']['note']
        assert {'site', 'error', 'exc_type', 'message', 'traceback', 'engine'}.isdisjoint(record['payload'])
        assert not names_an_engine(record)
        reads = await every_read(client, p['id'], turn['id'], direction['id'])
        (failed,) = [s for s in reads['snapshot slots'] if s['status'] == 'failed']
        assert failed['kind'] == 'internal' and failed['cause'] == blind.plain_cause('internal') and failed['detail'] is None
        assert leaks(reads) == {name: [] for name in reads}

        await reveal(client, turn['id'])
        (record,) = [e for e in await events(client, workspace_id=p['id']) if e['type'] == 'cockpit.failure']
        assert 'eleven_multilingual_ttv_v2' in record['payload']['traceback']      # kept, shown after the reveal


# --- reuse (V1b S-10 and S-5) ---

async def test_reuse_review_and_reuse_refuse_a_hidden_take_in_the_same_words(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        a, b = await project(client), await project(client)
        turn, takes = await ran(client, await prepared(client, a['id']))
        selection = {'source_project_id': a['id'], 'node_id': takes[0]['id']}
        review = await client.post(f"/api/crexil/projects/{b['id']}/reuse/review", json=selection)
        assert review.status_code == 422 and review.json()['detail'] == REUSE
        made = await client.post(f"/api/crexil/projects/{b['id']}/reuse", json=command(
            **selection, review_token='0' * 64, explanation='For the forge level'))
        assert made.status_code == 422 and made.json()['detail'] == REUSE
        assert (await client.get(f"/api/crexil/projects/{b['id']}")).json()['nodes'] == []

        await reveal(client, turn['id'])
        review = await client.post(f"/api/crexil/projects/{b['id']}/reuse/review", json=selection)
        assert review.status_code == 200 and review.json()['ready'] is True


async def test_a_reused_elevenlabs_take_keeps_its_terms_so_local_speech_refuses_it(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch, **LONG) as (client, _):
        a, b = await project(client), await project(client)
        _, takes = await ran(client, await prepared(client, a['id'], (HOSTED, 1), blind_run=False))
        selection = {'source_project_id': a['id'], 'node_id': takes[0]['id']}
        review = (await client.post(f"/api/crexil/projects/{b['id']}/reuse/review", json=selection)).json()
        made = await client.post(f"/api/crexil/projects/{b['id']}/reuse", json=command(
            **selection, review_token=review['token'], explanation='For the forge level'))
        assert made.status_code == 201, made.text
        copy = made.json()['node']
        assert (await store().material_origin(copy['id']))['terms']['provider'] == 'elevenlabs'
        check = await reviewed(client, await draft(client, b['id'], 'audio:speak:qwen3-tts', 'Bring me the ore.',
                                                   refs=[copy['id']]))
        assert not check['ready'] and any("ElevenLabs' terms do not allow" in error for error in check['errors'])


# --- a held take keeps the words of what holds it ---

def test_a_held_take_keeps_a_cause_no_failure_kind_words_and_a_failure_reads_plainly():
    limit = 'Project spending limit is $0.01; charged and reserved spending totals $0.02.'
    held = blind.mask('slot', {'engine': 'x', 'status': 'held', 'kind': None, 'cause': limit, 'started_at': 't'})
    assert held['cause'] == limit and held['engine'] == blind.HIDDEN_ENGINE and held['started_at'] is None
    cell = blind.mask('slot', {'engine': 'x', 'state': 'held', 'kind': None, 'cause': limit})   # the agent run view
    assert cell['cause'] == limit
    failed = blind.mask('slot', {'engine': 'x', 'status': 'failed', 'kind': 'timeout', 'cause': 'x went silent'})
    assert failed['cause'] == blind.plain_cause('timeout')
    skipped = blind.mask('slot', {'engine': 'x', 'status': 'skipped', 'kind': None, 'cause': 'Stopped before x'})
    assert skipped['cause'] == blind.plain_cause(None)
    saved = blind.mask('event', {'type': 'voice.saved', 'turn_id': 't', 'slot_id': 's', 'payload': {
        'provider': 'elevenlabs', 'generated_voice_id': 'gen-1', 'voice_id': 'v-1', 'attempt': 1}})
    assert saved['payload'] == {'attempt': 1, 'identity_hidden': True}


# --- law 22 for the whole run ---

async def test_an_unknown_price_refuses_the_whole_run_before_any_engine_is_called(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        del wiring.current.catalog['txtonly']['execution_cost']           # its price is no longer known
        fakes['comfy'].default = Step(data=image_bytes())
        p = await project(client)
        ops = {o['id']: o for o in (await client.get('/api/crexil/operations')).json()}
        made = await client.post(f"/api/crexil/projects/{p['id']}/directions", json=command(title='Lamps', setup={
            'prompt': 'brass lamp', 'operations': [{'operation_id': key, 'revision': ops[key]['revision'], 'count': 1}
                                                   for key in ('testpreset', 'txtonly')]}))
        check, answer = await submitted(client, made.json())
        whole = ('An unknown price counts as over the spending limit, so nothing in this run is sent. '
                 'Remove Txt Only from the run, or choose an engine with a published price.')
        assert not check['ready'] and (check['ready_attempts'], check['blocked_attempts']) == (0, 2)
        assert whole in check['errors'] and answer.status_code == 422 and whole in answer.json()['detail']
        assert fakes['comfy'].starts == [] and await store().list_turns(p['id']) == []


# --- spending limits are the artist's ---

async def test_agents_cannot_change_a_projects_spending_limits(tmp_path, monkeypatch):
    agent = {'kind': 'agent', 'name': 'Scout'}
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)

        async def edit(actor=None, **fields):
            current = (await client.get(f"/api/crexil/projects/{p['id']}")).json()['project']
            body = {'revision': current['revision'], 'name': current['name'], **fields}
            return await client.patch(f"/api/crexil/projects/{p['id']}",
                                      json=command(**body, **({'actor': actor} if actor else {})))

        artist = await edit(limits={'spending_limit_usd': 1, 'spending_policy': 'published_tariff'})
        assert artist.status_code == 200 and artist.json()['limits']['spending_limit_usd'] == 1
        changes = len([e for e in await events(client, workspace_id=p['id']) if e['type'] == 'project.changed'])
        for limits in ({'spending_limit_usd': 5, 'spending_policy': 'published_tariff'},
                       {'spending_limit_usd': 0.5, 'spending_policy': 'published_tariff'},
                       {'spending_limit_usd': 1, 'spending_policy': 'hard_cap'}):
            refused = await edit(agent, limits=limits)
            assert refused.status_code == 403 and "the artist's act" in refused.json()['detail']
        project_now = (await client.get(f"/api/crexil/projects/{p['id']}")).json()['project']
        assert project_now['limits']['spending_limit_usd'] == 1 and project_now['revision'] == artist.json()['revision']
        assert len([e for e in await events(client, workspace_id=p['id']) if e['type'] == 'project.changed']) == changes

        # Every other edit stays open to an agent, with or without restating the same limits.
        renamed = await edit(agent, name='Forge level', brief='A hammer for the smith')
        assert renamed.status_code == 200 and renamed.json()['name'] == 'Forge level'
        restated = await edit(agent, brief='A heavier hammer', limits=artist.json()['limits'])
        assert restated.status_code == 200 and restated.json()['limits']['spending_limit_usd'] == 1
