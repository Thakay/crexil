"""The agent door's contracts: a compact, blind-safe run view; voice sessions whose
key stays on the server and whose minutes are capped before ElevenLabs is asked;
delivery kept a human act; and the host and origin guard on every route that
changes state or spends."""

import json

import httpx
import pytest
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app import agent_door, config, main
from app.executors import EngineFailure
from fakes import FakeAudioExecutor
from test_api_v4 import booted, store
from test_audio_contract import PREVIEW, booted_audio, draft, submit
from test_blind_runs import LOCAL, HOSTED, cast, casting, identity_terms, leaks, revision_of
from test_crexil import command, project

pytestmark = pytest.mark.anyio

AGENT = {'kind': 'agent', 'name': 'Claude'}
KEY = 'sk-test-voice-key-0123456789abcdef'


# --- the run view ---

async def run_of(client, did):
    answer = await client.get(f'/api/crexil/directions/{did}/run')
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_a_blind_run_view_names_no_engine_until_its_reveal_and_keeps_its_totals(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'])
        view = await run_of(client, direction['id'])
        assert not leaks(view, await identity_terms(turn))
        assert view['blind'] and view['state'] == 'submitted' and view['action'] == 'voice-design'
        assert [s['turn_id'] for s in view['submissions']] == [turn['id']]
        assert view['submissions'][0]['blind'] and not view['submissions'][0]['revealed']
        idx = view['submissions'][0]['idx']
        assert [c['position'] for c in view['cells']] == [0, 1]
        for cell in view['cells']:
            assert cell['identity_hidden'] and cell['engine'] == 'hidden'
            assert cell['seed'] is None and cell['committed_usd'] is None and cell['elapsed_ms'] is None
            assert cell['state'] == 'landed' and cell['label'] == f"{idx}{chr(65 + cell['position'])}"
            assert [t['label'] for t in cell['takes']] == [cell['label'], cell['label'] + '.2', cell['label'] + '.3']
            for take in cell['takes']:
                assert take['identity_hidden'] and take['payload'] == 'audio' and take['available']
                assert take['audio']['voice'] is None and take['audio']['duration_s'] > 0
        assert {t['node_id'] for c in view['cells'] for t in c['takes']} == {t['id'] for t in takes}
        assert view['cap_usd'] == 0 and view['charged_usd'] == 0 and view['reserved_usd'] == 0   # totals stay (law 22)
        assert view['summary'] == '2 landed.'

        revealed = await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command(reason='Decided'))
        assert revealed.status_code == 200, revealed.text
        seen = await run_of(client, direction['id'])
        assert seen['submissions'][0]['revealed']
        assert {c['engine'] for c in seen['cells']} == {LOCAL, HOSTED}
        assert all(c['identity_hidden'] is None for c in seen['cells'])
        assert all(t['audio']['voice'] for c in seen['cells'] for t in c['takes'])


async def test_the_run_view_holds_only_its_own_run_and_shows_picks_and_decisions(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'], blind_run=False, engines=(LOCAL,))
        other = await draft(client, p['id'], f'audio:voice-design:{LOCAL}', 'A second voice',
                            controls={'preview_text': PREVIEW})
        picked = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=await revision_of(client, p['id'], direction['id']), node_ids=[takes[1]['id']],
            reason='Warmest growl', actor=AGENT))
        assert picked.status_code == 200, picked.text
        view = await run_of(client, direction['id'])
        assert view['selection'] == [takes[1]['id']] and view['revision'] == picked.json()['revision']
        assert [t['chosen'] for t in view['cells'][0]['takes']] == [False, True, False]
        assert view['decisions'][-1]['kind'] == 'selection' and view['decisions'][-1]['actor'] == AGENT
        assert view['decisions'][-1]['reason'] == 'Warmest growl'
        assert view['cells'][0]['engine'] == LOCAL and view['cells'][0]['identity_hidden'] is None
        assert view['submissions'][0]['control']['revision'] == 0 and not view['submissions'][0]['control']['paused']
        assert view['event_seq'] > 0

        empty = await run_of(client, other['id'])
        assert empty['state'] == 'draft' and empty['cells'] == [] and empty['submissions'] == []
        assert empty['summary'] == 'Not submitted yet.' and empty['charged_usd'] == 0
        assert (await client.get('/api/crexil/directions/nothing-here/run')).status_code == 404


async def test_failed_cells_carry_their_kind_and_one_cause_line(tmp_path, monkeypatch):
    failing = {'voice-runner': FakeAudioExecutor('voice-runner', 'local',
                                                 failure=EngineFailure('error', 'Runner said no.'))}
    async with booted_audio(tmp_path, monkeypatch, executors=failing) as (client, _):
        p = await project(client)
        direction = await draft(client, p['id'], f'audio:voice-design:{LOCAL}', 'A gruff dwarf',
                                controls={'preview_text': PREVIEW}, count=2)
        await submit(client, direction, until={'failed'})
        view = await run_of(client, direction['id'])
    assert [c['state'] for c in view['cells']] == ['failed', 'failed']
    assert {c['kind'] for c in view['cells']} == {'error'} and all(c['cause'] for c in view['cells'])
    assert all(not c['takes'] and not c['retrying'] for c in view['cells'])
    cause = view['cells'][0]['cause']
    assert view['summary'] == f"2 failed. {cause.rstrip('.')} (2 cells)."


# --- what an agent reads holds no audio and no address to fetch any ---

# Bytes or addresses a language model could fetch or read as material: base64 or
# data URIs, and any route that serves a take's file.
MATERIAL_MARKS = ('data:', 'base64', '/api/nodes/', '/api/crexil/material/', '/outputs/', '/files/',
                  'http://', 'https://', 'wss://', '.wav', '.mp3', '.png', '.glb')


def material_marks(value) -> set[str]:
    text = json.dumps(value)
    return {mark for mark in MATERIAL_MARKS if mark in text}


async def test_what_an_agent_reads_carries_facts_never_audio_or_file_addresses(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, turn, takes = await cast(client, p['id'], blind_run=False)
        await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=await revision_of(client, p['id'], direction['id']), node_ids=[takes[0]['id']], reason='Warm',
            actor=AGENT))
        reads = {
            'run': (await client.get(f"/api/crexil/directions/{direction['id']}/run")).json(),
            'decisions': (await client.get(f"/api/crexil/projects/{p['id']}/decisions")).json(),
            'lines': (await client.get(f"/api/crexil/projects/{p['id']}/decisions.jsonl")).text,
            'search': (await client.get(f"/api/crexil/projects/{p['id']}/record", params={'q': 'Warm'})).json(),
            'usage': (await client.get('/api/crexil/voice-sessions/usage')).json(),
        }
    for name, value in reads.items():
        assert not material_marks(value), (name, material_marks(value))
    audio = reads['run']['cells'][0]['takes'][0]['audio']
    assert set(audio) == {'duration_s', 'loudness_lufs', 'text', 'voice'}       # no peaks, alignment or format


async def test_only_the_artist_reveals_a_blind_run(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        _direction, turn, _takes = await cast(client, p['id'])
        refused = await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command(actor=AGENT, reason='Curious'))
        revealed = [e for e in await store().events_since(0, workspace_id=p['id']) if e['type'] == 'run.revealed']
    assert refused.status_code == 403 and "artist's act" in refused.json()['detail']
    assert revealed == []


# --- the decision record names each decision's kind ---

async def test_the_decision_record_names_each_decisions_kind(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        direction, _turn, takes = await cast(client, p['id'], blind_run=False, engines=(LOCAL,))
        await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=await revision_of(client, p['id'], direction['id']), node_ids=[takes[0]['id']], reason='Warm'))
        await client.post(f"/api/crexil/projects/{p['id']}/assessments",
                          json=command(node_ids=[takes[1]['id']], reason='Too bright'))
        records = (await client.get(f"/api/crexil/projects/{p['id']}/decisions")).json()['records']
        lines = (await client.get(f"/api/crexil/projects/{p['id']}/decisions.jsonl")).text.splitlines()
        old = (await client.get(f"/api/crexil/projects/{p['id']}/preferences")).json()['records']
        old_lines = (await client.get(f"/api/crexil/projects/{p['id']}/preferences.jsonl")).text.splitlines()
    assert [r['kind'] for r in records] == ['selection', 'assessment']
    assert [json.loads(line)['kind'] for line in lines] == ['selection', 'assessment']
    assert records[1]['chosen'] == []
    assert old == records and old_lines == lines                  # the former address, kept for the CLI
    paths = main.app.openapi()['paths']
    assert all(paths[f'/api/crexil/projects/{{wid}}/{name}']['get'].get('deprecated') for name in ('preferences', 'preferences.jsonl'))
    assert not any(paths[f'/api/crexil/projects/{{wid}}/{name}']['get'].get('deprecated') for name in ('decisions', 'decisions.jsonl'))


# --- voice sessions ---

class FakeElevenLabs:
    """ElevenLabs' conversation endpoints, recorded; never the network."""

    def __init__(self, status=200, body=None, broken=False):
        self.status, self.body, self.broken = status, body, broken
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.broken:
            raise httpx.ConnectError('no route', request=request)
        n = len(self.calls)
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        if request.url.path.endswith('/get-signed-url'):
            return httpx.Response(self.status, json={'signed_url': f'wss://example.invalid/session-{n}'})
        return httpx.Response(self.status, json={'token': f'tok-{n}', 'conversation_id': f'conv-{n}'})


def voice(monkeypatch, *, cap_minutes=5.0, session_s=300, key=KEY, agent='agent_test_123', provider=None):
    provider = provider or FakeElevenLabs()
    monkeypatch.setattr(config, 'ELEVENLABS_API_KEY', key)
    monkeypatch.setattr(config, 'ELEVENLABS_VOICE_AGENT_ID', agent)
    monkeypatch.setattr(config, 'VOICE_MINUTES_CAP', cap_minutes)
    monkeypatch.setattr(config, 'VOICE_SESSION_MAX_S', session_s)
    monkeypatch.setattr(agent_door, 'TRANSPORT', httpx.MockTransport(provider))
    return provider


async def receipts_and_events():
    rows = await store()._all('SELECT response_json FROM command_receipts')
    events = await store()._all("SELECT payload_json FROM events WHERE type LIKE 'voice_session.%'")
    return ' '.join(r['response_json'] for r in rows), [json.loads(e['payload_json']) for e in events]


async def test_an_unconfigured_installation_says_what_a_voice_session_needs(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        voice(monkeypatch, cap_minutes=0, key='', agent='')
        usage = (await client.get('/api/crexil/voice-sessions/usage')).json()
        refused = await client.post('/api/crexil/voice-sessions', json=command())
    assert not usage['ready'] and len(usage['reasons']) == 3
    assert any('ELEVENLABS_API_KEY' in r for r in usage['reasons'])
    assert any('ELEVENLABS_VOICE_AGENT_ID' in r for r in usage['reasons'])
    assert any('CREXIL_VOICE_MINUTES_CAP' in r for r in usage['reasons'])
    assert refused.status_code == 422 and 'ELEVENLABS_API_KEY' in refused.json()['detail']
    monkeypatch.setattr(config, 'CREXIL_PREVIEW', True)
    assert agent_door.reasons() == ['Voice sessions are disconnected in this isolated preview.']


async def test_a_voice_session_hands_over_a_token_never_the_key_and_stores_no_token(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        provider = voice(monkeypatch)
        p = await project(client)
        body = command(project_id=p['id'])
        started = await client.post('/api/crexil/voice-sessions', json=body)
        assert started.status_code == 201, started.text
        again = await client.post('/api/crexil/voice-sessions', json=body)       # a lost answer, replayed
        stored, events = await receipts_and_events()
    first, second = started.json(), again.json()
    assert first['conversation_token'] == 'tok-1' and first['signed_url'] is None
    assert second['session']['session_id'] == first['session']['session_id'] and second['conversation_token'] == 'tok-2'
    assert first['session']['actor'] == body['actor'] and first['session']['project_id'] == p['id']
    assert first['session']['max_seconds'] == 300 and first['session']['conversation_id'] == 'conv-1'
    assert first['usage']['reserved_seconds'] == 300 and first['usage']['left_seconds'] == 0
    assert [e['session_id'] for e in events] == [first['session']['session_id']]     # one session, one event
    assert KEY not in started.text and KEY not in again.text and KEY not in stored
    assert 'tok-1' not in stored and 'tok-2' not in stored and 'tok-' not in json.dumps(events)
    call = provider.calls[0]
    assert call.url.path == '/v1/convai/conversation/token' and call.url.params['agent_id'] == 'agent_test_123'
    assert call.headers['xi-api-key'] == KEY


async def test_minutes_are_capped_before_elevenlabs_is_asked(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        provider = voice(monkeypatch, cap_minutes=5)
        first = (await client.post('/api/crexil/voice-sessions', json=command())).json()
        refused = await client.post('/api/crexil/voice-sessions', json=command())
        assert refused.status_code == 422 and len(provider.calls) == 1         # nothing asked of ElevenLabs
        assert 'End an open session' in refused.json()['detail']
        sid = first['session']['session_id']
        ended = await client.post(f'/api/crexil/voice-sessions/{sid}/end',
                                  json=command(elapsed_s=61.5, conversation_id='conv-1'))
        assert ended.status_code == 200, ended.text
        assert ended.json()['elapsed_s'] == 61.5 and ended.json()['ended_by']['kind'] == 'human'
        twice = await client.post(f'/api/crexil/voice-sessions/{sid}/end', json=command(elapsed_s=90, actor=AGENT))
        assert twice.json()['elapsed_s'] == 61.5 and twice.json()['ended_by']['kind'] == 'human'   # the first end stays
        usage = (await client.get('/api/crexil/voice-sessions/usage')).json()
        assert usage['used_seconds'] == 61.5 and usage['reserved_seconds'] == 0 and usage['open_sessions'] == []
        assert usage['left_seconds'] == 238.5 and not usage['ready'] and '4 minutes' in usage['reasons'][0]
        monkeypatch.setattr(config, 'VOICE_MINUTES_CAP', 10)
        assert (await client.post('/api/crexil/voice-sessions', json=command())).status_code == 201
        unknown = await client.post('/api/crexil/voice-sessions/nothing/end', json=command(elapsed_s=1))
        _, events = await receipts_and_events()
    assert unknown.status_code == 404
    assert [e['actor']['kind'] for e in events] == ['human', 'human', 'human']   # start, end, start


async def test_an_agent_never_starts_a_voice_session(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        provider = voice(monkeypatch)
        refused = await client.post('/api/crexil/voice-sessions', json=command(actor=AGENT))
        _, events = await receipts_and_events()
    assert refused.status_code == 403 and 'artist' in refused.json()['detail']
    assert provider.calls == [] and events == []


@pytest.mark.parametrize('provider, status, words', [
    (FakeElevenLabs(status=401, body={'detail': 'invalid_api_key'}), 422, 'refused the key'),
    (FakeElevenLabs(status=404, body={'detail': 'agent_not_found'}), 422, 'does not know this agent'),
    (FakeElevenLabs(status=429, body={'detail': 'too_many'}), 503, 'slow down'),
    (FakeElevenLabs(status=500, body={'detail': 'boom'}), 503, 'could not start a conversation'),
    (FakeElevenLabs(broken=True), 503, 'could not be reached'),
    (FakeElevenLabs(body={'conversation_id': 'conv-1'}), 503, 'without a conversation'),
])
async def test_an_elevenlabs_refusal_starts_nothing_and_says_so_plainly(tmp_path, monkeypatch, provider, status, words):
    async with booted(tmp_path, monkeypatch) as (client, _):
        voice(monkeypatch, provider=provider)
        refused = await client.post('/api/crexil/voice-sessions', json=command())
        usage = (await client.get('/api/crexil/voice-sessions/usage')).json()
        _, events = await receipts_and_events()
    assert refused.status_code == status and words in refused.json()['detail']
    assert 'No voice session started' in refused.json()['detail'] or 'no voice session started' in refused.json()['detail']
    assert KEY not in refused.text and events == [] and usage['reserved_seconds'] == 0


async def test_a_websocket_session_gets_a_signed_url(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        provider = voice(monkeypatch)
        started = await client.post('/api/crexil/voice-sessions', json=command(connection='websocket'))
    assert started.status_code == 201, started.text
    assert started.json()['signed_url'] == 'wss://example.invalid/session-1' and started.json()['conversation_token'] is None
    assert provider.calls[0].url.path == '/v1/convai/conversation/get-signed-url'


# --- delivery stays a human act (law 23) ---

async def test_an_agent_may_not_prepare_or_send_a_delivery(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        prepared = await client.post(f"/api/crexil/projects/{p['id']}/deliveries", json=command(
            actor=AGENT, node_ids=['any-take'], review_token='0' * 64))
        sent = await client.post('/api/crexil/deliveries/any-delivery/transfer', json=command(actor=AGENT))
        receipts, _ = await receipts_and_events()
        delivery_events = await store()._all("SELECT 1 FROM events WHERE type LIKE 'delivery.%'")
    assert prepared.status_code == 403 and sent.status_code == 403
    assert "artist's act" in prepared.json()['detail'] and 'the artist delivers' in sent.json()['detail']
    assert 'any-take' not in receipts and not delivery_events


# --- the host and origin guard ---

GUARDED = [('post', '/api/crexil/projects'), ('patch', '/api/crexil/projects/p1'),
           ('post', '/api/crexil/directions/d1/submit'), ('put', '/api/crexil/directions/d1/selection'),
           ('post', '/api/crexil/projects/p1/deliveries'), ('post', '/api/crexil/deliveries/x/transfer'),
           ('post', '/api/crexil/execution/run/t1'), ('post', '/api/crexil/turns/t1/reveal'),
           ('post', '/api/crexil/voice-sessions'), ('post', '/api/crexil/voice-sessions/s1/end'),
           ('post', '/api/crexil/attempts/s1/collect'), ('post', '/api/crexil/projects/p1/assessments')]


def test_the_guard_wraps_the_whole_app():
    installed = {m.cls for m in main.app.user_middleware}
    assert {TrustedHostMiddleware, main.LocalOriginMiddleware} <= installed


async def test_routes_that_change_state_or_spend_refuse_foreign_hosts_and_origins(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        for method, path in GUARDED:
            rebound = await client.request(method, path, json=command(), headers={'host': 'crexil.attacker.example'})
            foreign = await client.request(method, path, json=command(), headers={'origin': 'https://attacker.example'})
            sandboxed = await client.request(method, path, json=command(), headers={'origin': 'null'})
            local = await client.request(method, path, json=command(), headers={'origin': 'http://localhost:5173'})
            assert rebound.status_code == 400, (method, path, rebound.status_code)
            assert foreign.status_code == 403 and sandboxed.status_code == 403, (method, path)
            assert local.status_code not in (400, 403), (method, path, local.status_code)
