"""The agent door's HTTP core against the scripted harness: an agent prepares,
reviews and submits runs within the door's ceiling, proposes takes with reasons
as itself, sees a spending conflict with a fresh review, and never receives a
take's bytes, a file path or an address to fetch one. The MCP layer over this
core is tested in tools/agent-door/tests, where the mcp package is installed."""

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app import wiring
from test_api_v4 import booted, store
from test_audio_contract import PREVIEW, booted_audio
from test_blind_runs import LOCAL
from test_crexil import command
from test_crexil_execution import reviewed
from test_crexil_hosted import discover, hosted
from test_review_token import IN_FLIGHT, priced_project

pytestmark = pytest.mark.anyio

DOOR_FILE = Path(__file__).resolve().parents[3] / 'tools' / 'agent-door' / 'crexil_mcp.py'
_spec = importlib.util.spec_from_file_location('crexil_mcp', DOOR_FILE)
door_module = importlib.util.module_from_spec(_spec)
sys.modules.setdefault('crexil_mcp', door_module)
_spec.loader.exec_module(door_module)
Door, DoorError, EngineChoice = door_module.Door, door_module.DoorError, door_module.EngineChoice

AGENT = {'kind': 'agent', 'name': 'Tester'}
# What an unfiltered engine list says of each engine.
COMPACT = {'id', 'label', 'actions', 'produces', 'availability', 'reasons', 'price_usd', 'price_unit'}
# Bytes, routes to a take's file, media file names and machine paths: none may
# reach an agent's model (ElevenLabs' terms; the run view and record hold none).
MATERIAL_MARKS = ('data:', 'base64', '/api/crexil/material/', '/api/nodes/', '/outputs/', '/files/', '/thumb',
                  'http://', 'https://', '.wav', '.mp3', '.png', '.glb', '/home/', '/tmp/', '/mnt/')


def marks(*results) -> set[str]:
    text = json.dumps([r.model_dump() for r in results])
    return {mark for mark in MATERIAL_MARKS if mark in text}


def door_over(client, ceiling=0):
    return Door('http://127.0.0.1', 'Tester', ceiling, client=client, poll_s=0.01)


async def events_of(wid):
    return [e for e in await store().events_since(0, workspace_id=wid) if isinstance(e.get('payload'), dict)]


# --- the journey an integrator follows ---

async def test_an_agent_drafts_voices_on_the_free_local_engine_and_proposes_one_as_itself(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        door = door_over(client)
        engines = await door.list_engines(payload='audio', action='voice-design')
        local = next(e for e in engines.engines if e.id == f'audio:voice-design:{LOCAL}')
        assert local.produces == 'audio' and local.availability != 'unavailable' and local.price_usd == 0
        assert 'this machine' in local.data_destination.lower() or 'local' in local.location.lower()
        made = await door.create_project('Dwarf blacksmith', 'A gruff smith for the forge level')
        prepared = await door.prepare(made.project_id, 'voice-design', 'A gruff dwarf blacksmith, low and warm',
                                      [EngineChoice(id=local.id, count=2, controls={'preview_text': PREVIEW})], 0)
        review = prepared.review
        assert review.ready and review.reserved_usd == 0 and review.within_door_ceiling
        assert review.engines[0].id == local.id and review.engines[0].ready and 'Free to run' in review.next
        submitted = await door.submit(prepared.run_id, review.revision, review.review_token, 0)
        assert submitted.submitted_attempts == 2 and submitted.left_in_draft == []
        run = await door.run(prepared.run_id, wait_seconds=10)
        assert run.settled and [c.state for c in run.cells] == ['landed', 'landed']
        takes = [t for c in run.cells for t in c.takes]
        assert len(takes) == 6 and all(t.payload == 'audio' and t.audio and t.audio.duration_s > 0 for t in takes)
        proposed = await door.propose(made.project_id, 'Warmest growl, clearest consonants', take_ids=[takes[1].take_id])
        assert proposed.kind == 'assessment' and proposed.author == 'agent:Tester'
        record = await door.decisions(made.project_id)
        project = await door.project(made.project_id)
        found = await door.search(made.project_id, 'growl')
        listed = await door.projects()
        status = await door.status()
        again = await door.run(prepared.run_id)
        events = await events_of(made.project_id)
    assert record.count == 1 and record.records[0].kind == 'assessment' and record.records[0].author == 'agent:Tester'
    assert record.records[0].chosen == [] and record.purpose
    # The judged preview names the attempt that made it: the first cell's first attempt.
    (judged,) = record.records[0].candidates
    assert judged.take_id == takes[1].take_id and judged.attempt_id.endswith(f':{run.cells[0].attempt}')
    assert project.runs[0].takes == 6 and project.recent_decisions[0].author == 'agent:Tester'
    assert found.decisions and found.decisions[0].reason == 'Warmest growl, clearest consonants'
    assert any(p.project_id == made.project_id for p in listed.projects) and status.agent == 'agent:Tester'
    assert again.selection == [] and again.decisions[-1].author == 'agent:Tester'   # proposing never picks
    authored = [e['payload']['actor'] for e in events if 'actor' in e['payload']]
    assert authored and all(actor == AGENT for actor in authored)                     # every write names the agent
    assert not marks(engines, made, prepared, submitted, run, proposed, record, project, found, listed, status, again)


async def test_the_unfiltered_engine_list_stays_compact_and_a_filter_adds_each_engines_controls(tmp_path, monkeypatch):
    # A live catalogue lists dozens of hosted image models. Unfiltered, the list must fit
    # a client's tool output: Claude Code refused 100,593 characters for 75 engines.
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        discover()
        catalog = wiring.current.provider_catalog
        for n in range(65):
            catalog._live[f'vendor{n}/model-{n}'] = {**catalog._live['testvendor/testmodel'], 'id': f'vendor{n}/model-{n}',
                                                    'provider': f'vendor{n}', 'label': f'Model {n}', 'name': f'Model {n}'}
        # One engine priced by token, which a review cannot reserve from a number of images.
        endpoint = catalog._live['testvendor/testmodel']['endpoints'][0]
        catalog._live['vendor7/model-7']['endpoints'] = [
            {**endpoint, 'pricing': [{'billable': 'output_image', 'unit': 'token', 'cost_usd': .00004}]}]
        door = door_over(client)
        everything = (await door.list_engines()).model_dump(mode='json')
        voices = (await door.list_engines(action='voice-design')).model_dump(mode='json')
        images = (await door.list_engines(payload='image')).model_dump(mode='json')
    assert len(everything['engines']) >= 75 and len(json.dumps(everything)) < 30_000
    assert all(set(engine) == COMPACT for engine in everything['engines'])
    priced = {e['id']: (e['price_usd'], e['price_unit']) for e in everything['engines']}
    # An engine's price is what its review reserves for one image, whatever a pin estimates.
    assert priced['testvendor/testmodel'] == priced['vendor8/model-8'] == (0.04, 'image')
    assert priced['vendor7/model-7'] == (None, 'image')
    assert priced[f'audio:voice-design:{LOCAL}'] == (0, 'attempt')
    assert {e['id'] for e in voices['engines']} == {f'audio:voice-design:{LOCAL}', 'audio:voice-design:elevenlabs-voice-design'}
    for engine in voices['engines'] + images['engines']:
        assert set(engine) > COMPACT and engine['location'] and engine['data_destination'] and engine['controls']
    preview = next(c for e in voices['engines'] for c in e['controls'] if c['key'] == 'preview_text')
    assert None not in preview.values() and 'choices' not in preview   # a control's unset facts stay out


async def test_revising_a_draft_needs_the_revision_the_agent_read(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, _):
        door = door_over(client)
        made = await door.create_project('Dwarf blacksmith')
        op = f'audio:voice-design:{LOCAL}'
        first = await door.prepare(made.project_id, 'voice-design', 'A gruff dwarf',
                                   [EngineChoice(id=op, controls={'preview_text': PREVIEW})], 0)
        with pytest.raises(DoorError) as missing:
            await door.prepare(made.project_id, 'voice-design', 'A warmer dwarf',
                               [EngineChoice(id=op, controls={'preview_text': PREVIEW})], 0, run_id=first.run_id)
        revised = await door.prepare(made.project_id, 'voice-design', 'A warmer dwarf',
                                     [EngineChoice(id=op, count=3, controls={'preview_text': PREVIEW})], 0,
                                     run_id=first.run_id, expected_revision=first.review.revision)
        with pytest.raises(DoorError) as stale:
            await door.prepare(made.project_id, 'voice-design', 'A third dwarf',
                               [EngineChoice(id=op, controls={'preview_text': PREVIEW})], 0,
                               run_id=first.run_id, expected_revision=first.review.revision)
    assert missing.value.kind == 'invalid_input'
    assert revised.run_id == first.run_id and revised.review.attempts == 3
    assert stale.value.kind == 'conflict'


# --- money: the door's ceiling and the reviewed amount (law 22) ---

async def test_the_door_refuses_a_run_over_its_ceiling_before_anything_is_sent(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        door = door_over(client)                              # $0: free engines only
        p = await priced_project(client, 1)
        before = len((await client.get(f"/api/crexil/projects/{p['id']}")).json()['directions'])
        with pytest.raises(DoorError) as refused:
            await door.prepare(p['id'], 'generate', 'brass lamp', [EngineChoice(id='testvendor/testmodel')], 0.04,
                               spending_policy='published_tariff')
        after = (await client.get(f"/api/crexil/projects/{p['id']}")).json()['directions']
    assert refused.value.kind == 'over_door_ceiling'
    assert '$0.04 spending limit; this door allows $0.00 per run.' in str(refused.value)
    # The next move names the launch flag that raises the ceiling.
    assert '--max-run-usd' in refused.value.next_move and 'Nothing was sent' in str(refused.value)
    assert len(after) == before and not fakes['openrouter'].starts


async def test_submit_restates_the_reviewed_amount_and_holds_the_ceiling_against_a_raised_draft(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        door = door_over(client, 0.05)
        p = await priced_project(client, 1)
        prepared = await door.prepare(p['id'], 'generate', 'brass lamp', [EngineChoice(id='testvendor/testmodel')],
                                      0.05, spending_policy='published_tariff')
        assert prepared.review.ready and prepared.review.reserved_usd == 0.04 and prepared.review.within_door_ceiling
        with pytest.raises(DoorError) as understated:
            await door.submit(prepared.run_id, prepared.review.revision, prepared.review.review_token, 0)
        # The artist raises the draft past the door's ceiling; the agent may not submit it.
        draft = next(d for d in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['directions']
                     if d['id'] == prepared.run_id)
        raised = {**draft['setup'], 'spending_limit_usd': 1,
                  'operations': [{**draft['setup']['operations'][0], 'count': 2}]}
        patched = await client.patch(f"/api/crexil/directions/{prepared.run_id}", json=command(
            revision=draft['revision'], title=draft['title'], setup=raised))
        assert patched.status_code == 200, patched.text
        bigger = await door.review(prepared.run_id)
        with pytest.raises(DoorError) as over:
            await door.submit(prepared.run_id, bigger.revision, bigger.review_token, bigger.reserved_usd)
        sent = fakes['openrouter'].starts
    assert understated.value.kind == 'refused' and 'You confirmed $0.00, but this run reserves $0.04.' in str(understated.value)
    assert bigger.reserved_usd == 0.08 and not bigger.within_door_ceiling and "door's ceiling" in bigger.next
    assert "over this door's ceiling of $0.05" in bigger.next and '--max-run-usd' in bigger.next
    assert over.value.kind == 'over_door_ceiling' and not sent
    assert 'this door allows $0.05 per run' in over.value.line and '--max-run-usd' in over.value.next_move


async def test_a_spending_conflict_comes_back_with_a_fresh_review(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        fakes['openrouter'].default = IN_FLIGHT
        door = door_over(client, 1)
        p = await priced_project(client, .04)                # the project fits one $0.04 image
        mine = await door.prepare(p['id'], 'generate', 'brass lamp', [EngineChoice(id='testvendor/testmodel')],
                                  1, spending_policy='published_tariff')
        theirs = await hosted(client, p['id'])               # the artist's own run lands first
        theirs_review = await reviewed(client, theirs)
        accepted = await client.post(f"/api/crexil/directions/{theirs['id']}/submit", json=command(
            revision=theirs['revision'], review_token=theirs_review['token']))
        assert accepted.status_code == 201, accepted.text
        with pytest.raises(DoorError) as conflict:
            await door.submit(mine.run_id, mine.review.revision, mine.review.review_token, mine.review.reserved_usd)
        run = await door.run(mine.run_id)
    error = conflict.value
    assert error.kind == 'conflict' and error.line.startswith("Other work changed this project's spending since review")
    assert 'Reviewed again: ready false' in error.next_move and 'spending limit' in error.next_move
    assert run.state == 'draft' and run.cells == []          # nothing was sent


# --- errors: a fixed set of kinds, replay with the same delivery id ---

def scripted(*answers):
    """A transport that answers in turn; an exception is raised as the transport failing."""
    seen = []

    def handle(request: httpx.Request):
        seen.append(request)
        answer = answers[min(len(seen), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1'), seen


@pytest.mark.parametrize(('answer', 'kind', 'words'), [
    (httpx.Response(403, json={'detail': "Revealing a blind run is the artist's act."}), 'not_allowed', 'Propose'),
    (httpx.Response(404, json={'detail': 'Project not found.'}), 'not_found', 'Read the project'),
    (httpx.Response(409, json={'detail': 'Spending moved.'}, headers={'X-Crexil-Conflict': 'spending'}),
     'conflict', 'Spending moved since your review'),
    (httpx.Response(409, json={'detail': 'This direction changed.'}), 'conflict', 'new revision'),
    (httpx.Response(422, json={'detail': 'The reserved $1 exceeds the $0 spending limit.'}), 'refused', 'Nothing was'),
    (httpx.Response(422, json={'detail': [{'loc': ['body', 'name'], 'msg': 'Field required'}]}), 'refused', 'name: Field required'),
    (httpx.Response(501, json={'detail': 'Not built yet.'}), 'not_built', 'not built'),
    (httpx.Response(503, json={'detail': 'The project store is starting.'}), 'unavailable', 'crexil_status'),
    (httpx.Response(500, text='boom'), 'server_error', 'recorded the failure'),
])
async def test_every_answer_maps_to_one_kind_with_its_plain_line_first(answer, kind, words):
    client, _ = scripted(answer)
    with pytest.raises(DoorError) as raised:
        await door_over(client).create_project('Brass handle')
    assert raised.value.kind == kind and words in str(raised.value)
    assert str(raised.value).endswith(f'(kind: {kind})')


async def test_a_lost_answer_is_replayed_once_with_the_same_delivery_id_and_body():
    made = httpx.Response(201, json={'id': 'p1', 'name': 'Brass handle'})
    client, seen = scripted(httpx.ConnectError('dropped'), made)
    created = await door_over(client).create_project('Brass handle', 'A worn handle')
    first, second = (json.loads(r.content) for r in seen)
    assert first == second and first['request_id'] == created.delivery_id
    assert first['actor'] == AGENT and first['name'] == 'Brass handle'


async def test_an_unknown_outcome_names_the_delivery_id_to_replay_with():
    client, seen = scripted(httpx.ReadTimeout('no answer'))
    with pytest.raises(DoorError) as raised:
        await door_over(client).create_project('Brass handle', delivery_id='replay-me-0001')
    assert raised.value.kind == 'unreachable' and 'replay-me-0001' in raised.value.next_move
    assert len(seen) == 2 and all(json.loads(r.content)['request_id'] == 'replay-me-0001' for r in seen)


async def test_the_door_talks_to_loopback_only_and_always_as_a_named_agent():
    for url in ('http://example.com:8000', 'https://127.0.0.1:8000', 'http://user@127.0.0.1:8000'):
        with pytest.raises(ValueError):
            Door(url)
    with pytest.raises(ValueError):
        Door('http://127.0.0.1:8000', '  ')
    with pytest.raises(ValueError):
        Door('http://127.0.0.1:8000', 'Tester', -1)
    with pytest.raises(DoorError) as bad_id:
        await door_over(httpx.AsyncClient(base_url='http://127.0.0.1')).run('../projects')
    assert bad_id.value.kind == 'invalid_input'
    assert Door('http://localhost:8000', 'Claude').actor == {'kind': 'agent', 'name': 'Claude'}


def test_server_words_keep_their_meaning_without_an_address_or_a_path():
    plain = door_module.plain
    assert plain('The voice runner is not answering at http://127.0.0.1:8190.') == \
        'The voice runner is not answering at this machine.'
    assert plain('Sent to ElevenLabs (https://api.elevenlabs.io/v1/sound-generation).') == \
        'Sent to ElevenLabs (api.elevenlabs.io).'
    assert plain('Missing /home/someone/models/x.safetensors on disk') == 'Missing (path withheld) on disk'
    assert plain('No published price.') == 'No published price.' and plain(None) is None


async def test_a_status_read_reports_an_unreachable_server_without_failing():
    client, _ = scripted(httpx.ConnectError('refused'))
    status = await door_over(client).status()
    assert not status.reachable and status.agent == 'agent:Tester' and status.door_max_run_usd == 0


async def test_waiting_on_a_run_returns_when_it_settles(tmp_path, monkeypatch):
    async with booted_audio(tmp_path, monkeypatch) as (client, fakes):
        fakes['voice-runner'].delay_s = 0.2
        door = door_over(client)
        made = await door.create_project('Dwarf blacksmith')
        prepared = await door.prepare(made.project_id, 'voice-design', 'A gruff dwarf',
                                      [EngineChoice(id=f'audio:voice-design:{LOCAL}', controls={'preview_text': PREVIEW})], 0)
        await door.submit(prepared.run_id, prepared.review.revision, prepared.review.review_token, 0)
        ticks = []

        async def progress(done, total):
            ticks.append(done)
        run = await door.run(prepared.run_id, wait_seconds=10, progress=progress)
        draft_view = await door.run((await door.prepare(
            made.project_id, 'voice-design', 'Another', [EngineChoice(id=f'audio:voice-design:{LOCAL}',
                                                                      controls={'preview_text': PREVIEW})], 0)).run_id,
            wait_seconds=10)
    assert run.settled and run.cells[0].state == 'landed' and ticks
    assert draft_view.settled and draft_view.state == 'draft'      # a draft returns at once


def test_a_paid_step_is_never_the_default():
    """The free-to-run hint names $0 only when the review reserves nothing."""
    door = Door('http://127.0.0.1:8000', 'Tester', 1)
    base = {'direction_id': 'd1', 'revision': 0, 'token': 'a' * 64, 'ready': True, 'errors': [], 'attempts': 1,
            'ready_attempts': 1, 'blocked_attempts': 0, 'maximum_usd': None,
            'snapshot': {'setup': {'spending_policy': 'published_tariff'}, 'operations': []}}
    paid = door._review({**base, 'reserved_usd': 0.04, 'reserved_micro_usd': 40000})
    free = door._review({**base, 'reserved_usd': 0, 'reserved_micro_usd': 0})
    assert 'Ask the artist before spending' in paid.next and 'Free to run' in free.next
    assert paid.reserved_usd == 0.04 and paid.spending_policy == 'published_tariff'


def test_the_doors_money_reads_as_crexils_two_decimals_or_every_digit():
    dollars = door_module.dollars
    assert [dollars(Decimal(m)) for m in (0, 600_000, 40_000, 1_500_000)] == ['$0.00', '$0.60', '$0.04', '$1.50']
    assert [dollars(Decimal(m)) for m in (33_600, 4_080, 865_080)] == ['$0.0336', '$0.00408', '$0.86508']
