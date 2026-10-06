"""Every read route against hidden (blind, unrevealed) runs.

The sweep reads the ground truth from the store (engines, providers, labels,
models, job references, voice ids, seeds, and each take's own landing and run
times), then GETs every run-specific route the OpenAPI document lists, filled
with the hidden runs' ids, and fails on any body or header that carries one of
those facts. The one place a fact may stand is where a run lists the engines
it asked for (its operations), which blindness does not hide: the artist chose
them. A route or an event key added later is swept without anyone remembering
to mask it. The takes' own numbers are swept too: the real ElevenLabs and
local voice adapters, against a synthetic ElevenLabs and a voice runner served
on loopback with a scripted backend, land takes that read alike although only
the hosted one lands a receipt. Fakes only: no network beyond loopback, no GPU,
no key.
"""

import json
import os
import re
from datetime import datetime
from email.utils import formatdate

import httpx
import pytest

from app import config, local_voice, main
from app.elevenlabs import ElevenLabsExecutor
from app.executors import EngineFailure
from app.local_voice import VoiceRunnerExecutor
from fakes import FakeAudioExecutor, tone_wav
from test_api_v4 import store
from test_blind_everywhere import HOSTED, LOCAL, casting, prepared, ran, reveal
from test_blind_runs import MODELS
from test_crexil import command, project
from test_elevenlabs import BASE, FakeElevenLabs
from test_elevenlabs import designed as designed_previews
from test_local_voice import served

pytestmark = pytest.mark.anyio

# Routes that describe the installation, not a run: they name engines by design.
CATALOGUE = ('/api/health', '/api/presets', '/api/providers', '/api/setup/openrouter', '/api/crexil/operations',
             '/api/crexil/setup', '/api/crexil/operation-requirements', '/api/crexil/registrations',
             '/api/dials', '/api/engines', '/api/costs', '/api/headroom', '/api/mesh/health',
             '/api/promote-target', '/api/promote-browse', '/api/crexil/voice-sessions/usage')
# Where a fact may stand: a run's own list of the engines it asked for.
ASKED_FOR = re.compile(r'\.operations\[\]')


async def truth(turn_ids):
    """What would tell a hidden take's engine or landing order, read from the store."""
    db = store()
    slots = await db._all('SELECT * FROM slots WHERE turn_id IN (SELECT value FROM json_each(?))',
                          (json.dumps(turn_ids),))
    nodes = await db._all('SELECT * FROM nodes WHERE turn_id IN (SELECT value FROM json_each(?))',
                          (json.dumps(turn_ids),))
    terms = {'engine': {LOCAL, HOSTED}, 'provider': {'elevenlabs', 'voice-runner'},
             'label': {'Qwen', 'ElevenLabs'}, 'model': set(MODELS), 'job_ref': set(), 'seed': set(),
             'voice': set(), 'own time': set()}
    for slot in slots:
        if slot['job_ref']:
            terms['job_ref'].add(slot['job_ref'])
        if slot['seed'] is not None:
            terms['seed'].add(str(slot['seed']))
        terms['own time'] |= {slot[key] for key in ('started_at', 'finished_at') if slot[key]}
    for node in nodes:
        terms['own time'].add(node['created_at'])
    for facts in (await db.audio_facts(nodes[0]['workspace_id'])).values():
        voice = facts.get('voice') or {}
        terms['voice'] |= {v for k, v in voice.items() if k.endswith('_id') and isinstance(v, str) and v}
    return terms, [dict(s) for s in slots], [dict(n) for n in nodes]


def found(text, terms) -> set[str]:
    """The kinds of fact a text carries."""
    hits = set()
    for kind, values in terms.items():
        for value in values:
            pattern = rf'(?<![0-9]){re.escape(value)}(?![0-9])' if kind == 'seed' else re.escape(value)
            if re.search(pattern, text):
                hits.add(kind)
    return hits


def paths_of(value, terms, path='$'):
    """Each JSON path whose string value carries a fact, with the kinds it carries."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from paths_of(item, terms, f'{path}.{key}')
    elif isinstance(value, list):
        for item in value:
            yield from paths_of(item, terms, f'{path}[]')
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        hits = found(str(value), terms)
        if hits:
            yield path, hits


def body_of(response):
    kind = response.headers.get('content-type', '')
    if 'ndjson' in kind:
        return [json.loads(line) for line in response.text.splitlines() if line]
    if 'json' in kind:
        return response.json()
    if 'text' in kind or 'csv' in kind:
        return response.text
    return None                                       # the take's own bytes: blindness never hides them


def routes():
    return [path for path, ops in main.app.openapi()['paths'].items() if 'get' in ops and path not in CATALOGUE]


async def sweep(client, wid, did, turn_ids, nodes, outputs):
    """GET every run-specific route with the hidden runs' ids; yield (url, response)."""
    fills = {'{wid}': [wid], '{did}': [did], '{nid}': [n['id'] for n in nodes], '{oid}': outputs,
             '{scope}/{target_id}': [f'run/{t}' for t in turn_ids]}
    for path in routes():
        urls = [path]
        for key, values in fills.items():
            if key in path:
                urls = [u.replace(key, v) for u in urls for v in values]
        if '{' in ''.join(urls):
            continue                                   # an id these runs do not have (masks, backups, ...)
        for url in urls:
            params = {'since': 0, 'limit': 500} if url == '/api/events' else None
            yield url, await client.get(url, params=params)


async def test_every_read_route_of_a_hidden_run(tmp_path, monkeypatch):
    long = {'elevenlabs': FakeAudioExecutor('elevenlabs', 'external', data=tone_wav(seconds=3.5)),
            'voice-runner': FakeAudioExecutor('voice-runner', 'local', data=tone_wav(seconds=3.5))}
    async with casting(tmp_path, monkeypatch, **long) as (client, fakes):
        p = await project(client)
        direction = await prepared(client, p['id'], (LOCAL, 2), (HOSTED, 2))
        turn, takes = await ran(client, direction)
        # A pick and an assessment while hidden, so the record and its export have lines.
        snapshot = (await client.get(f"/api/crexil/projects/{p['id']}")).json()
        revision = next(d for d in snapshot['directions'] if d['id'] == direction['id'])['revision']
        picked = await client.put(f"/api/crexil/directions/{direction['id']}/selection", json=command(
            revision=revision, node_ids=[takes[0]['id']], reason='warmest'))
        assert picked.status_code == 200, picked.text
        assessed = await client.post(f"/api/crexil/projects/{p['id']}/assessments", json=command(
            node_ids=[takes[1]['id']], reason='too bright'))
        assert assessed.status_code == 201, assessed.text
        # A second hidden run whose hosted engine fails, so failure words are read too.
        fakes['elevenlabs'].failure = EngineFailure(
            'error', 'ElevenLabs refused: voice_not_found (eleven_ttv)',
            detail='{"detail": {"status": "voice_not_found", "message": "ElevenLabs"}}')
        failing, _ = await ran(client, await prepared(client, p['id'], (LOCAL, 1), (HOSTED, 1)),
                               until=('landed', 'failed', 'held'))

        turn_ids = [turn['id'], failing['id']]
        terms, slots, nodes = await truth(turn_ids)
        # The takes' files carry their own times a minute apart, as real landings would.
        base = 1_900_000_000
        for index, node in enumerate(sorted(nodes, key=lambda n: n['id'])):
            os.utime(node['output_path'], (base + 60 * index, base + 60 * index))
        terms['file time'] = {formatdate(base + 60 * i, usegmt=True) for i in range(len(nodes))}
        runs = {run['turn_id']: run['created_at']
                for run in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['blind_runs']}
        assert set(runs) == set(turn_ids)
        run_times = {formatdate(datetime.fromisoformat(at).timestamp(), usegmt=True) for at in runs.values()}
        outputs = [row['id'] for row in await store()._all(
            'SELECT o.id FROM execution_outputs o JOIN execution_groups g ON g.id=o.group_id '
            'WHERE g.slot_id IN (SELECT value FROM json_each(?)) AND o.node_id IS NOT NULL',
            (json.dumps([s['id'] for s in slots]),))]

        leaks, files, reads = [], 0, 0
        async for url, response in sweep(client, p['id'], direction['id'], turn_ids, nodes, outputs):
            reads += 1
            body = body_of(response)
            if isinstance(body, str):
                leaks += [(url, 'text', sorted(found(body, terms)))] if found(body, terms) else []
            elif body is not None:
                leaks += [(url, path, sorted(hits)) for path, hits in paths_of(body, terms)
                          if not ASKED_FOR.search(path)]
            heads = {key: response.headers[key] for key in ('content-disposition', 'last-modified', 'etag')
                     if key in response.headers}
            if found(json.dumps(heads), terms):
                leaks.append((url, 'headers', heads))
            if 'last-modified' in heads:
                files += 1
                assert heads['last-modified'] in run_times, (url, heads)    # the run's own time
        assert reads > 100 and files >= len(nodes)
        assert leaks == []

        # A run's takes keep its frozen, shuffled grid order, listed at the run's recorded creation time.
        position = {slot['id']: slot['pos'] for slot in slots}
        grid = [n['id'] for n in sorted(nodes, key=lambda n: (runs[n['turn_id']], position[n['slot_id']],
                                                              n['output_index']))]
        listed = [n['id'] for n in (await client.get(f"/api/crexil/projects/{p['id']}")).json()['nodes']]
        assert [nid for nid in listed if nid in set(grid)] == grid

        # Search never matches a hidden take by an identity word.
        hidden = {n['id'] for n in nodes}
        for word in sorted(terms['engine'] | terms['provider'] | {'qwen', 'eleven', 'take-1'}):
            answer = (await client.get(f"/api/crexil/projects/{p['id']}/record", params={'q': word})).json()
            assert not hidden & set(answer['material_ids']), word


async def told_apart(client, wid, did) -> dict:
    """What tells one run's cells apart, cell by cell: the run view's take labels
    after the cell's own, the take numbers, the take files its group lists as
    (ordinal, identity, name), and the name each file downloads under."""
    run = (await client.get(f'/api/crexil/directions/{did}/run')).json()
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    cells = {}
    for cell in run['cells']:
        (group,) = [g for g in snapshot['execution_groups'] if g['slot_id'] == cell['slot_id']]
        files = [o for o in group['outputs'] if o['node_id']]
        cells[cell['slot_id']] = {
            'labels': [take['label'].removeprefix(cell['label']) for take in cell['takes']],
            'numbers': sorted(n['output_index'] for n in snapshot['nodes'] if n['slot_id'] == cell['slot_id']),
            'files': [(o['ordinal'], o['identity'], o['filename']) for o in files],
            'downloads': [(await client.get(f"/api/crexil/outputs/{o['id']}/file")).headers['content-disposition']
                          for o in files]}
    return cells


async def test_a_blind_runs_hosted_and_local_takes_are_numbered_alike(tmp_path, monkeypatch):
    """ElevenLabs Voice Design lands its provider receipt before its three
    previews, and the local voice runner lands three takes and no receipt. A take
    is numbered among its attempt's takes only, so both engines' cells read alike:
    labels, take numbers, and the hidden file lists and download names."""
    monkeypatch.setattr(config, 'ELEVENLABS_URL', BASE)
    monkeypatch.setattr(local_voice, 'POLL_S', 0.01)
    fake = FakeElevenLabs().on('POST', '/v1/text-to-voice/design', designed_previews())
    with served(tmp_path) as (backend, _, base):
        engines = {'elevenlabs': ElevenLabsExecutor(transport=httpx.MockTransport(fake)),
                   'voice-runner': VoiceRunnerExecutor(base)}
        async with casting(tmp_path, monkeypatch, **engines) as (client, _):
            p = await project(client)
            direction = await prepared(client, p['id'], (LOCAL, 1), (HOSTED, 1))
            turn, takes = await ran(client, direction)
            assert len(takes) == 6 and len(fake.requests) == 1 and len(backend.calls) == 1

            # The store holds the difference: one receipt, the hosted attempt's, and it is no take.
            rows = await store()._all(
                'SELECT g.slot_id, o.ordinal, o.role, o.node_id FROM execution_outputs o JOIN execution_groups g '
                'ON g.id=o.group_id WHERE g.slot_id IN (SELECT value FROM json_each(?))',
                (json.dumps([slot['id'] for slot in turn['slots']]),))
            receipts = [row for row in rows if row['role'] == 'provider receipt']
            assert [(row['ordinal'], row['node_id']) for row in receipts] == [(0, None)]
            hosted = receipts[0]['slot_id']

            hidden = await told_apart(client, p['id'], direction['id'])
            alike = {'labels': ['', '.2', '.3'], 'numbers': [0, 1, 2],
                     'files': [(n, f'output:{n}', f'output-{n}.wav') for n in range(3)],
                     'downloads': [f'attachment; filename="output-{n}.wav"' for n in range(3)]}
            assert list(hidden.values()) == [alike, alike]

            # The reveal shows the receipt, and the takes keep the numbers they landed with.
            await reveal(client, turn['id'])
            shown = await told_apart(client, p['id'], direction['id'])
            for cell in shown.values():
                assert (cell['labels'], cell['numbers']) == (alike['labels'], alike['numbers'])
            assert [ordinal for ordinal, *_ in shown[hosted]['files']] == [1, 2, 3]
