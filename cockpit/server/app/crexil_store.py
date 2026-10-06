"""Saved creative preparation and displayed choices beside the execution store.

Directions group a starter, submitted attempts and an explicit display selection.
Rows are presentation groups; the existing turn still owns frozen execution slots.
Draft revisions prevent stale participants overwriting each other. Command receipts
commit with mutations so repeating delivery cannot repeat an action. Submitted
setups and old decisions remain in events; changing a draft never rewrites a run.
Legacy work gains directions without changing nodes, files, events or manifest v4.
Unattributed legacy work stays unattributed. All writes use the server's one store.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_details(
 workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
 brief TEXT NOT NULL DEFAULT '',
 instructions TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0,
 limits_json TEXT NOT NULL DEFAULT '{}'
) STRICT;
CREATE TABLE IF NOT EXISTS directions(
 id TEXT PRIMARY KEY,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 row_idx INTEGER NOT NULL,
 title TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft'
   CHECK(state IN ('draft','submitted','imported','discarded')),
 setup_json TEXT NOT NULL DEFAULT '{}',
 selection_json TEXT NOT NULL DEFAULT '[]',
 revision INTEGER NOT NULL DEFAULT 0,
 set_aside INTEGER NOT NULL DEFAULT 0 CHECK(set_aside IN (0,1)),
 actor_json TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_directions_project ON directions(workspace_id,row_idx);
CREATE TABLE IF NOT EXISTS direction_turns(
 direction_id TEXT NOT NULL REFERENCES directions(id),
 turn_id TEXT NOT NULL UNIQUE REFERENCES turns(id),
 PRIMARY KEY(direction_id,turn_id)
) STRICT;
CREATE TABLE IF NOT EXISTS direction_material(
 direction_id TEXT NOT NULL REFERENCES directions(id),
 node_id TEXT NOT NULL UNIQUE REFERENCES nodes(id),
 PRIMARY KEY(direction_id,node_id)
) STRICT;
CREATE TABLE IF NOT EXISTS material_info(
 node_id TEXT PRIMARY KEY REFERENCES nodes(id),
 sha256 TEXT NOT NULL,
 filename TEXT NOT NULL,
 media_type TEXT NOT NULL,
 width INTEGER,
 height INTEGER,
 origin_json TEXT NOT NULL DEFAULT '{}'
) STRICT;
CREATE TABLE IF NOT EXISTS audio_info(
 node_id TEXT PRIMARY KEY REFERENCES nodes(id),
 container TEXT NOT NULL,
 codec TEXT NOT NULL,
 sample_rate INTEGER NOT NULL CHECK(sample_rate > 0),
 channels INTEGER NOT NULL CHECK(channels > 0),
 duration_s REAL NOT NULL CHECK(duration_s >= 0),
 loudness_lufs REAL,
 peak_dbfs REAL,
 peaks_json TEXT NOT NULL DEFAULT '[]',
 text TEXT,
 voice_json TEXT,
 alignment_json TEXT
) STRICT;
CREATE TRIGGER IF NOT EXISTS trg_audio_info_immutable BEFORE UPDATE ON audio_info BEGIN
 SELECT RAISE(ABORT, 'measured audio facts are immutable');
END;
CREATE TABLE IF NOT EXISTS blind_runs(
 turn_id TEXT PRIMARY KEY REFERENCES turns(id),
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 actor_json TEXT NOT NULL,
 created_at TEXT NOT NULL
) STRICT;
CREATE TRIGGER IF NOT EXISTS trg_blind_runs_immutable BEFORE UPDATE ON blind_runs BEGIN
 SELECT RAISE(ABORT, 'a blind run stays blind until its reveal event');
END;
CREATE TRIGGER IF NOT EXISTS trg_blind_runs_retained BEFORE DELETE ON blind_runs BEGIN
 SELECT RAISE(ABORT, 'blind runs are part of the record');
END;
CREATE TABLE IF NOT EXISTS execution_groups(
 id TEXT PRIMARY KEY,
 slot_id TEXT NOT NULL REFERENCES slots(id),
 take INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('collecting','complete','incomplete','uncertain')),
 job_ref TEXT,
 metadata_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(slot_id,take)
) STRICT;
CREATE TABLE IF NOT EXISTS execution_outputs(
 id TEXT PRIMARY KEY,
 group_id TEXT NOT NULL REFERENCES execution_groups(id),
 identity TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 node_id TEXT UNIQUE REFERENCES nodes(id),
 role TEXT NOT NULL DEFAULT 'unclassified',
 filename TEXT NOT NULL,
 media_type TEXT NOT NULL,
 output_path TEXT,
 sha256 TEXT,
 size_bytes INTEGER,
 descriptor_json TEXT NOT NULL DEFAULT '{}',
 error TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(group_id,identity),
 UNIQUE(group_id,ordinal)
) STRICT;
CREATE TRIGGER IF NOT EXISTS trg_outputs_no_delete BEFORE DELETE ON execution_outputs BEGIN
 SELECT RAISE(ABORT, 'collected output identity survives purge');
END;
CREATE TRIGGER IF NOT EXISTS trg_outputs_identity
BEFORE UPDATE OF id,group_id,identity,ordinal,node_id,role,filename,media_type ON execution_outputs
WHEN old.sha256 IS NOT NULL BEGIN
 SELECT RAISE(ABORT, 'collected output identity is immutable');
END;
CREATE TABLE IF NOT EXISTS command_receipts(
 request_id TEXT PRIMARY KEY,
 fingerprint TEXT NOT NULL,
 response_json TEXT NOT NULL,
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS operation_registrations(
 id TEXT PRIMARY KEY,
 source_name TEXT NOT NULL,
 source_text TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 source_kind TEXT NOT NULL,
 source_json TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS operation_revisions(
 registration_id TEXT NOT NULL REFERENCES operation_registrations(id),
 revision INTEGER NOT NULL,
 name TEXT NOT NULL,
 definition_json TEXT NOT NULL,
 binding_json TEXT NOT NULL,
 adaptation_note TEXT NOT NULL,
 actor_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(registration_id,revision)
) STRICT;
CREATE TRIGGER IF NOT EXISTS trg_operation_revision_immutable BEFORE UPDATE ON operation_revisions BEGIN
 SELECT RAISE(ABORT, 'operation revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_operation_revision_retained BEFORE DELETE ON operation_revisions BEGIN
 SELECT RAISE(ABORT, 'operation revisions are retained');
END;
CREATE TRIGGER IF NOT EXISTS trg_operation_source_immutable
BEFORE UPDATE OF id,source_name,source_text,source_sha256,source_kind,source_json,created_at ON operation_registrations BEGIN
 SELECT RAISE(ABORT, 'original operation definitions are immutable');
END;
CREATE TABLE IF NOT EXISTS operation_inspections(
 id TEXT PRIMARY KEY,
 registration_id TEXT NOT NULL REFERENCES operation_registrations(id),
 revision INTEGER NOT NULL,
 runtime_url TEXT NOT NULL,
 report_json TEXT NOT NULL,
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS mask_assets(
 id TEXT PRIMARY KEY,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 source_node_id TEXT NOT NULL REFERENCES nodes(id),
 source_sha256 TEXT NOT NULL,
 width INTEGER NOT NULL,
 height INTEGER NOT NULL,
 name TEXT NOT NULL,
 mask_path TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 original_path TEXT NOT NULL,
 original_sha256 TEXT NOT NULL,
 previous_mask_id TEXT REFERENCES mask_assets(id),
 transform_json TEXT NOT NULL,
 actor_json TEXT NOT NULL,
 created_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_mask_project ON mask_assets(workspace_id,created_at);
CREATE TRIGGER IF NOT EXISTS trg_mask_immutable BEFORE UPDATE ON mask_assets BEGIN
 SELECT RAISE(ABORT, 'source-bound mask versions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS trg_mask_retained BEFORE DELETE ON mask_assets BEGIN
 SELECT RAISE(ABORT, 'mask records survive material removal');
END;
CREATE TABLE IF NOT EXISTS sequences(
 id TEXT PRIMARY KEY,
 workspace_id TEXT NOT NULL REFERENCES workspaces(id),
 title TEXT NOT NULL,
 instructions TEXT NOT NULL DEFAULT '',
 limits_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','active','cancelled')),
 revision INTEGER NOT NULL DEFAULT 0,
 authorization_json TEXT,
 actor_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_sequences_project ON sequences(workspace_id,created_at);
CREATE TABLE IF NOT EXISTS sequence_steps(
 sequence_id TEXT NOT NULL REFERENCES sequences(id),
 key TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 direction_id TEXT NOT NULL UNIQUE REFERENCES directions(id),
 input_mode TEXT NOT NULL CHECK(input_mode IN ('together','each')),
 dependencies_json TEXT NOT NULL DEFAULT '[]',
 binding_json TEXT,
 decision_json TEXT,
 state TEXT NOT NULL DEFAULT 'waiting',
 reason TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(sequence_id,key)
) STRICT;
CREATE TABLE IF NOT EXISTS sequence_runs(
 sequence_id TEXT NOT NULL,
 step_key TEXT NOT NULL,
 input_key TEXT NOT NULL,
 direction_id TEXT NOT NULL UNIQUE REFERENCES directions(id),
 turn_id TEXT UNIQUE REFERENCES turns(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(sequence_id,step_key,input_key),
 FOREIGN KEY(sequence_id,step_key) REFERENCES sequence_steps(sequence_id,key)
) STRICT;
"""


class Conflict(ValueError):
    pass


async def migrate_projects(db: Database) -> None:
    """Add views of earlier projects without inventing choices or authors."""
    for project in await db.list_workspaces():
        wid = project['id']
        await db.conn.execute(
            "INSERT OR IGNORE INTO project_details(workspace_id) VALUES(?)", (wid,))
        for turn in await db.list_turns(wid):
            if await db._one('SELECT 1 FROM direction_turns WHERE turn_id=?', (turn['id'],)):
                continue
            did = 'legacy-' + turn['id']
            setup = {'prompt': turn['prompt'] or '', 'controls': turn['dials'],
                     'references': [], 'action': 'generate', 'operations': []}
            await db.conn.execute(
                "INSERT OR IGNORE INTO directions(id,workspace_id,row_idx,title,state,"
                "setup_json,created_at,updated_at) VALUES(?,?,?,?,'submitted',?,?,?)",
                (did, wid, turn['idx'], f"Direction {turn['idx'] + 1}",
                 json.dumps(setup), turn['created_at'], turn['created_at']))
            await db.conn.execute(
                "INSERT OR IGNORE INTO direction_turns VALUES(?,?)", (did, turn['id']))
        nodes = await db.list_nodes(wid, include_purged=True)
        for node in nodes:
            if node['turn_id']:
                continue
            if await db._one('SELECT 1 FROM direction_material WHERE node_id=?', (node['id'],)):
                continue
            did = 'legacy-' + node['id']
            await db.conn.execute(
                "INSERT OR IGNORE INTO directions(id,workspace_id,row_idx,title,state,"
                "setup_json,created_at,updated_at) VALUES(?,?,0,?,'imported',?,?,?)",
                (did, wid, 'Imported material', json.dumps({
                    'prompt': node['prompt'] or '', 'action': 'import',
                    'references': [{'node_id': p} for p in node['parents']]}),
                 node['created_at'], node['created_at']))
            await db.conn.execute(
                "INSERT OR IGNORE INTO direction_material VALUES(?,?)", (did, node['id']))


class CreativeStore:
    def __init__(self, db: Database):
        self.db = db

    async def delivered(self, request_id: str, scope: str, body: dict) -> dict | None:
        fingerprint = hashlib.sha256(json.dumps([scope, body], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        previous = await self.db._one('SELECT * FROM command_receipts WHERE request_id=?', (request_id,))
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise Conflict('This request ID already names different work. Refresh before making a new request.')
            return json.loads(previous['response_json'])
        return None

    async def command(self, request_id: str, scope: str, body: dict,
                      action: Callable[[], Awaitable[dict]]) -> dict:
        from .db import now_iso
        fingerprint = hashlib.sha256(json.dumps(
            [scope, body], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        async with self.db._tx():
            previous = await self.delivered(request_id, scope, body)
            if previous is not None:
                return previous
            result = await action()
            await self.db.conn.execute(
                'INSERT INTO command_receipts VALUES(?,?,?,?)',
                (request_id, fingerprint, json.dumps(result), now_iso()))
            return result

    async def project(self, wid: str) -> dict:
        project = await self.db.get_workspace(wid)
        if not project:
            raise LookupError('This project could not be found. Return to Projects.')
        detail = await self.db._one('SELECT * FROM project_details WHERE workspace_id=?', (wid,))
        return {**project, 'brief': detail['brief'] if detail else '',
                'instructions': detail['instructions'] if detail else '',
                'revision': detail['revision'] if detail else 0,
                'limits': json.loads(detail['limits_json']) if detail else {}}

    async def direction(self, did: str) -> dict:
        row = await self.db._one('SELECT * FROM directions WHERE id=?', (did,))
        if not row:
            raise LookupError('This direction could not be found. Refresh the project.')
        out = dict(row)
        for field in ('setup', 'selection', 'actor'):
            raw = out.pop(field + '_json')
            out[field] = json.loads(raw) if raw is not None else None
        out['set_aside'] = bool(out['set_aside'])
        turns = await self.db._all('SELECT turn_id FROM direction_turns WHERE direction_id=?', (did,))
        out['turn_ids'] = [r['turn_id'] for r in turns]
        nodes = await self.db._all(
            'SELECT node_id FROM direction_material WHERE direction_id=? UNION ALL '
            'SELECT id AS node_id FROM nodes WHERE turn_id IN '
            '(SELECT turn_id FROM direction_turns WHERE direction_id=?)', (did, did))
        out['node_ids'] = [r['node_id'] for r in nodes]
        membership = await self.db._one('SELECT sequence_id,key AS step_key,1 AS template FROM sequence_steps WHERE direction_id=? '
            'UNION ALL SELECT sequence_id,step_key,0 AS template FROM sequence_runs WHERE direction_id=?', (did, did))
        out.update(sequence_id=membership['sequence_id'] if membership else None,
                   sequence_step_key=membership['step_key'] if membership else None,
                   sequence_template=bool(membership['template']) if membership else False)
        return out

    async def snapshot(self, wid: str) -> dict:
        from . import blind
        from .material_files import view as retained_files
        from .api_v4 import _node_view, _turn_view
        from .execution_outputs import inventory
        from .crexil_budget import accounting
        from .execution_controls import state as control_state
        from .crexil_sequences import list_sequences
        from .crexil_restore import summary as restore_context
        from .crexil_record import concerns_direction, decisions
        # One read transaction prevents mismatched direction revisions and choices.
        async with self.db._locked():
            project = await self.project(wid)
            turns = await self.db.list_turns(wid)
            sequences = await list_sequences(self.db, wid)
            rows = await self.db._all(
                'SELECT id FROM directions WHERE workspace_id=? ORDER BY row_idx,created_at,id', (wid,))
            directions = [await self.direction(r['id']) for r in rows]
            history = await decisions(self.db, wid)
            for direction in directions:
                direction['latest_decision'] = next((item for item in reversed(history)
                    if concerns_direction(item, direction)), None)
            sounds = await self.db.audio_facts(wid)
            nodes = []
            for node in await self.db.list_nodes(wid, include_purged=True):
                info = await self.db._one('SELECT * FROM material_info WHERE node_id=?', (node['id'],))
                material = dict(info) if info else None
                if material:
                    material['origin'] = json.loads(material.pop('origin_json'))
                nodes.append({**_node_view(node), 'output_index': node['output_index'], 'purged_at': node['purged_at'],
                    'available': not node['purged_at'] and Path(node['output_path']).is_file(),
                    'material': material, 'retained_files': await retained_files(self.db, node),
                    'audio': sounds.get(node['id'])})
            hidden = await blind.hidden_turns(self.db, wid)
            slot_turns = {slot['id']: turn['id'] for turn in turns for slot in turn['slots']}
            views = [_turn_view(t) for t in turns]
            for view in views:
                if view['id'] in hidden:
                    view['slots'] = [blind.mask('slot', slot) for slot in view['slots']]
            groups = await inventory(self.db, wid)
            return {'project': project, 'directions': directions,
                    'nodes': await blind.apply(self.db, 'node', nodes, workspace_id=wid),
                    'turns': views,
                    'charged_usd': await self.db.workspace_charged(wid),
                    'accounting': await accounting(self.db, wid),
                    'execution_controls': [await control_state(self.db, 'project', wid),
                        *[await control_state(self.db, 'run', turn['id']) for turn in turns],
                        *[await control_state(self.db, 'sequence', sequence['id']) for sequence in sequences]],
                    'sequences': sequences,
                    'restoration': await restore_context(self.db, wid),
                    'event_seq': await self.db.latest_seq(),
                    'execution_groups': await blind.apply(self.db, 'group', groups, workspace_id=wid,
                                                          turn_of=lambda group: slot_turns.get(group['slot_id'])),
                    'blind_runs': await self.db.blind_runs(wid)}

    async def receive_material(self, wid: str, *, path: Path, sha256: str, filename: str,
                               media_type: str, payload: str, width: int | None,
                               height: int | None, origin: dict, parents: list[str],
                               direction_id: str | None = None) -> dict:
        """Attach independently owned imported bytes within the caller's transaction."""
        await self.project(wid)
        direction = await self.direction(direction_id) if direction_id else None
        if direction and direction['workspace_id'] != wid:
            raise ValueError('Import into a direction in this project.')
        explanation = origin.get('explanation', '')
        node = await self.db.import_node(wid, output_path=str(path), payload=payload,
                                         prompt=explanation, parents=parents)
        filename = Path(filename).name
        await self.db.conn.execute('INSERT INTO material_info VALUES(?,?,?,?,?,?,?)',
            (node['id'], sha256, filename, media_type, width, height, json.dumps(origin)))
        if direction is None:
            direction = await self.new_direction(wid, Path(filename).stem,
                {'action': 'import', 'prompt': explanation,
                 'references': [{'node_id': parent} for parent in parents]}, origin['actor'], state='imported')
        await self.db.conn.execute('INSERT INTO direction_material VALUES(?,?)', (direction['id'], node['id']))
        await self.db.append_event('material.received', workspace_id=wid, node_id=node['id'],
            payload={**origin, 'sha256': sha256, 'filename': filename,
                     'direction_id': direction['id'], 'parents': parents})
        return {'node': node, 'direction': await self.direction(direction['id'])}

    async def continuation_row(self, wid: str, sources: list[str]) -> int | None:
        """The row a continuation of these sources lands on: the row after the
        last of their rows, beside the family's other continuations; None when
        no source sits on a row. The client's `continuationRow` decides the same,
        so a continuation joins its family whoever creates it."""
        if not sources:
            return None
        ids = json.dumps(sources)
        row = await self.db._one(
            'SELECT MAX(row_idx) AS last FROM directions WHERE workspace_id=? AND id IN ('
            'SELECT direction_id FROM direction_material WHERE node_id IN (SELECT value FROM json_each(?)) UNION '
            'SELECT t.direction_id FROM direction_turns t JOIN nodes n ON n.turn_id=t.turn_id '
            'WHERE n.id IN (SELECT value FROM json_each(?)))', (wid, ids, ids))
        return None if row is None or row['last'] is None else row['last'] + 1

    async def new_direction(self, wid: str, title: str, setup: dict,
                            actor: dict, row_idx: int | None = None,
                            state: str = 'draft') -> dict:
        """A new direction on `row_idx`, or on a new last row."""
        from .db import new_id, now_iso
        await self.project(wid)
        if row_idx is None:
            row = await self.db._one(
                'SELECT COALESCE(MAX(row_idx),-1)+1 AS idx FROM directions WHERE workspace_id=?', (wid,))
            row_idx = row['idx']
        did, ts = new_id(), now_iso()
        await self.db.conn.execute(
            'INSERT INTO directions(id,workspace_id,row_idx,title,state,setup_json,actor_json,'
            'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (did, wid, row_idx, title, state, json.dumps(setup), json.dumps(actor), ts, ts))
        await self.db.append_event('direction.created', workspace_id=wid,
            payload={'direction_id': did, 'setup': setup, 'actor': actor})
        await self.db.touch_workspace(wid)
        return await self.direction(did)

    async def revise_direction(self, did: str, revision: int, changes: dict,
                               actor: dict, reason: str | None = None,
                               facts: dict | None = None) -> dict:
        """Apply one revision; `facts` are recorded on its event beside the change
        (for example that a selection was made while its run was blind)."""
        from .db import now_iso
        before = await self.direction(did)
        if before['revision'] != revision:
            raise Conflict('This direction changed in another view. Reload it before applying your changes.')
        if 'setup' in changes and before['state'] != 'draft':
            raise Conflict('This setup has already been submitted. Reuse it as a new direction to make changes.')
        for key, value in changes.items():
            column = {'setup': 'setup_json', 'selection': 'selection_json',
                      'title': 'title', 'state': 'state', 'set_aside': 'set_aside'}[key]
            if key in ('setup', 'selection'):
                value = json.dumps(value)
            await self.db.conn.execute(f'UPDATE directions SET {column}=? WHERE id=?', (value, did))
        await self.db.conn.execute(
            'UPDATE directions SET revision=revision+1,updated_at=? WHERE id=?', (now_iso(), did))
        await self.db.append_event('direction.changed', workspace_id=before['workspace_id'],
            payload={**(facts or {}), 'direction_id': did, 'revision': revision + 1, 'changes': changes,
                     'actor': actor, 'reason': reason})
        await self.db.touch_workspace(before['workspace_id'])
        return await self.direction(did)
