"""One server-owned SQLite record for projects, attempts and material.

Forward migrations refuse incompatible archives before changing them. Nodes
identify immutable material and provenance by slot, attempt and output index;
several collected images can belong to one attempt. Available incomplete work
can exist without claiming a successful execution. Only terminal collection
moves a slot to landed and records its charge, once for the whole execution.

Purge tombstones material and keeps lineage. Events are append-only; slot position,
turn and engine are frozen. Current group members share the slot's set-aside state
only when every member is aside. Older attempts never change a newer slot's state.
The event ledger owns spending totals; estimates and confirmed charges stay separate.

Every database statement uses the task-reentrant lock. Reads see committed state,
standalone writes never join another task's transaction, and nested mutations use
savepoints. Files belong to server storage callers; this module writes no artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import asyncio

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS workspaces(
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  slug          TEXT NOT NULL UNIQUE,
  viewport_json TEXT NOT NULL DEFAULT '{}',
  dials_json    TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS turns(
  id           TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id),
  idx          INTEGER NOT NULL,
  prompt       TEXT,
  dials_json   TEXT NOT NULL DEFAULT '{}',
  anchor_x     REAL,
  anchor_y     REAL,
  created_at   TEXT NOT NULL,
  UNIQUE(workspace_id, idx)
) STRICT;

CREATE TABLE IF NOT EXISTS slots(
  id                   TEXT PRIMARY KEY,
  turn_id              TEXT NOT NULL REFERENCES turns(id),
  pos                  INTEGER NOT NULL,
  engine               TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','running','landed','failed',
                     'held','skipped','set_aside','late')),
  attempt              INTEGER NOT NULL DEFAULT 0,
  seed                 INTEGER,
  dials_json           TEXT NOT NULL DEFAULT '{}',
  job_ref              TEXT,
  submitted_graph_json TEXT,
  external_json        TEXT,
  committed_usd        REAL,
  charged_usd          REAL,
  cause                TEXT,
  detail               TEXT,
  retry_of             TEXT,
  kind                 TEXT,
  frame                TEXT,
  facts_json           TEXT,
  started_at           TEXT,
  finished_at          TEXT,
  created_at           TEXT NOT NULL,
  UNIQUE(turn_id, pos)
) STRICT;

CREATE TABLE IF NOT EXISTS nodes(
  id                   TEXT PRIMARY KEY,
  workspace_id         TEXT NOT NULL REFERENCES workspaces(id),
  turn_id              TEXT REFERENCES turns(id),
  slot_id              TEXT REFERENCES slots(id),
  take                 INTEGER,
  output_index         INTEGER NOT NULL DEFAULT 0 CHECK(output_index >= 0),
  payload              TEXT NOT NULL CHECK(payload IN ('image','3d','audio')),
  verb                 TEXT NOT NULL,
  prompt               TEXT,
  engine               TEXT,
  seed                 INTEGER,
  dials_json           TEXT NOT NULL DEFAULT '{}',
  output_path          TEXT NOT NULL,
  submitted_graph_json TEXT,
  external_json        TEXT,
  duration_ms          INTEGER,
  committed_usd        REAL,
  charged_usd          REAL,
  set_aside            INTEGER NOT NULL DEFAULT 0 CHECK(set_aside IN (0,1)),
  x                    REAL,
  y                    REAL,
  purged_at            TEXT,
  created_at           TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS node_parents(
  node_id   TEXT NOT NULL REFERENCES nodes(id),
  parent_id TEXT NOT NULL REFERENCES nodes(id),
  ordinal   INTEGER NOT NULL,
  PRIMARY KEY(node_id, ordinal),
  UNIQUE(node_id, parent_id)
) STRICT;

-- Column names 'id', 'action', 'z_order', 'prompt', 'name' are the frozen
-- duck-type contract with app/regions.py. Do not rename.
CREATE TABLE IF NOT EXISTS regions(
  id                  TEXT PRIMARY KEY,
  node_id             TEXT NOT NULL REFERENCES nodes(id),
  name                TEXT NOT NULL,
  action              TEXT NOT NULL
    CHECK(action IN ('keep','modify','replace','delete')),
  prompt              TEXT,
  mask_path           TEXT NOT NULL,
  mask_sha256         TEXT NOT NULL,
  z_order             INTEGER NOT NULL,
  consumed_by_turn_id TEXT REFERENCES turns(id),
  created_at          TEXT NOT NULL
) STRICT;

-- Entity ids deliberately un-FK'd: an audit log must accept best-effort ids
-- (capture.py records unhandled failures) and its rows never cascade.
CREATE TABLE IF NOT EXISTS events(
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  type         TEXT NOT NULL,
  workspace_id TEXT,
  turn_id      TEXT,
  slot_id      TEXT,
  node_id      TEXT,
  charged_usd  REAL,
  payload_json TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE TABLE IF NOT EXISTS model_hashes(
  path   TEXT PRIMARY KEY,
  size   INTEGER NOT NULL,
  mtime  REAL NOT NULL,
  sha256 TEXT NOT NULL
) STRICT;

-- Plain content-full FTS (not external-content: that keys on rowid, nodes has
-- a TEXT PK, and VACUUM may renumber rowids -> silent index desync).
CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
  prompt,
  node_id UNINDEXED
);

CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_insert AFTER INSERT ON nodes BEGIN
  INSERT INTO node_fts(node_id, prompt) VALUES (new.id, new.prompt);
END;

CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_update
AFTER UPDATE OF prompt ON nodes BEGIN
  DELETE FROM node_fts WHERE node_id = old.id;
  INSERT INTO node_fts(node_id, prompt) VALUES (new.id, new.prompt);
END;

CREATE TRIGGER IF NOT EXISTS trg_nodes_fts_purge
AFTER UPDATE OF purged_at ON nodes
WHEN new.purged_at IS NOT NULL AND old.purged_at IS NULL BEGIN
  DELETE FROM node_fts WHERE node_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_update BEFORE UPDATE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete BEFORE DELETE ON events BEGIN
  SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_nodes_no_delete BEFORE DELETE ON nodes BEGIN
  SELECT RAISE(ABORT, 'nodes are never deleted; purge tombstones (law 7)');
END;

-- The record beneath a node is as permanent as the node: a turn is
-- the prompt's home, a slot is a take's (seed, attempts, failures).
CREATE TRIGGER IF NOT EXISTS trg_turns_no_delete BEFORE DELETE ON turns BEGIN
  SELECT RAISE(ABORT, 'turns are never deleted; the prompt is the record (law 7)');
END;

CREATE TRIGGER IF NOT EXISTS trg_slots_no_delete BEFORE DELETE ON slots BEGIN
  SELECT RAISE(ABORT, 'slots are never deleted; a take is a place (law 7)');
END;

-- Geometry is frozen at Run: a cell never moves, changes turn or
-- changes engine; only its contents (status, attempts, money) move.
CREATE TRIGGER IF NOT EXISTS trg_slots_frozen_geometry
BEFORE UPDATE OF pos, turn_id, engine ON slots BEGIN
  SELECT RAISE(ABORT, 'grid geometry is frozen at Run (law 8)');
END;

CREATE INDEX IF NOT EXISTS idx_slots_job_ref
  ON slots(job_ref) WHERE job_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_slots_pending
  ON slots(status) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_nodes_workspace ON nodes(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_nodes_tray
  ON nodes(workspace_id) WHERE set_aside = 1 AND purged_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_node_parents_parent ON node_parents(parent_id);
CREATE INDEX IF NOT EXISTS idx_regions_node ON regions(node_id, z_order);
CREATE INDEX IF NOT EXISTS idx_events_slot ON events(slot_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_ws ON events(workspace_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_charged
  ON events(ts) WHERE charged_usd IS NOT NULL;
-- The sidebar's promoted count reads the ledger on the poll cadence,
-- and `events` grows with every slot transition. Partial + covering: only
-- the promotion rows are indexed, and the count never touches the table.
CREATE INDEX IF NOT EXISTS idx_events_promoted
  ON events(node_id) WHERE type = 'node.promoted';
"""

from .crexil_store import SCHEMA as CREXIL_SCHEMA

SCHEMA += CREXIL_SCHEMA
SCHEMA_VERSION = "13"

# Slot lifecycle (PRD §2): to-status -> the from-statuses it is legal from.
# Policy detail (cancel semantics, late handling) is the scheduler's, in code;
# no schema rung needed to change this map.
ALLOWED = {
    "running":   {"queued"},
    "landed":    {"running", "late"},
    "failed":    {"running"},
    "held":      {"queued"},
    "queued":    {"held", "landed", "failed", "set_aside", "skipped",
                  "running"},  # running->queued: the scheduler's visible retry
    "skipped":   {"queued", "held"},
    "set_aside": {"landed"},
    "late":      {"running"},
}

# Statuses reroll_slot accepts as its starting point (reroll + backfill);
# held->queued is resume and goes through transition_slot instead.
REROLLABLE = {"landed", "failed", "set_aside", "skipped"}

# transition_slot's default for `cause`: the row keeps the cause it has.
KEEP_CAUSE = object()


def sha256_file(path: Path) -> str:
    """A whole file's SHA-256, read in chunks. Blocking: call it off the loop."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or new_id()


class SchemaVersionError(RuntimeError):
    """The file at the v4 path is not something schema v4 may open."""


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        # resolved at construction so tests pass tmp paths; the default is
        # COCKPIT_DB (the version guard refuses a pre-v4 file)
        self._path = Path(path) if path is not None else config.DB_PATH
        self._conn: aiosqlite.Connection | None = None
        self._tx_lock = asyncio.Lock()
        self._lock_owner: asyncio.Task | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "db not opened"
        return self._conn

    @property
    def opened(self) -> bool:
        """Open and not yet closed. The capture seam's guard: a
        failure that arrives after close() is logged, never written."""
        return self._conn is not None

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None -> autocommit; transactions are explicit (_tx)
        self._conn = await aiosqlite.connect(self._path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._guard_version()  # BEFORE any DDL or file-mutating pragma
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version',?)",
            (SCHEMA_VERSION,))
        await self._migrate()

    async def _guard_version(self) -> None:
        """Refuse anything that is not a fresh file or a v4+ladder file —
        run before executescript so a refused file is left untouched."""
        rows = await self._all(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'")
        tables = {r["name"] for r in rows}
        if not tables:
            return  # fresh file
        if "meta" not in tables:
            raise SchemaVersionError(
                f"{self._path} has tables but no version stamp — "
                "not a cockpit database; refusing to touch it")
        sealed = await self._one("SELECT value FROM meta WHERE key='archive_sealed'")
        if sealed:
            raise SchemaVersionError('This is a project backup. Restore it through Crexil instead of opening it as a live database.')
        row = await self._one(
            "SELECT value FROM meta WHERE key='schema_version'")
        stamp = row["value"] if row else None
        try:
            version = int(stamp)
        except (TypeError, ValueError):
            raise SchemaVersionError(
                f"{self._path} carries an unreadable schema stamp "
                f"({stamp!r}); refusing to touch it") from None
        if version < 4:
            raise SchemaVersionError(
                f"{self._path} is a pre-v4 archive (schema v{version}); "
                "schema v4 starts clean — point COCKPIT_DB at a "
                "fresh path (the cockpit's store is cockpit-v4.sqlite) and "
                "leave the archive where it is; nothing migrates it")
        if version > int(SCHEMA_VERSION):
            raise SchemaVersionError(
                f"{self._path} is schema v{version}, newer than this code "
                f"(v{SCHEMA_VERSION}); refusing to touch it")

    async def _migrate(self) -> None:
        """The forward-only ladder. v4 is the base rung. Each rung is
        one transaction — a crash mid-rung leaves the stamp behind, so the
        next open runs the same rung again rather than half of it twice."""
        row = await self._one(
            "SELECT value FROM meta WHERE key='schema_version'")
        version = row["value"]
        if version == "4":
            # v5: the failed cell's two faces (`cause` is the plain
            # line the artist reads, `detail` the raw engine text behind a
            # collapsed affordance, law 3) and `retry_of`, the kind an
            # automatic retry is answering, so the retry is visible on the
            # running cell and not only in the ledger (law 9).
            await self.conn.execute("BEGIN")
            try:
                await self.conn.execute(
                    "ALTER TABLE slots ADD COLUMN detail TEXT")
                await self.conn.execute(
                    "ALTER TABLE slots ADD COLUMN retry_of TEXT")
                await self.conn.execute(
                    "UPDATE meta SET value='5' WHERE key='schema_version'")
                await self.conn.execute("COMMIT")
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            version = "5"
        if version == "5":
            # v6: the failure KIND on the row. The face picks its
            # remedy by kind: a blank frame offers RUN AGAIN · SAME SEED,
            # a charged-but-blank paid take offers the receipt — and a
            # kind that lived only in the slot.failed event could not be
            # read back without walking the ledger per cell.
            await self.conn.execute("BEGIN")
            try:
                await self.conn.execute(
                    "ALTER TABLE slots ADD COLUMN kind TEXT")
                await self.conn.execute(
                    "UPDATE meta SET value='6' WHERE key='schema_version'")
                await self.conn.execute("COMMIT")
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            version = "6"
        if version == "6":
            # v7: the requested FRAME on the row ("1536x1536",
            # stamped at dispatch) — an OOM names the size it died at,
            # and "this engine held that size before" (A11) is a query
            # over landed rows, not a walk of the ledger — and `facts`,
            # the kind-specific structured facts the face's buttons need
            # (the smaller frame the record suggests, the take that held
            # the size), written by fail_slot beside the plain cause.
            await self.conn.execute("BEGIN")
            try:
                await self.conn.execute(
                    "ALTER TABLE slots ADD COLUMN frame TEXT")
                await self.conn.execute(
                    "ALTER TABLE slots ADD COLUMN facts_json TEXT")
                await self.conn.execute(
                    "UPDATE meta SET value='7' WHERE key='schema_version'")
                await self.conn.execute("COMMIT")
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            version = "7"
        if version == "7":
            from .crexil_store import migrate_projects
            async with self._tx():
                await migrate_projects(self)
                await self.conn.execute(
                    "UPDATE meta SET value='8' WHERE key='schema_version'")
            version = "8"
        if version == "8":
            async with self._tx():
                columns = {row['name'] for row in await self._all('PRAGMA table_info(nodes)')}
                if 'output_index' not in columns:
                    await self.conn.execute('ALTER TABLE nodes ADD COLUMN output_index INTEGER NOT NULL DEFAULT 0 CHECK(output_index >= 0)')
                await self.conn.execute('DROP INDEX IF EXISTS idx_nodes_slot_take')
                await self.conn.execute('CREATE UNIQUE INDEX idx_nodes_slot_take ON nodes(slot_id,take,output_index) WHERE slot_id IS NOT NULL')
                await self.conn.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
            version = "9"
        if version == "9":
            async with self._tx():
                await self.conn.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
            version = "10"
        if version == "10":
            async with self._tx():
                await self.conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
            version = "11"
        if version == "11":
            async with self._tx():
                await self.conn.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
            version = "12"
        if version == "12":
            await self._widen_node_payloads()
            version = "13"
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(f"unsupported schema version {version}")
        # Fresh stores already have the new column; existing stores gain it in
        # the rung above before this index can be created.
        await self.conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_slot_take ON nodes(slot_id,take,output_index) WHERE slot_id IS NOT NULL')

    async def _widen_node_payloads(self) -> None:
        """v13: nodes may hold audio. SQLite cannot alter a CHECK, so the
        table is rebuilt by SQLite's documented procedure: foreign keys off
        outside the transaction, copy every row by column name, drop, rename,
        recreate the table's own indexes and triggers, require an empty
        foreign key check, then stamp the version, all in one transaction.
        A store that already allows audio only gains the stamp."""
        row = await self._one("SELECT sql FROM sqlite_master WHERE type='table' AND name='nodes'")
        table = row['sql']
        before = "CHECK(payload IN ('image','3d'))"
        if "'audio'" not in table and table.count(before) != 1:
            raise SchemaVersionError(f"{self._path}: the nodes table has an unrecognised payload "
                                     "constraint; refusing to rebuild it")
        await self.conn.execute('PRAGMA foreign_keys=OFF')
        try:
            async with self._tx():
                if "'audio'" not in table:
                    extras = [r['sql'] for r in await self._all(
                        "SELECT sql FROM sqlite_master WHERE tbl_name='nodes' AND type IN ('index','trigger') "
                        "AND sql IS NOT NULL ORDER BY type, name")]
                    columns = ', '.join(r['name'] for r in await self._all('PRAGMA table_info(nodes)'))
                    widened = table.replace(before, "CHECK(payload IN ('image','3d','audio'))")
                    widened = re.sub(r'^CREATE TABLE\s+"?nodes"?', 'CREATE TABLE nodes_v13', widened, count=1)
                    await self.conn.execute(widened)
                    await self.conn.execute(f'INSERT INTO nodes_v13({columns}) SELECT {columns} FROM nodes')
                    await self.conn.execute('DROP TABLE nodes')
                    await self.conn.execute('ALTER TABLE nodes_v13 RENAME TO nodes')
                    for statement in extras:
                        await self.conn.execute(statement)
                problems = await self._all('PRAGMA foreign_key_check')
                if problems:
                    raise SchemaVersionError(f"{self._path}: {len(problems)} rows lost their references "
                                             "while widening node payloads; nothing was changed")
                await self.conn.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
        finally:
            await self.conn.execute('PRAGMA foreign_keys=ON')

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def _locked(self):
        """THE concurrency contract of this store, on one shared connection:
        every read observes committed state and every standalone write is
        isolated — without this, a bare INSERT executed while another task's
        transaction is open silently JOINS that transaction (and dies with
        its rollback), and reads see uncommitted rows. Task-reentrant so
        code inside a _tx body may call ordinary read/write helpers."""
        me = asyncio.current_task()
        if self._lock_owner is me:
            yield
            return
        async with self._tx_lock:
            self._lock_owner = me
            try:
                yield
            finally:
                self._lock_owner = None

    async def _one(self, sql: str, params=()) -> aiosqlite.Row | None:
        """Single-row read that fully drains and closes its cursor. A
        fetchone'd cursor left open is a statement 'in progress' and blocks
        COMMIT (python 3.12's sqlite3 no longer resets it for you)."""
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    async def _all(self, sql: str, params=()) -> list[aiosqlite.Row]:
        async with self._locked():
            cur = await self.conn.execute(sql, params)
            rows = await cur.fetchall()
            await cur.close()
            return list(rows)

    @asynccontextmanager
    async def _tx(self):
        """One logical transaction: multi-write methods (turn+slots, land,
        reroll, aside/restore, purge) commit whole or not at all. The lock
        serializes logical transactions across tasks on the one shared
        connection — aiosqlite serializes statements, not transactions."""
        async with self._locked():
            if self.conn.in_transaction:
                # Shared actions compose existing atomic store methods. A
                # savepoint lets an inner failure unwind without committing
                # the caller's receipt or any earlier part of its action.
                savepoint = "nested_" + uuid.uuid4().hex
                await self.conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    yield
                    await self.conn.execute(f"RELEASE {savepoint}")
                except BaseException:
                    await self.conn.execute(f"ROLLBACK TO {savepoint}")
                    await self.conn.execute(f"RELEASE {savepoint}")
                    raise
                return
            # Cancellation-safe: a task cancelled at any await here may have
            # had its statement already executed by aiosqlite's worker thread
            # (cancelling the future does not unqueue the work). BEGIN inside
            # the try means the except-branch ROLLBACK is queued AFTER it and
            # always closes the transaction instead of leaking it open.
            try:
                await self.conn.execute("BEGIN IMMEDIATE")
                yield
                # the commit METHOD (not execute("COMMIT")) — it resets any
                # pending cursors first; a raw COMMIT statement fails with
                # "SQL statements in progress" if a RETURNING/SELECT cursor
                # was not fully drained
                await self.conn.commit()
            except BaseException:
                with suppress(Exception):  # no tx open (BEGIN never ran / already rolled back)
                    await self.conn.rollback()
                raise

    # --- workspaces ---

    async def create_workspace(self, name: str, slug: str | None = None) -> dict:
        wid, ts = new_id(), now_iso()
        async with self._locked():
            await self.conn.execute(
                "INSERT INTO workspaces(id, name, slug, created_at, updated_at)"
                " VALUES(?,?,?,?,?)",
                (wid, name, slug or slugify(name), ts, ts))
        return await self.get_workspace(wid)

    async def get_workspace(self, wid: str) -> dict | None:
        row = await self._one("SELECT * FROM workspaces WHERE id=?", (wid,))
        return self._ws_row(row) if row else None

    # The list carries the two counts the sidebar row draws: live
    # nodes, and how many of them have left for the asset tree. They ride
    # the list query rather than N detail fetches from the client.
    # `promoted` counts NODES that were promoted, not promotions:
    # re-promoting the same take is another ledger row and the same asset,
    # so DISTINCT is the honest count. A purged node keeps its
    # ledger row — lineage outlives purge (law 7) — but it is gone from the
    # workspace, so both counts filter it.
    #
    # Grouped joins, NOT correlated subqueries: the sidebar list is
    # invalidated by every workspace verb (invalidate.ts), so this runs on
    # the poll cadence. Per-row subqueries would re-scan `events` once per
    # workspace each time — with idx_events_promoted this is one indexed
    # pass over each table however many workspaces there are.
    _WS_COUNTS_JOIN = (
        " LEFT JOIN (SELECT workspace_id, COUNT(*) AS c FROM nodes"
        "   WHERE purged_at IS NULL GROUP BY workspace_id) nc"
        "  ON nc.workspace_id = w.id"
        " LEFT JOIN (SELECT n.workspace_id, COUNT(DISTINCT e.node_id) AS c"
        "   FROM events e JOIN nodes n ON n.id = e.node_id"
        "   WHERE e.type='node.promoted' AND n.purged_at IS NULL"
        "   GROUP BY n.workspace_id) pc"
        "  ON pc.workspace_id = w.id")
    _WS_COUNTS = " COALESCE(nc.c, 0) AS nodes, COALESCE(pc.c, 0) AS promoted"

    async def list_workspaces(self) -> list[dict]:
        rows = await self._all(
            "SELECT w.*," + self._WS_COUNTS + " FROM workspaces w"
            + self._WS_COUNTS_JOIN + " ORDER BY w.updated_at DESC")
        return [self._ws_row(r) for r in rows]

    async def touch_workspace(self, wid: str) -> None:
        async with self._locked():
            await self.conn.execute(
                "UPDATE workspaces SET updated_at=? WHERE id=?",
                (now_iso(), wid))

    async def rename_workspace(self, wid: str, name: str) -> None:
        # slug survives a rename: promote paths stay stable
        async with self._locked():
            await self.conn.execute(
                "UPDATE workspaces SET name=?, updated_at=? WHERE id=?",
                (name, now_iso(), wid))

    async def set_workspace_view(self, wid: str, viewport: dict | None = None,
                                 dials: dict | None = None) -> None:
        sets, vals = ["updated_at=?"], [now_iso()]
        if viewport is not None:
            sets.append("viewport_json=?")
            vals.append(json.dumps(viewport))
        if dials is not None:
            sets.append("dials_json=?")
            vals.append(json.dumps(dials))
        vals.append(wid)
        async with self._locked():
            await self.conn.execute(
                f"UPDATE workspaces SET {', '.join(sets)} WHERE id=?", vals)

    # NOTE: no delete_workspace — Purge is the only delete verb and it is
    # node-scoped (law 7). Deliberate omission; no route may re-add it.

    # --- turns + slots ---

    async def next_turn_idx(self, wid: str) -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(idx), -1) + 1 FROM turns WHERE workspace_id=?",
            (wid,))
        return row[0]

    async def create_turn_with_slots(
            self, wid: str, prompt: str | None, dials: dict | None,
            slots: list[dict],
            anchor: tuple[float, float] | None = None) -> dict:
        """One Run = one turn of N slots, atomically — the grid exists whole
        or not at all (law 8). `slots` is ordered {engine, seed?, dials?,
        committed_usd?}; positions are list order."""
        if not slots:
            raise ValueError("a turn needs at least one slot")
        tid, ts = new_id(), now_iso()
        async with self._tx():
            idx = await self.next_turn_idx(wid)
            ax, ay = anchor if anchor else (None, None)
            await self.conn.execute(
                "INSERT INTO turns(id, workspace_id, idx, prompt, dials_json,"
                " anchor_x, anchor_y, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (tid, wid, idx, prompt, json.dumps(dials or {}), ax, ay, ts))
            for pos, spec in enumerate(slots):
                # prepared payloads persist at CREATION so a queued slot
                # survives restart; dispatch_slot re-stamps at
                # dispatch time (what was submitted is what's stored)
                graph = spec.get("submitted_graph")
                ext = spec.get("external")
                await self.conn.execute(
                    "INSERT INTO slots(id, turn_id, pos, engine, seed,"
                    " dials_json, submitted_graph_json, external_json,"
                    " committed_usd, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (new_id(), tid, pos, spec["engine"], spec.get("seed"),
                     json.dumps(spec.get("dials") if spec.get("dials")
                                is not None else (dials or {})),
                     json.dumps(graph) if graph is not None else None,
                     json.dumps(ext) if ext is not None else None,
                     spec.get("committed_usd"), ts))
            await self._append_event(
                "turn.created", workspace_id=wid, turn_id=tid,
                payload={"slots": len(slots),
                         "engines": [s["engine"] for s in slots]})
        return await self.get_turn(tid)

    async def get_turn(self, tid: str) -> dict | None:
        row = await self._one("SELECT * FROM turns WHERE id=?", (tid,))
        if not row:
            return None
        turn = self._turn_row(row)
        rows = await self._all(
            "SELECT * FROM slots WHERE turn_id=? ORDER BY pos", (tid,))
        turn["slots"] = [self._slot_row(r) for r in rows]
        return turn

    async def list_turns(self, wid: str) -> list[dict]:
        rows = await self._all(
            "SELECT id FROM turns WHERE workspace_id=? ORDER BY idx", (wid,))
        return [await self.get_turn(r["id"]) for r in rows]

    async def get_slot(self, slot_id: str) -> dict | None:
        row = await self._one("SELECT * FROM slots WHERE id=?", (slot_id,))
        return self._slot_row(row) if row else None

    async def _require_slot(self, slot_id: str) -> dict:
        slot = await self.get_slot(slot_id)
        if slot is None:
            raise ValueError(f"unknown slot {slot_id}")
        return slot

    @staticmethod
    def _guard_transition(slot: dict, to_status: str) -> None:
        if slot["status"] not in ALLOWED[to_status]:
            raise ValueError(
                f"illegal slot transition: {slot['status']} -> {to_status}"
                f" (slot {slot['id']})")

    async def _slot_ids(self, slot: dict) -> dict:
        """workspace/turn ids for a slot's events."""
        row = await self._one(
            "SELECT workspace_id FROM turns WHERE id=?", (slot["turn_id"],))
        return {"workspace_id": row["workspace_id"] if row else None,
                "turn_id": slot["turn_id"], "slot_id": slot["id"]}

    async def dispatch_slot(self, slot_id: str, *, job_ref: str | None = None,
                            submitted_graph: dict | None = None,
                            external: dict | None = None,
                            committed_usd: float | None = None,
                            seed: int | None = None,
                            frame: str | None = None) -> dict:
        """queued -> running; bumps attempt and stamps the dispatch-time
        provenance (the exact submission survives a crash before land).
        `frame` is the requested size, "WxH", kept across
        attempts unless a re-dispatch names a new one."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            from .crexil_restore import guard as guard_restored
            guard_restored(slot)
            self._guard_transition(slot, "running")
            from .execution_controls import guard_dispatch as guard_control
            await guard_control(self, slot)
            from .crexil_budget import guard_dispatch
            await guard_dispatch(self, slot)
            attempt = slot["attempt"] + 1
            await self.conn.execute(
                "UPDATE slots SET status='running', attempt=?, started_at=?,"
                " finished_at=NULL, cause=NULL, detail=NULL, facts_json=NULL,"
                " job_ref=?,"
                " submitted_graph_json=COALESCE(?, submitted_graph_json),"
                " external_json=COALESCE(?, external_json),"
                " committed_usd=COALESCE(?, committed_usd), seed=COALESCE(?, seed),"
                " frame=COALESCE(?, frame)"
                " WHERE id=?",
                (attempt, now_iso(), job_ref,
                 json.dumps(submitted_graph) if submitted_graph else None,
                 json.dumps(external) if external else None,
                 committed_usd, seed, frame, slot_id))
            ids = await self._slot_ids(slot)
            await self._append_event(
                "slot.dispatched", **ids,
                payload={"engine": slot["engine"], "attempt": attempt,
                         "seed": seed if seed is not None else slot["seed"],
                         "committed_usd": (committed_usd
                                           if committed_usd is not None
                                           else slot["committed_usd"])})
        return await self.get_slot(slot_id)

    async def fail_slot(self, slot_id: str, *, cause: str,
                        charged_usd: float | None = None,
                        detail: str | None = None,
                        kind: str | None = None,
                        facts: dict | None = None) -> dict:
        """running -> failed. The event carries the full failure record: cause,
        kind, attempt, engine, timestamp, cost. `cause` is the plain line the
        cell shows (law 3: the scheduler words it per kind); `detail` is the
        raw engine text, kept on the row for the cell's collapsed affordance
        and in the event forever; `kind` stays on the row too so the
        face can pick its remedy without walking the ledger. charged_usd
        here IS the ledger entry (B4: a failed external attempt may still
        bill — `charged_blank` arrives with the amount the response
        reported). `facts` are the kind-specific structured facts
        the face's buttons read — an OOM's suggested smaller frame, the
        take that held the size — stored beside the cause and in the
        event."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            self._guard_transition(slot, "failed")
            await self.conn.execute(
                "UPDATE slots SET status='failed', cause=?, detail=?,"
                " kind=?, facts_json=?, finished_at=?,"
                " charged_usd=COALESCE(?, charged_usd)"
                " WHERE id=?",
                (cause, detail, kind, json.dumps(facts) if facts else None,
                 now_iso(), charged_usd, slot_id))
            ids = await self._slot_ids(slot)
            await self._append_event(
                "slot.failed", **ids, charged_usd=await self._unrecorded_charge(slot, charged_usd),
                payload={"cause": cause, "kind": kind,
                         "attempt": slot["attempt"],
                         "engine": slot["engine"], "detail": detail,
                         "frame": slot["frame"], "facts": facts,
                         "committed_usd": slot["committed_usd"]})
        return await self.get_slot(slot_id)

    async def transition_slot(self, slot_id: str, to_status: str,
                              *, payload: dict | None = None,
                              retry_of: str | None = None,
                              cause: str | None | object = KEEP_CAUSE) -> dict:
        """The generic guarded move (held / skipped / late / resume).
        `retry_of` marks a running -> queued move as the scheduler's own
        automatic retry of that failure kind (law 9): it stays on the row
        through the next dispatch so the cell says RETRYING while the
        attempt runs, and only a reroll — the artist's verb — clears it.
        `cause`, when given, is the row's plain line in the same statement
        as the status: a held slot says why it waits, and None clears it
        when the slot moves on. Left out, the row keeps its cause."""
        if to_status not in ALLOWED:
            raise ValueError(f"unknown slot status {to_status!r}")
        async with self._tx():
            slot = await self._require_slot(slot_id)
            self._guard_transition(slot, to_status)
            if cause is KEEP_CAUSE:
                await self.conn.execute(
                    "UPDATE slots SET status=?, retry_of=COALESCE(?, retry_of)"
                    " WHERE id=?", (to_status, retry_of, slot_id))
            else:
                await self.conn.execute(
                    "UPDATE slots SET status=?, retry_of=COALESCE(?, retry_of),"
                    " cause=? WHERE id=?", (to_status, retry_of, cause, slot_id))
            ids = await self._slot_ids(slot)
            await self._append_event(
                f"slot.{to_status}", **ids,
                payload={"from": slot["status"], **(payload or {})})
        return await self.get_slot(slot_id)

    async def reroll_slot(self, slot_id: str, new_seed: int | None,
                          *, committed_usd: float | None = None,
                          dials: dict | None = None) -> dict:
        """Reroll: same slot, same engine, same dials, new seed, back to
        queued. Old takes are node rows and stay reachable (law 7); backfill
        is this, called per emptied slot. `dials` (A1's retry at a
        smaller frame) rewrites the cell's dials so the new size IS the
        cell's from here on; the old take's node keeps its own."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            from .crexil_restore import guard as guard_restored
            guard_restored(slot)
            if slot["status"] not in REROLLABLE:
                raise ValueError(
                    f"illegal slot transition: {slot['status']} -> queued"
                    f" (reroll; slot {slot_id})")
            await self.conn.execute(
                "UPDATE slots SET status='queued', seed=?, job_ref=NULL,"
                " submitted_graph_json=NULL, external_json=NULL, cause=NULL,"
                " detail=NULL, retry_of=NULL, kind=NULL, facts_json=NULL,"
                " started_at=NULL, finished_at=NULL, committed_usd=?,"
                " dials_json=COALESCE(?, dials_json) WHERE id=?",
                (new_seed, committed_usd,
                 json.dumps(dials) if dials is not None else None, slot_id))
            ids = await self._slot_ids(slot)
            await self._append_event(
                "slot.rerolled", **ids,
                payload={"engine": slot["engine"], "seed": new_seed,
                         "attempt": slot["attempt"]})
        return await self.get_slot(slot_id)

    async def pending_slots(self) -> list[dict]:
        """Restart recovery (the scheduler consumes it): everything not in
        a resting state when the process died."""
        rows = await self._all(
            "SELECT * FROM slots WHERE status IN ('queued','running')"
            " ORDER BY created_at")
        return [self._slot_row(r) for r in rows]

    async def active_slots(self) -> list[dict]:
        """The queue view's durable half: every slot the artist
        is still waiting on — queued/running plus parked `held` and resting-
        but-unresolved `late` — with the owning workspace and turn index
        joined in (slot rows don't carry workspace_id)."""
        rows = await self._all(
            "SELECT s.*, t.workspace_id AS workspace_id, t.idx AS turn_idx"
            " FROM slots s JOIN turns t ON t.id = s.turn_id"
            " WHERE s.status IN ('queued','running','held','late')"
            " ORDER BY s.created_at, s.pos")
        return [self._slot_row(r) for r in rows]

    async def set_job_ref(self, slot_id: str, job_ref: str) -> None:
        """Stamp the executor job id once it exists — ComfyUI's prompt_id is
        only known after the POST, and the scheduler dispatches BEFORE
        submitting (running-without-job_ref = crashed pre-submit, safe to
        requeue). Guarded to running slots; no event (slot.dispatched
        recorded intent)."""
        async with self._locked():
            cur = await self.conn.execute(
                "UPDATE slots SET job_ref=? WHERE id=? AND status='running'",
                (job_ref, slot_id))
            if cur.rowcount == 0:
                raise ValueError(
                    f"slot {slot_id} is not running; job_ref not set")

    async def set_late_job_ref(self, slot_id: str, attempt: int, job_ref: str) -> None:
        """Stamp the runtime reference a stopped call reported after the
        attempt went late, so recovery and the bill check can find it.
        Guarded to that attempt while it is still late; otherwise a no-op."""
        async with self._locked():
            await self.conn.execute(
                "UPDATE slots SET job_ref=? WHERE id=? AND attempt=? AND status='late'",
                (job_ref, slot_id, attempt))

    async def slots_by_status(self, status: str,
                              *, engine: str | None = None) -> list[dict]:
        """The scheduler's bench sweeps: the queued slots of a benched
        engine, the held slots at unbench time."""
        q, vals = "SELECT * FROM slots WHERE status=?", [status]
        if engine:
            q += " AND engine=?"
            vals.append(engine)
        rows = await self._all(q + " ORDER BY created_at", vals)
        return [self._slot_row(r) for r in rows]

    async def frames_landed(self, engine: str) -> list[dict]:
        """The record's answer to "has this engine held a frame on this
        card" (A1/A11): every distinct frame a slot
        of this engine LANDED at, with the earliest turn/slot that held it
        and how many times. Landed or set aside — both are takes that
        landed; a purged take still landed once."""
        rows = await self._all(
            "SELECT s.frame AS frame, COUNT(*) AS n, MIN(t.idx) AS turn_idx,"
            " MIN(s.pos) AS pos"
            " FROM slots s JOIN turns t ON t.id = s.turn_id"
            " WHERE s.engine=? AND s.frame IS NOT NULL"
            " AND s.status IN ('landed','set_aside')"
            " GROUP BY s.frame", (engine,))
        return [dict(r) for r in rows]

    async def failed_in_turn(self, turn_id: str, engine: str,
                             kinds: tuple[str, ...]) -> list[dict]:
        """A9: this engine's failed slots in this turn, of the
        engine-level kinds — the second one benches it for the turn."""
        marks = ",".join("?" for _ in kinds)
        rows = await self._all(
            f"SELECT * FROM slots WHERE turn_id=? AND engine=?"
            f" AND status='failed' AND kind IN ({marks}) ORDER BY pos",
            (turn_id, engine, *kinds))
        return [self._slot_row(r) for r in rows]

    async def landed_sizes(self, limit: int = 50) -> list[dict]:
        """E6's typical-output-size input: the newest landed
        local takes' output paths with their frames — the caller stats
        the files (bytes per pixel is a fact of this card's outputs, not
        an authored number)."""
        rows = await self._all(
            "SELECT n.output_path AS output_path, s.frame AS frame"
            " FROM nodes n JOIN slots s ON s.id = n.slot_id"
            " WHERE n.purged_at IS NULL AND s.frame IS NOT NULL"
            " AND n.engine NOT LIKE '%/%'"
            " ORDER BY n.created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # --- nodes ---

    async def land_node(self, slot_id: str, *, payload: str, verb: str,
                        output_path: str, prompt: str | None = None,
                        engine: str | None = None, seed: int | None = None,
                        dials: dict | None = None,
                        parents: list[str] | None = None,
                        submitted_graph: dict | None = None,
                        external: dict | None = None,
                        duration_ms: int | None = None,
                        committed_usd: float | None = None,
                        charged_usd: float | None = None,
                        x: float | None = None, y: float | None = None,
                        output_index: int = 0, complete: bool = True,
                        expected_attempt: int | None = None) -> dict:
        """Record a collected material member, optionally completing its attempt.

        Intermediate collection never changes slot status or bills. Repeating
        collection at the same index and path reuses its identity. Completion
        retains the existing terminal-transition guard, so one execution bills
        once regardless of how many files it returned.
        """
        nid, ts = new_id(), now_iso()
        async with self._tx():
            slot = await self._require_slot(slot_id)
            if expected_attempt is not None and slot['attempt'] != expected_attempt:
                raise ValueError('This output belongs to an earlier attempt; the current request is unchanged.')
            if complete:
                self._guard_transition(slot, 'landed')
            elif slot['status'] not in {'running', 'late', 'failed', 'landed', 'set_aside'}:
                raise ValueError('Material cannot be collected before its attempt starts.')
            if not complete and charged_usd is not None:
                raise ValueError('Collection does not record a charge. Complete or fail the execution once.')
            ids = await self._slot_ids(slot)
            previous = await self._one('SELECT id,output_path FROM nodes WHERE slot_id=? AND take=? AND output_index=?',
                                       (slot_id, slot['attempt'], output_index))
            if previous:
                if previous['output_path'] != output_path:
                    raise ValueError('This output index already identifies different material.')
                nid = previous['id']
            else:
                graph_src = submitted_graph if submitted_graph is not None else slot['submitted_graph']
                graph = json.dumps(graph_src) if graph_src is not None else None
                ext_src = external if external is not None else slot['external']
                ext = json.dumps(ext_src) if ext_src is not None else None
                await self.conn.execute(
                    'INSERT INTO nodes(id,workspace_id,turn_id,slot_id,take,output_index,'
                    'payload,verb,prompt,engine,seed,dials_json,output_path,submitted_graph_json,'
                    'external_json,duration_ms,committed_usd,charged_usd,x,y,created_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (nid, ids['workspace_id'], slot['turn_id'], slot_id, slot['attempt'], output_index,
                     payload, verb, prompt, engine or slot['engine'],
                     seed if seed is not None else slot['seed'],
                     json.dumps(dials if dials is not None else slot['dials']), output_path,
                     graph, ext, duration_ms,
                     committed_usd if committed_usd is not None else slot['committed_usd'],
                     charged_usd, x, y, ts))
                await self._insert_parents(nid, parents or [])
            if complete:
                visible = await self._one('SELECT 1 FROM nodes WHERE slot_id=? AND take=? AND set_aside=0 AND purged_at IS NULL LIMIT 1',
                                          (slot_id, slot['attempt']))
                await self.conn.execute(
                    "UPDATE slots SET status=?,finished_at=?,charged_usd=COALESCE(?,charged_usd) WHERE id=?",
                    ('landed' if visible else 'set_aside', ts, charged_usd, slot_id))
                if previous:
                    # Completion fills execution facts, never image bytes or
                    # generative inputs already attached to the material.
                    await self.conn.execute('UPDATE nodes SET duration_ms=COALESCE(duration_ms,?),charged_usd=COALESCE(charged_usd,?) WHERE id=?',
                                             (duration_ms, charged_usd, nid))
                await self._append_event('node.landed', **ids, node_id=nid, charged_usd=await self._unrecorded_charge(slot, charged_usd),
                    payload={'engine': engine or slot['engine'], 'take': slot['attempt'],
                             'duration_ms': duration_ms, 'output_index': output_index})
            elif not previous:
                if slot['status'] == 'set_aside':
                    await self.conn.execute("UPDATE slots SET status='landed' WHERE id=?", (slot_id,))
                await self._append_event('node.collected', **ids, node_id=nid,
                    payload={'engine': engine or slot['engine'], 'take': slot['attempt'],
                             'output_index': output_index, 'complete': False})
        return await self.get_node(nid)

    async def complete_files(self, slot_id: str, attempt: int, *, charged_usd: float | None = None) -> None:
        """Complete a file-only operation without inventing an image or mesh node."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            if slot['attempt'] != attempt:
                raise ValueError('These files belong to an earlier attempt.')
            self._guard_transition(slot, 'landed')
            await self.conn.execute("UPDATE slots SET status='landed',finished_at=?,charged_usd=COALESCE(?,charged_usd) WHERE id=?",
                                    (now_iso(), charged_usd, slot_id))
            await self._append_event('slot.files_landed', **await self._slot_ids(slot), charged_usd=await self._unrecorded_charge(slot, charged_usd),
                                    payload={'attempt': attempt, 'job_ref': slot['job_ref']})

    async def recovered_collection(self, slot_id: str, attempt: int) -> None:
        """Verified runtime success may repair collection uncertainty, never bill again."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            if slot['attempt'] != attempt:
                raise ValueError('The request now has another attempt. Its state was not changed.')
            if slot['status'] != 'failed' or slot['kind'] not in {'collection', 'uncertain', 'timeout', 'restart', 'charged_blank'}:
                return
            visible = await self._one('SELECT 1 FROM nodes WHERE slot_id=? AND take=? AND set_aside=0 AND purged_at IS NULL LIMIT 1',
                                      (slot_id, attempt))
            if not visible and not await self._one('SELECT 1 FROM nodes WHERE slot_id=? AND take=? LIMIT 1', (slot_id, attempt)):
                visible = await self._one('SELECT 1 FROM execution_outputs o JOIN execution_groups g ON g.id=o.group_id WHERE g.slot_id=? AND g.take=? AND o.sha256 IS NOT NULL LIMIT 1', (slot_id, attempt))
            await self.conn.execute("UPDATE slots SET status=?,cause=NULL,kind=NULL,detail=NULL WHERE id=?",
                                    ('landed' if visible else 'set_aside', slot_id))
            await self._append_event('slot.collection_recovered', **await self._slot_ids(slot),
                payload={'attempt': attempt, 'job_ref': slot['job_ref'], 'generated_again': False})

    async def import_node(self, wid: str, *, output_path: str,
                          payload: str = "image", prompt: str | None = None,
                          parents: list[str] | None = None,
                          x: float | None = None,
                          y: float | None = None) -> dict:
        """an imported image enters the graph as a plain node — no turn,
        no slot, no take."""
        nid, ts = new_id(), now_iso()
        async with self._tx():
            await self.conn.execute(
                "INSERT INTO nodes(id, workspace_id, payload, verb, prompt,"
                " output_path, x, y, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (nid, wid, payload, "import", prompt, output_path, x, y, ts))
            await self._insert_parents(nid, parents or [])
            await self._append_event(
                "node.imported", workspace_id=wid, node_id=nid,
                payload={"payload": payload})
        return await self.get_node(nid)

    async def _insert_parents(self, nid: str, parents: list[str]) -> None:
        for ordinal, pid in enumerate(parents):
            await self.conn.execute(
                "INSERT INTO node_parents(node_id, parent_id, ordinal)"
                " VALUES(?,?,?)", (nid, pid, ordinal))

    # Promotion is an append-only FACT in the ledger (no column on
    # nodes — a node is immutable but for its position), so the two
    # readers the wire uses join the first `node.promoted` row back in.
    # First, not last: the moment the asset left for the tree is the one
    # the mark means; a re-promotion is another row, not a new answer.
    _PROMOTED_JOIN = (
        " LEFT JOIN (SELECT node_id, MIN(ts) AS promoted_at FROM events"
        "  WHERE type='node.promoted' AND node_id IS NOT NULL"
        "  GROUP BY node_id) pr ON pr.node_id = n.id")

    # A blind run's takes keep its frozen, shuffled grid order on every list,
    # revealed or not, never the order they landed in, which tells its engines
    # apart: they sort at the run's creation time, by cell, then output. Lists
    # that use it join `slots s` and `blind_runs b`.
    _BLIND_JOIN = " LEFT JOIN blind_runs b ON b.turn_id = n.turn_id"
    _IN_RUN_ORDER = "CASE WHEN b.turn_id IS NULL THEN NULL ELSE s.pos END, n.output_index"

    async def get_node(self, nid: str) -> dict | None:
        """Purged nodes are returned too — lineage outlives purge (law 7)."""
        row = await self._one(
            "SELECT n.*, pr.promoted_at FROM nodes n"
            + self._PROMOTED_JOIN + " WHERE n.id=?", (nid,))
        if not row:
            return None
        node = self._node_row(row)
        await self._attach_parents([node])
        return node

    async def list_nodes(self, wid: str, include_purged: bool = False) -> list[dict]:
        """The canvas feed: node rows plus the two label keys the card
        footer needs: `turn_idx` and `slot_pos` (the card's letters are SLOT
        letters: `12b` = turn 12, slot b). Imported nodes LEFT-JOIN to NULLs.
        Other node readers stay unjoined. Oldest first; a blind run's takes
        in its grid order."""
        q = ("SELECT n.*, t.idx AS turn_idx, s.pos AS slot_pos,"
             " pr.promoted_at FROM nodes n"
             " LEFT JOIN turns t ON t.id = n.turn_id"
             " LEFT JOIN slots s ON s.id = n.slot_id"
             + self._BLIND_JOIN + self._PROMOTED_JOIN +
             " WHERE n.workspace_id=?")
        if not include_purged:
            q += " AND n.purged_at IS NULL"
        rows = await self._all(q + " ORDER BY COALESCE(b.created_at, n.created_at), " + self._IN_RUN_ORDER, (wid,))
        nodes = [self._node_row(r) for r in rows]
        await self._attach_parents(nodes)
        return nodes

    # --- audio facts and blind runs ---

    _AUDIO_COLUMNS = ("container", "codec", "sample_rate", "channels", "duration_s",
                      "loudness_lufs", "peak_dbfs", "peaks", "text", "voice", "alignment")

    async def record_audio_facts(self, node_id: str, facts: dict) -> None:
        """Store an audio take's measured and reported facts once; a repeated
        collection of the same material keeps the first record."""
        values = [facts.get(key) for key in self._AUDIO_COLUMNS]
        for index in (7, 9, 10):              # peaks, voice, alignment are JSON
            values[index] = json.dumps(values[index]) if values[index] is not None else None
        values[7] = values[7] or "[]"
        async with self._locked():
            await self.conn.execute(
                "INSERT OR IGNORE INTO audio_info(node_id,container,codec,sample_rate,channels,duration_s,"
                "loudness_lufs,peak_dbfs,peaks_json,text,voice_json,alignment_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (node_id, *values))

    async def audio_facts(self, workspace_id: str) -> dict[str, dict]:
        """Every audio take's facts in one project, by node id."""
        rows = await self._all("SELECT a.* FROM audio_info a JOIN nodes n ON n.id=a.node_id "
                               "WHERE n.workspace_id=?", (workspace_id,))
        facts = {}
        for row in rows:
            item = dict(row)
            item["peaks"] = json.loads(item.pop("peaks_json"))
            for key in ("voice", "alignment"):
                raw = item.pop(key + "_json")
                item[key] = json.loads(raw) if raw is not None else None
            facts[item.pop("node_id")] = item
        return facts

    async def audio_fact(self, node_id: str) -> dict | None:
        node = await self.get_node(node_id)
        return (await self.audio_facts(node["workspace_id"])).get(node_id) if node else None

    async def material_origin(self, node_id: str) -> dict:
        """A material's recorded origin (how it entered the project, and any
        provider terms on it), or an empty record when none was kept."""
        row = await self._one("SELECT origin_json FROM material_info WHERE node_id=?", (node_id,))
        return json.loads(row["origin_json"]) if row else {}

    async def saved_voice(self, provider: str, generated_voice_id: str) -> str | None:
        """The provider voice saved from this designed voice: the save kept the
        moment the provider named it (`voice.saved`), else the voice an earlier
        speech take landed with."""
        row = await self._one(
            "SELECT json_extract(payload_json,'$.voice_id') AS voice_id FROM events WHERE type='voice.saved' "
            "AND json_extract(payload_json,'$.provider')=? "
            "AND json_extract(payload_json,'$.generated_voice_id')=? ORDER BY seq DESC LIMIT 1",
            (provider, generated_voice_id))
        if row and row["voice_id"]:
            return row["voice_id"]
        row = await self._one(
            "SELECT json_extract(voice_json,'$.voice_id') AS voice_id FROM audio_info "
            "WHERE json_extract(voice_json,'$.provider')=? "
            "AND json_extract(voice_json,'$.from_generated_voice_id')=? "
            "AND json_extract(voice_json,'$.voice_id') IS NOT NULL ORDER BY rowid DESC LIMIT 1",
            (provider, generated_voice_id))
        return row["voice_id"] if row else None

    async def record_saved_voice(self, slot: dict, provider: str, generated_voice_id: str, voice_id: str,
                                 *, found: bool) -> None:
        """Keep, once, the provider voice an attempt learned for a designed
        voice: one it just saved, or (`found`) one already in the account under
        its Crexil label. The event names the attempt, so the voice slot it used
        has an author (law 23). Crexil never deletes a saved voice."""
        async with self._tx():
            if await self.saved_voice(provider, generated_voice_id) == voice_id:
                return
            await self._append_event("voice.saved", **await self._slot_ids(slot), payload={
                "provider": provider, "generated_voice_id": generated_voice_id, "voice_id": voice_id,
                "found_in_account": found, "attempt": slot["attempt"]})

    async def provider_attempts(self, provider: str, *, slot_id: str, attempt: int) -> dict:
        """What the record says about one provider's attempts, for an adapter
        that binds a lost answer to one attempt (`slot_id`, `attempt`): `bound`,
        every request and history item id another attempt's receipt records;
        `unsettled`, every other attempt sent to this provider that no receipt
        records and whose charge no event settles (in flight, stopped late, or
        lost), with its frozen request and send time; and `sent_at`, when this
        attempt was sent. Times are Unix seconds."""
        bound: set[str] = set()
        receipted: set[tuple] = set()
        for row in await self._all(
                "SELECT g.slot_id, g.take, o.descriptor_json FROM execution_outputs o "
                "JOIN execution_groups g ON g.id=o.group_id WHERE o.role='provider receipt'"):
            external = (json.loads(row["descriptor_json"]).get("receipt") or {}).get("external") or {}
            if external.get("provider") != provider:
                continue
            receipted.add((row["slot_id"], row["take"]))
            if (row["slot_id"], row["take"]) != (slot_id, attempt):
                bound.update(v for v in (external.get("request_id"), external.get("history_item_id")) if v)
        sent = await self._all(
            "SELECT e.slot_id, e.ts, json_extract(e.payload_json,'$.attempt') AS attempt, s.external_json "
            "FROM events e JOIN slots s ON s.id=e.slot_id WHERE e.type='slot.provider_sending' "
            "AND json_extract(s.external_json,'$.provider')=? ORDER BY e.seq", (provider,))
        settled: set[tuple] = set()
        if sent:
            for row in await self._all(
                    "SELECT slot_id, payload_json FROM events WHERE slot_id IN (SELECT value FROM json_each(?)) "
                    "AND (charged_usd IS NOT NULL OR type='slot.charge_verified')",
                    (json.dumps(sorted({r["slot_id"] for r in sent})),)):
                payload = json.loads(row["payload_json"])
                settled.add((row["slot_id"], payload.get("attempt", payload.get("take"))))
        unsettled, sent_at = [], None
        for row in sent:
            key, when = (row["slot_id"], row["attempt"]), datetime.fromisoformat(row["ts"]).timestamp()
            if key == (slot_id, attempt):
                sent_at = when
            elif key not in settled and key not in receipted:
                unsettled.append({"slot_id": row["slot_id"], "attempt": row["attempt"], "sent_at": when,
                                  "external": json.loads(row["external_json"])})
        return {"bound": bound, "unsettled": unsettled, "sent_at": sent_at}

    async def mark_blind_run(self, turn_id: str, workspace_id: str, actor: dict) -> None:
        """Record, inside the caller's transaction, that a run is blind."""
        ts = now_iso()
        async with self._locked():
            await self.conn.execute(
                "INSERT INTO blind_runs(turn_id,workspace_id,actor_json,created_at) VALUES(?,?,?,?)",
                (turn_id, workspace_id, json.dumps(actor), ts))
        await self.append_event("run.blinded", workspace_id=workspace_id, turn_id=turn_id,
                                payload={"actor": actor})

    async def blind_runs(self, workspace_id: str | None = None) -> list[dict]:
        """Blind runs with their reveal, read from the authored `run.revealed`
        event; a run is revealed by its first such event."""
        where, args = ("WHERE b.workspace_id=?", (workspace_id,)) if workspace_id else ("", ())
        rows = await self._all(
            "SELECT b.*, e.ts AS revealed_at, e.payload_json AS reveal_json FROM blind_runs b "
            "LEFT JOIN events e ON e.seq=(SELECT MIN(seq) FROM events WHERE type='run.revealed' "
            f"AND turn_id=b.turn_id) {where} ORDER BY b.created_at", args)
        runs = []
        for row in rows:
            reveal = json.loads(row["reveal_json"]) if row["reveal_json"] else None
            runs.append({"turn_id": row["turn_id"], "workspace_id": row["workspace_id"],
                         "actor": json.loads(row["actor_json"]), "created_at": row["created_at"],
                         "revealed": reveal is not None, "revealed_at": row["revealed_at"],
                         "revealed_by": (reveal or {}).get("actor")})
        return runs

    async def _attach_parents(self, nodes: list[dict]) -> None:
        if not nodes:
            return
        by_id = {n["id"]: n for n in nodes}
        for n in nodes:
            n["parents"] = []
        qs = ",".join("?" * len(by_id))
        rows = await self._all(
            f"SELECT node_id, parent_id FROM node_parents"
            f" WHERE node_id IN ({qs}) ORDER BY node_id, ordinal",
            list(by_id))
        for r in rows:
            by_id[r["node_id"]]["parents"].append(r["parent_id"])

    async def set_node_position(self, nid: str, x: float, y: float) -> None:
        """The mutable-view exception to node immutability (a drag moves
        where the artist is working, never what happened)."""
        async with self._locked():
            await self.conn.execute(
                "UPDATE nodes SET x=?, y=? WHERE id=?", (x, y, nid))

    async def _node_slot_sync(self, node: dict, from_status: str,
                              to_status: str) -> None:
        """Move the node's slot only when this node is the slot's current
        take — a rerolled/backfilled slot no longer mirrors an old take."""
        if not node["slot_id"]:
            return
        slot = await self.get_slot(node["slot_id"])
        if slot and slot["status"] == from_status \
                and slot["attempt"] == node["take"]:
            if to_status == 'set_aside':
                visible = await self._one('SELECT COUNT(*) AS n FROM nodes WHERE slot_id=? AND take=? AND set_aside=0 AND purged_at IS NULL',
                                          (slot['id'], slot['attempt']))
                if visible['n']:
                    return
            await self.conn.execute(
                "UPDATE slots SET status=? WHERE id=?",
                (to_status, slot["id"]))

    async def set_aside_node(self, nid: str) -> dict:
        """Set aside is a place, not a delete: flag the node, keep the
        slot footprint (status set_aside), offer restore."""
        async with self._tx():
            node = await self._require_node(nid)
            if node["purged_at"]:
                raise ValueError(f"node {nid} is purged")
            await self.conn.execute(
                "UPDATE nodes SET set_aside=1 WHERE id=?", (nid,))
            await self._node_slot_sync(node, "landed", "set_aside")
            await self._append_event(
                "node.set_aside", workspace_id=node["workspace_id"],
                turn_id=node["turn_id"], slot_id=node["slot_id"], node_id=nid)
        return await self.get_node(nid)

    async def restore_node(self, nid: str) -> dict:
        async with self._tx():
            node = await self._require_node(nid)
            await self.conn.execute(
                "UPDATE nodes SET set_aside=0 WHERE id=?", (nid,))
            await self._node_slot_sync(node, "set_aside", "landed")
            await self._append_event(
                "node.restored", workspace_id=node["workspace_id"],
                turn_id=node["turn_id"], slot_id=node["slot_id"], node_id=nid)
        return await self.get_node(nid)

    async def tray_nodes(self, wid: str) -> list[dict]:
        """The set-aside tray: live set-aside nodes,
        newest first, carrying the same two label keys as the canvas feed
        (`turn_idx`, `slot_pos`) — a tray entry is named like its card. A
        blind run's takes keep its grid order."""
        rows = await self._all(
            "SELECT n.*, t.idx AS turn_idx, s.pos AS slot_pos FROM nodes n"
            " LEFT JOIN turns t ON t.id = n.turn_id"
            " LEFT JOIN slots s ON s.id = n.slot_id" + self._BLIND_JOIN +
            " WHERE n.workspace_id=? AND n.set_aside=1"
            " AND n.purged_at IS NULL ORDER BY COALESCE(b.created_at, n.created_at) DESC, "
            + self._IN_RUN_ORDER, (wid,))
        nodes = [self._node_row(r) for r in rows]
        await self._attach_parents(nodes)
        return nodes

    async def children_counts(self, ids: list[str]) -> dict[str, int]:
        """Live (unpurged) children per parent id — what a purge would
        orphan of its image. Ids with no children are absent."""
        if not ids:
            return {}
        qs = ",".join("?" * len(ids))
        rows = await self._all(
            f"SELECT p.parent_id AS pid, COUNT(*) AS n FROM node_parents p"
            f" JOIN nodes c ON c.id = p.node_id"
            f" WHERE p.parent_id IN ({qs}) AND c.purged_at IS NULL"
            f" GROUP BY p.parent_id", ids)
        return {r["pid"]: r["n"] for r in rows}

    async def purge_node(self, nid: str) -> dict:
        """The only delete verb (law 7) — and even it only tombstones the row.
        Returns the node with `purge_paths`: the files the CALLER must unlink
        (output + region masks); the store never touches the filesystem.
        Idempotent: a second purge is a no-op with no second event."""
        async with self._tx():
            node = await self._require_node(nid)
            regions = await self.regions_for_node(nid)
            fresh = not node["purged_at"]
            if fresh:
                from .crexil_sequences import guard_purge
                await guard_purge(self, nid)
                await self.conn.execute(
                    "UPDATE nodes SET purged_at=? WHERE id=?"
                    " AND purged_at IS NULL", (now_iso(), nid))
                await self._append_event(
                    "node.purged", workspace_id=node["workspace_id"],
                    turn_id=node["turn_id"], slot_id=node["slot_id"],
                    node_id=nid)
        node = await self.get_node(nid)
        node["purge_paths"] = [node["output_path"]] + \
            [r["mask_path"] for r in regions]
        return node

    async def record_promotion(self, nid: str, manifest_path: str,
                               promoted_path: str) -> dict:
        """Promotion is an append-only fact in the event log; it has no
        table of its own."""
        async with self._locked():
            node = await self._require_node(nid)
            return await self._append_event(
                "node.promoted", workspace_id=node["workspace_id"],
                turn_id=node["turn_id"], node_id=nid,
                payload={"manifest_path": manifest_path,
                         "promoted_path": promoted_path})

    async def _require_node(self, nid: str) -> dict:
        node = await self.get_node(nid)
        if node is None:
            raise ValueError(f"unknown node {nid}")
        return node

    # --- search (the tray + the summon palette's node side) ---

    async def search_nodes(self, wid: str, query: str, *,
                           aside_only: bool = False,
                           engine: str | None = None,
                           limit: int = 50) -> list[dict]:
        """FTS over prompts. User text is quoted token-by-token so FTS
        metacharacters ('\"', AND, *) never raise. Purged nodes never match."""
        tokens = [t.replace('"', '""') for t in query.split()]
        if not tokens:
            return []
        match = " ".join(f'"{t}"' for t in tokens)
        q = ("SELECT n.* FROM node_fts JOIN nodes n ON n.id = node_fts.node_id"
             " WHERE node_fts MATCH ? AND n.workspace_id=?"
             " AND n.purged_at IS NULL")
        vals: list = [match, wid]
        if aside_only:
            q += " AND n.set_aside=1"
        if engine:
            q += " AND n.engine=?"
            vals.append(engine)
        q += " ORDER BY node_fts.rank LIMIT ?"
        vals.append(limit)
        rows = await self._all(q, vals)
        nodes = [self._node_row(r) for r in rows]
        await self._attach_parents(nodes)
        return nodes

    # --- regions (keys are the regions.py duck-type contract) ---

    async def create_region(self, node_id: str, name: str, action: str,
                            prompt: str | None, mask_path: str,
                            mask_sha256: str, *,
                            id_: str | None = None) -> dict:
        """`id_` lets the region route validate a whole run against
        pre-generated ids and persist rows only after every gate passed:
        a refused request must leave no rows behind."""
        rid, ts = id_ or new_id(), now_iso()
        async with self._locked():
            row = await self._one(
                "SELECT COALESCE(MAX(z_order), -1) + 1 FROM regions"
                " WHERE node_id=?", (node_id,))
            z = row[0]
            await self.conn.execute(
                "INSERT INTO regions(id, node_id, name, action, prompt,"
                " mask_path, mask_sha256, z_order, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (rid, node_id, name, action, prompt, mask_path,
                 mask_sha256, z, ts))
        return await self.get_region(rid)

    async def get_region(self, rid: str) -> dict | None:
        row = await self._one("SELECT * FROM regions WHERE id=?", (rid,))
        return dict(row) if row else None

    async def regions_for_node(self, nid: str) -> list[dict]:
        rows = await self._all(
            "SELECT * FROM regions WHERE node_id=? ORDER BY z_order", (nid,))
        return [dict(r) for r in rows]

    async def regions_by_ids(self, rids: list[str]) -> list[dict]:
        if not rids:
            return []
        qs = ",".join("?" * len(rids))
        rows = await self._all(
            f"SELECT * FROM regions WHERE id IN ({qs}) ORDER BY z_order", rids)
        return [dict(r) for r in rows]

    async def mark_regions_consumed(self, rids: list[str], turn_id: str) -> None:
        if not rids:
            return
        qs = ",".join("?" * len(rids))
        async with self._locked():
            await self.conn.execute(
                f"UPDATE regions SET consumed_by_turn_id=? WHERE id IN ({qs})",
                [turn_id, *rids])

    # --- events (every failure recorded; law 9; the resume cursor) ---

    async def _append_event(self, type_: str, *,
                            workspace_id: str | None = None,
                            turn_id: str | None = None,
                            slot_id: str | None = None,
                            node_id: str | None = None,
                            payload: dict | None = None,
                            charged_usd: float | None = None) -> dict:
        cur = await self.conn.execute(
            "INSERT INTO events(ts, type, workspace_id, turn_id, slot_id,"
            " node_id, charged_usd, payload_json) VALUES(?,?,?,?,?,?,?,?)"
            " RETURNING *",
            (now_iso(), type_, workspace_id, turn_id, slot_id, node_id,
             charged_usd, json.dumps(payload or {})))
        row = await cur.fetchone()
        await cur.close()   # finalize the RETURNING statement promptly
        return self._event_row(row)

    async def append_event(self, type_: str, **kw) -> dict:
        """Public append (capture.py routes unhandled failures through it)."""
        async with self._locked():
            return await self._append_event(type_, **kw)

    async def events_since(self, seq: int, *, workspace_id: str | None = None,
                           limit: int = 500) -> list[dict]:
        q, vals = "SELECT * FROM events WHERE seq > ?", [seq]
        if workspace_id:
            q += " AND workspace_id=?"
            vals.append(workspace_id)
        q += " ORDER BY seq LIMIT ?"
        vals.append(limit)
        return [self._event_row(r) for r in await self._all(q, vals)]

    async def slot_events(self, slot_id: str) -> list[dict]:
        """The permanent per-slot record — every dispatch, retry, failure and
        charge, in order (law 9)."""
        rows = await self._all(
            "SELECT * FROM events WHERE slot_id=? ORDER BY seq", (slot_id,))
        return [self._event_row(r) for r in rows]

    async def node_events(self, nid: str, type_: str | None = None
                          ) -> list[dict]:
        """The per-node record. Promote reads `node.promoted` off it: the
        promote plinth names the earlier promotions of a take rather than
        pretending the first was the only one."""
        q, vals = "SELECT * FROM events WHERE node_id=?", [nid]
        if type_ is not None:
            q += " AND type=?"
            vals.append(type_)
        rows = await self._all(q + " ORDER BY seq", vals)
        return [self._event_row(r) for r in rows]

    async def latest_seq(self) -> int:
        """The ledger tail: the poll cursor bootstrap, and the point a
        live feed would resume from."""
        row = await self._one("SELECT COALESCE(MAX(seq), 0) FROM events")
        return row[0]

    async def _unrecorded_charge(self, slot: dict, amount: float | None) -> float | None:
        if amount is None:
            return None
        row = await self._one("SELECT charged_usd FROM events WHERE slot_id=? AND type='slot.provider_charge_reported' "
                              "AND json_extract(payload_json,'$.attempt')=? LIMIT 1", (slot['id'], slot['attempt']))
        return round(amount - row['charged_usd'], 6) if row else amount

    async def report_provider_charge(self, slot: dict, amount: float, job_ref: str | None) -> None:
        """Record a known response charge once, before its files finish downloading."""
        async with self._tx():
            previous = await self._one("SELECT 1 FROM events WHERE slot_id=? AND type='slot.provider_charge_reported' "
                                       "AND json_extract(payload_json,'$.attempt')=?", (slot['id'], slot['attempt']))
            if previous:
                return
            await self.conn.execute('UPDATE slots SET charged_usd=?,job_ref=COALESCE(?,job_ref) WHERE id=? AND attempt=?',
                                    (amount, job_ref, slot['id'], slot['attempt']))
            await self._append_event('slot.provider_charge_reported', **await self._slot_ids(slot), charged_usd=amount,
                                     payload={'attempt': slot['attempt'], 'job_ref': job_ref})

    async def confirm_unsent_stop(self, slot: dict) -> None:
        """A cancelled external preflight can stop only before its paid-send event."""
        async with self._tx():
            current = await self._require_slot(slot['id'])
            if current['status'] != 'late' or current['attempt'] != slot['attempt']:
                return
            sent = await self._one("SELECT 1 FROM events WHERE slot_id=? AND type='slot.provider_sending' "
                                   "AND json_extract(payload_json,'$.attempt')=?", (slot['id'], slot['attempt']))
            if sent:
                raise ValueError('A sent request cannot be confirmed as unsent.')
            await self.conn.execute("UPDATE slots SET status='skipped',finished_at=?,cause='Stopped before provider dispatch' WHERE id=?",
                                    (now_iso(), slot['id']))
            await self._append_event('slot.stop_confirmed', **await self._slot_ids(slot),
                                      payload={'attempt': slot['attempt'], 'reason': 'No paid request was sent'})

    async def record_charge(self, slot_id: str, usd: float,
                            *, note: str | None = None, attempt: int | None = None) -> dict:
        """The ledger entry for charges outside land/fail — B4's charged-but-
        nothing-returned, cancel of an in-flight external take."""
        async with self._tx():
            slot = await self._require_slot(slot_id)
            attempt = slot['attempt'] if attempt is None else attempt
            await self.conn.execute(
                "UPDATE slots SET charged_usd=COALESCE(charged_usd,0)+?"
                " WHERE id=? AND attempt=?", (usd, slot_id, attempt))
            ids = await self._slot_ids(slot)
            return await self._append_event(
                "slot.charged", **ids, charged_usd=usd,
                payload={"note": note, "engine": slot["engine"], 'attempt': attempt})

    # --- cost rollups (totals report CHARGED, summed from the ledger) ---

    async def workspace_charged(self, wid: str) -> float:
        row = await self._one(
            "SELECT COALESCE(SUM(charged_usd), 0) FROM events"
            " WHERE workspace_id=? AND charged_usd IS NOT NULL", (wid,))
        return round(row[0], 6)

    async def charged_between(self, start_iso: str, end_iso: str) -> float:
        """Charged over [start, end) across every workspace — the corner
        totals' one source. Bounds are ISO-8601 strings in the
        spelling now_iso() writes (UTC, `+00:00`), compared as text so the
        partial ts index stays sargable — a date-only bound ('2026-08-01')
        sorts before every timestamp of that day, as intended."""
        row = await self._one(
            "SELECT COALESCE(SUM(charged_usd), 0) FROM events"
            " WHERE charged_usd IS NOT NULL AND ts >= ? AND ts < ?"
            " AND json_type(payload_json,'$.archive_origin') IS NULL",
            (start_iso, end_iso))
        return round(row[0], 6)

    # --- file hash cache (model files for manifests, material facts) ---

    async def cached_hash(self, path: str, size: int, mtime: float) -> str | None:
        row = await self._one(
            "SELECT sha256 FROM model_hashes WHERE path=? AND size=? AND mtime=?",
            (path, size, mtime))
        return row["sha256"] if row else None

    async def store_hash(self, path: str, size: int, mtime: float,
                         sha256: str) -> None:
        async with self._locked():
            await self.conn.execute(
                "INSERT OR REPLACE INTO model_hashes(path, size, mtime, sha256)"
                " VALUES(?,?,?,?)", (path, size, mtime, sha256))

    async def file_sha256(self, path: str | Path) -> str | None:
        """A file's SHA-256 through the size-and-mtime cache. A miss is
        hashed on a worker thread, never on the event loop, and remembered;
        a changed file has a new size or mtime and is hashed again. None
        when the file is gone or unreadable."""
        target = Path(path)
        try:
            st = await asyncio.to_thread(target.stat)
        except OSError:
            return None
        cached = await self.cached_hash(str(target), st.st_size, st.st_mtime)
        if cached is None:
            try:
                cached = await asyncio.to_thread(sha256_file, target)
            except OSError:
                return None
            await self.store_hash(str(target), st.st_size, st.st_mtime, cached)
        return cached

    # --- app settings (the promote root's re-point) ---

    async def get_meta(self, key: str) -> str | None:
        """`meta` is the store's own key/value strip: schema_version lives
        here, and `promote_dir` is its first artist-set value. A broken
        promote target must offer a re-point, and .env can't be
        re-pointed from inside a container."""
        row = await self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str | None) -> None:
        """None clears the key — the setting falls back to its .env default."""
        async with self._locked():
            if value is None:
                await self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                await self.conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)",
                    (key, value))

    # --- row decoders (the dict shapes the API layer builds on) ---

    @staticmethod
    def _ws_row(r: aiosqlite.Row) -> dict:
        d = dict(r)
        d["viewport"] = json.loads(d.pop("viewport_json") or "{}")
        d["dials"] = json.loads(d.pop("dials_json") or "{}")
        # Only the list readers join _WS_COUNTS, and only the list's
        # response model (WorkspaceRow) declares them — a single-row reader
        # answers about a workspace the caller already has open, pays no
        # aggregate for a number that surface doesn't draw, and drops these
        # zeros on the way out rather than serving them as a count.
        d.setdefault("nodes", 0)
        d.setdefault("promoted", 0)
        return d

    @staticmethod
    def _turn_row(r: aiosqlite.Row) -> dict:
        d = dict(r)
        d["dials"] = json.loads(d.pop("dials_json") or "{}")
        return d

    @staticmethod
    def _slot_row(r: aiosqlite.Row) -> dict:
        d = dict(r)
        d["dials"] = json.loads(d.pop("dials_json") or "{}")
        raw = d.pop("submitted_graph_json", None)
        d["submitted_graph"] = json.loads(raw) if raw else None
        raw = d.pop("external_json", None)
        d["external"] = json.loads(raw) if raw else None
        raw = d.pop("facts_json", None)
        d["facts"] = json.loads(raw) if raw else None
        return d

    @staticmethod
    def _node_row(r: aiosqlite.Row) -> dict:
        d = dict(r)
        d["dials"] = json.loads(d.pop("dials_json") or "{}")
        raw = d.pop("submitted_graph_json", None)
        d["submitted_graph"] = json.loads(raw) if raw else None
        raw = d.pop("external_json", None)
        d["external"] = json.loads(raw) if raw else None
        d["set_aside"] = bool(d["set_aside"])
        # Only the two joined readers carry the real value; every
        # other reader gets the key with nothing claimed in it.
        d.setdefault("promoted_at", None)
        return d

    @staticmethod
    def _event_row(r: aiosqlite.Row) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
        return d
