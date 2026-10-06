"""The agent door's server side: the compact run view, and the voice director's
sessions. The MCP server and the voice director are HTTP clients of these routes
and of the shared actions; neither writes the store (law 19).

The run view reads one direction's submissions, cells and takes in frozen grid
order (law 8), never the project snapshot. It carries facts, ids, labels and
reasons only: no take's bytes and no address to fetch them, because ElevenLabs'
terms forbid its audio as input to any machine learning and an agent's language
model is one. A blind run passes through
`blind.mask`, so engine, model, seed, per-take cost and timing stay withheld
until its authored reveal; run totals and the cap stay visible (law 22). Takes are
named the way the grid names them: run index, cell letter, and `.n` for a later
output of the same cell.

A voice session is started by the artist: it opens the microphone and spends
minutes. The server asks ElevenLabs for a short-lived conversation token, or a
signed URL, with the key; the key never leaves the server and the token is handed
over once and kept out of every receipt (law 18). Minutes are capped (law 22): an
open session counts its whole maximum until it ends, a session starts only when
the minutes left cover that maximum, and ElevenLabs is not asked when they do not.
Session starts and ends are events that name their author (law 23); they belong
to the installation, not to a project, so no project backup carries them.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import blind, config
from .crexil_budget import accounting
from .crexil_record import concerns_direction, decisions
from .crexil_store import Conflict, CreativeStore
from .db import new_id
from .execution_controls import state as control_state

DETAIL_LIMIT = 500
STATE_WORDS = {'landed': 'landed', 'running': 'running', 'queued': 'queued', 'late': 'late', 'held': 'held',
               'failed': 'failed', 'skipped': 'skipped', 'set_aside': 'set aside'}

# ElevenLabs' published list price per agent minute beyond a plan's included
# minutes, read from elevenlabs.io/pricing/api; model use is billed on top.
USD_PER_MINUTE = 0.08

# Tests replace the network with an httpx.MockTransport; nothing else sets it.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class ProviderRefused(Exception):
    """ElevenLabs did not hand over a conversation; `status` is the answer the
    route gives (422 for what the installation must fix, 503 for what may pass)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


# --- the run view ---

def label(turn_idx: int, pos: int, output_index: int = 0) -> str:
    """The grid's own name for a cell or a take: the run's index, then the cell's
    letters, "3B", or "3B.2" for a later output. Cells past Z continue as spreadsheet
    columns do, "3AA", "3AB", so every position of a run has its own name. The
    client's `takeLabel` spells the same names."""
    letters, rest = '', pos + 1
    while rest:
        rest, digit = divmod(rest - 1, 26)
        letters = chr(65 + digit) + letters
    return f'{turn_idx}{letters}' + (f'.{output_index + 1}' if output_index > 0 else '')


def _elapsed_ms(slot: dict, now: datetime) -> int | None:
    """Observed time on the take (law 17): until now while it runs, else until it finished."""
    if not slot['started_at']:
        return None
    started = datetime.fromisoformat(slot['started_at'])
    if slot['status'] == 'running':
        end = now
    elif slot['finished_at']:
        end = datetime.fromisoformat(slot['finished_at'])
    else:
        return None
    return max(0, int((end - started).total_seconds() * 1000))


def summary(cells: list[dict]) -> str:
    """One plain line for a run: counts by state, then each cause once (law 1)."""
    if not cells:
        return 'Not submitted yet.'
    counts = Counter(cell['state'] for cell in cells)
    words = [f'{counts[state]} {word}' for state, word in STATE_WORDS.items() if counts[state]]
    words += [f'{n} {state}' for state, n in counts.items() if state not in STATE_WORDS]
    line = ', '.join(words) + '.'
    causes = Counter(cell['cause'] for cell in cells if cell['state'] in ('failed', 'held') and cell['cause'])
    for cause, n in causes.items():
        line += ' ' + (cause if n == 1 else f'{cause.rstrip(".")} ({n} cells).')
    return line


AGENT_AUDIO = ('duration_s', 'loudness_lufs', 'text', 'voice')


def _take(node: dict, turn_idx: int, pos: int, selection: set[str], sounds: dict) -> dict:
    """A take as an agent reads it: facts, ids and labels, never bytes or an address
    to fetch them, so no take's audio reaches an agent's model."""
    facts = sounds.get(node['id'])
    return {'node_id': node['id'], 'label': label(turn_idx, pos, node['output_index'] or 0),
            'payload': node['payload'], 'verb': node['verb'],
            'available': not node['purged_at'] and Path(node['output_path']).is_file(),
            'set_aside': bool(node['set_aside']), 'chosen': node['id'] in selection,
            'audio': {key: facts.get(key) for key in AGENT_AUDIO} if facts else None}


async def _incomplete_attempts(db, turn_ids: list[str]) -> set[tuple[str, int]]:
    """Attempts whose reported files are missing and can be collected for free."""
    rows = await db._all(
        "SELECT g.slot_id, g.take FROM execution_groups g JOIN slots s ON s.id=g.slot_id "
        "WHERE s.turn_id IN (SELECT value FROM json_each(?)) AND g.state='incomplete' "
        "AND json_type(g.metadata_json,'$.archive_origin') IS NULL", (json.dumps(turn_ids),))
    return {(row['slot_id'], row['take']) for row in rows}


async def run_view(db, did: str) -> dict:
    """A `RunView` for one direction, read in one consistent pass."""
    creative = CreativeStore(db)
    async with db._locked():
        direction = await creative.direction(did)
        wid, setup = direction['workspace_id'], direction['setup'] or {}
        turns = sorted((t for t in [await db.get_turn(tid) for tid in direction['turn_ids']] if t),
                       key=lambda t: t['idx'])
        turn_ids = [t['id'] for t in turns]
        hidden = await blind.hidden_turns(db, wid)
        blind_runs = {run['turn_id']: run for run in await db.blind_runs(wid)}
        nodes = [n for n in await db.list_nodes(wid, include_purged=True) if n['turn_id'] in set(turn_ids)]
        sounds = await db.audio_facts(wid) if any(n['payload'] == 'audio' for n in nodes) else {}
        collectable = await _incomplete_attempts(db, turn_ids) if turn_ids else set()
        totals = (await accounting(db, wid, turn_ids=turn_ids) if turn_ids
                  else {'charged_usd': 0.0, 'reserved_usd': 0.0})
        controls = {tid: await control_state(db, 'run', tid) for tid in turn_ids}
        history = [item for item in await decisions(db, wid) if concerns_direction(item, direction)][-10:]
        seq = await db.latest_seq()

    selection = set(direction['selection'] or [])
    outputs = defaultdict(list)
    for node in sorted(nodes, key=lambda n: (n['output_index'] or 0, n['id'])):
        outputs[node['slot_id']].append(node)
    now = datetime.now(timezone.utc)
    cells = []
    for turn in turns:
        for slot in turn['slots']:
            takes = [_take(node, turn['idx'], slot['pos'], selection, sounds) for node in outputs[slot['id']]]
            cell = {'slot_id': slot['id'], 'turn_id': turn['id'], 'label': label(turn['idx'], slot['pos']),
                    'position': slot['pos'], 'state': slot['status'], 'attempt': slot['attempt'],
                    'retrying': bool(slot['retry_of']) and slot['status'] in ('queued', 'running'),
                    'kind': slot['kind'], 'cause': slot['cause'],
                    'detail': (slot['detail'] or '')[:DETAIL_LIMIT] or None,
                    'engine': slot['engine'], 'seed': slot['seed'],
                    'committed_usd': slot['committed_usd'], 'charged_usd': slot['charged_usd'],
                    'elapsed_ms': _elapsed_ms(slot, now),
                    'collectable': (slot['id'], slot['attempt']) in collectable, 'takes': takes}
            if turn['id'] in hidden:
                cell = {**blind.mask('slot', cell), 'elapsed_ms': None,
                        'takes': [blind.mask('node', take) for take in takes]}
            cells.append(cell)
    return {'run_id': did, 'project_id': wid, 'title': direction['title'], 'state': direction['state'],
            'revision': direction['revision'], 'action': setup.get('action') or 'generate',
            'prompt': setup.get('prompt') or '',
            'blind': bool(setup.get('blind')) or any(tid in blind_runs for tid in turn_ids),
            'cap_usd': float(setup.get('spending_limit_usd') or 0),
            'reserved_usd': totals['reserved_usd'], 'charged_usd': totals['charged_usd'],
            'submissions': [{'turn_id': t['id'], 'idx': t['idx'], 'created_at': t['created_at'],
                             'blind': t['id'] in blind_runs,
                             'revealed': bool(blind_runs.get(t['id'], {}).get('revealed')),
                             'control': controls[t['id']]} for t in turns],
            'cells': cells, 'selection': list(direction['selection'] or []), 'decisions': history,
            'summary': summary(cells), 'event_seq': seq}


# --- voice sessions ---

def _minutes(seconds: float) -> str:
    return f'{round(seconds / 60, 1):g} minutes'


def reasons() -> list[str]:
    """What the installation must provide before any voice session can start."""
    if config.CREXIL_PREVIEW:
        return ['Voice sessions are disconnected in this isolated preview.']
    found = []
    if not config.ELEVENLABS_API_KEY:
        found.append('Needs an ElevenLabs key. Set ELEVENLABS_API_KEY in .env, then restart.')
    if not config.ELEVENLABS_VOICE_AGENT_ID:
        found.append("Needs the voice director's agent. Apply tools/voice-director, set "
                     'ELEVENLABS_VOICE_AGENT_ID in .env, then restart.')
    if config.VOICE_MINUTES_CAP <= 0:
        found.append('The voice minutes budget is zero. Set CREXIL_VOICE_MINUTES_CAP in .env, then restart.')
    return found


async def sessions(db) -> list[dict]:
    """Every voice session with its end, read from its events."""
    rows = await db._all(
        "SELECT ts, type, payload_json FROM events WHERE type IN ('voice_session.started','voice_session.ended') "
        "AND json_type(payload_json,'$.archive_origin') IS NULL ORDER BY seq")
    found: dict[str, dict] = {}
    for row in rows:
        payload = json.loads(row['payload_json'])
        sid = payload['session_id']
        if row['type'] == 'voice_session.started':
            found[sid] = {'session_id': sid, 'project_id': payload.get('project_id'), 'actor': payload['actor'],
                          'started_at': row['ts'], 'max_seconds': payload['max_seconds'],
                          'connection': payload['connection'], 'ended_at': None, 'ended_by': None,
                          'elapsed_s': None, 'conversation_id': payload.get('conversation_id')}
        elif sid in found and found[sid]['ended_at'] is None:
            found[sid].update(ended_at=row['ts'], ended_by=payload['actor'], elapsed_s=payload['elapsed_s'],
                              conversation_id=payload.get('conversation_id') or found[sid]['conversation_id'])
    return list(found.values())


async def usage(db) -> dict:
    """A `VoiceUsage`: the minutes against the cap and whether a session may start."""
    found = await sessions(db)
    cap = int(config.VOICE_MINUTES_CAP * 60)
    used = float(sum(s['elapsed_s'] for s in found if s['ended_at'] is not None))
    reserved = sum(s['max_seconds'] for s in found if s['ended_at'] is None)
    left = cap - used - reserved
    why = reasons()
    if not why and left < config.VOICE_SESSION_MAX_S:
        why.append(f'{_minutes(max(left, 0))} of the voice budget is left and a session may run '
                   f'{_minutes(config.VOICE_SESSION_MAX_S)}. End an open session, or raise CREXIL_VOICE_MINUTES_CAP.')
    return {'ready': not why, 'reasons': why, 'cap_seconds': cap, 'session_max_seconds': config.VOICE_SESSION_MAX_S,
            'used_seconds': used, 'reserved_seconds': reserved, 'left_seconds': max(left, 0.0),
            'usd_per_minute': USD_PER_MINUTE, 'open_sessions': [s for s in found if s['ended_at'] is None]}


_REFUSALS = {
    401: (422, 'ElevenLabs refused the key. Check ELEVENLABS_API_KEY, then restart.'),
    402: (422, 'The ElevenLabs account has no credit for agent minutes.'),
    403: (422, 'This ElevenLabs key or plan cannot start agent conversations.'),
    404: (422, 'ElevenLabs does not know this agent. Apply tools/voice-director again and update '
               'ELEVENLABS_VOICE_AGENT_ID.'),
    429: (503, 'ElevenLabs asked to slow down. Try again in a moment.'),
}


async def mint(connection: str) -> dict:
    """Ask ElevenLabs for one conversation, with the key, outside any transaction.
    Nothing is charged until a conversation runs."""
    path, field = (('/v1/convai/conversation/token', 'token') if connection == 'webrtc'
                   else ('/v1/convai/conversation/get-signed-url', 'signed_url'))
    try:
        async with httpx.AsyncClient(base_url=config.ELEVENLABS_URL, transport=TRANSPORT,
                                     timeout=10.0, follow_redirects=False) as client:
            answer = await client.get(path, params={'agent_id': config.ELEVENLABS_VOICE_AGENT_ID},
                                      headers={'xi-api-key': config.ELEVENLABS_API_KEY})
    except httpx.HTTPError as exc:
        raise ProviderRefused('ElevenLabs could not be reached, so no voice session started.', 503) from exc
    if answer.status_code != 200:
        status, words = _REFUSALS.get(answer.status_code, (503, 'ElevenLabs could not start a conversation now.'))
        raise ProviderRefused(words + ' No voice session started.', status)
    try:
        data = answer.json()
    except ValueError:
        data = {}
    value = data.get(field) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise ProviderRefused('ElevenLabs answered without a conversation, so no voice session started.', 503)
    conversation = data.get('conversation_id')
    return {'conversation_token': value if connection == 'webrtc' else None,
            'signed_url': value if connection == 'websocket' else None,
            'conversation_id': conversation if isinstance(conversation, str) else None}


async def _session(db, sid: str) -> dict:
    found = next((s for s in await sessions(db) if s['session_id'] == sid), None)
    if found is None:
        raise LookupError('This voice session was not found.')
    return found


async def start_session(creative: CreativeStore, body) -> dict:
    """Start one voice session for the artist and return a `VoiceSessionStarted`.
    A replay of the same request returns the same session with a fresh token;
    the token is never part of the stored receipt."""
    db, scope, request = creative.db, 'voice_sessions.start', body.model_dump()
    previous = await creative.delivered(body.request_id, scope, request)
    if previous is not None:
        session = await _session(db, previous['session']['session_id'])
        if session['ended_at'] is not None:
            raise Conflict('This voice session already ended. Start a new one.')
        return {**previous, **_handover(await mint(body.connection)), 'session': session, 'usage': await usage(db)}
    if body.project_id is not None:
        await creative.project(body.project_id)
    allowed = await usage(db)
    if not allowed['ready']:
        raise ValueError(' '.join(allowed['reasons']))
    minted = await mint(body.connection)

    async def apply():
        allowed = await usage(db)
        if not allowed['ready']:
            raise ValueError(' '.join(allowed['reasons']))
        sid = new_id()
        await db.append_event('voice_session.started', payload={
            'session_id': sid, 'project_id': body.project_id, 'actor': body.actor.model_dump(),
            'max_seconds': config.VOICE_SESSION_MAX_S, 'connection': body.connection,
            'conversation_id': minted['conversation_id']})
        return {'session': await _session(db, sid), 'conversation_token': None, 'signed_url': None}

    receipt = await creative.command(body.request_id, scope, request, apply)
    return {**receipt, **_handover(minted), 'usage': await usage(db)}


def _handover(minted: dict) -> dict:
    return {'conversation_token': minted['conversation_token'], 'signed_url': minted['signed_url']}


async def end_session(creative: CreativeStore, sid: str, body) -> dict:
    """Record a session's end with the minutes the panel observed (law 17). A session
    already ended keeps its first end and author."""
    db = creative.db

    async def apply():
        session = await _session(db, sid)
        if session['ended_at'] is None:
            await db.append_event('voice_session.ended', payload={
                'session_id': sid, 'actor': body.actor.model_dump(), 'elapsed_s': body.elapsed_s,
                'conversation_id': body.conversation_id})
            session = await _session(db, sid)
        return session

    return await creative.command(body.request_id, 'voice_sessions.end:' + sid, body.model_dump(), apply)
