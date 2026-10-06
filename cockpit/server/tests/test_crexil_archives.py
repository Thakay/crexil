"""A portable project preserves records and bytes without becoming a live store."""

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app import config, crexil_archives
from app.db import Database, SchemaVersionError
from test_api_v4 import booted, store
from test_crexil import image_bytes, imported, project

pytestmark = pytest.mark.anyio


async def test_backup_scopes_records_preserves_bytes_and_refuses_live_startup(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        original = (await imported(client, p['id']))['node']
        child = (await imported(client, p['id'], parents=[original['id']], tool='Krita'))['node']
        other = await project(client)
        await imported(client, other['id'])
        await store().set_meta('fixture_secret', 'must not be in a project archive')
        turn = await store().create_turn_with_slots(p['id'], 'Unfinished work', {}, [{'engine': 'local'}])
        slot = await store().dispatch_slot(turn['slots'][0]['id'], job_ref='original-runtime-job')
        async with store()._tx():
            backup = await crexil_archives.create(store(), p['id'], {'kind': 'human', 'name': 'Artist'})
        assert (await store().get_slot(slot['id']))['status'] == 'running'
        path = config.STAGING_DIR / 'backups' / backup['id'] / 'project.crexil.zip'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == backup['sha256']
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['records']['nodes'] == 2
            assert manifest['records']['workspaces'] == 1
            assert all(file['available'] for file in manifest['files'])
            for file in manifest['files']:
                assert archive.read(file['file']) == image_bytes()
            database = tmp_path / 'archived.sqlite'
            database.write_bytes(archive.read('project.sqlite'))
        before = database.read_bytes()
        with sqlite3.connect(database) as conn:
            assert conn.execute('SELECT id FROM workspaces').fetchall() == [(p['id'],)]
            assert conn.execute('SELECT node_id,parent_id,ordinal FROM node_parents').fetchall() == [(child['id'], original['id'], 0)]
            assert conn.execute('SELECT status,job_ref FROM slots').fetchall() == [('running', 'original-runtime-job')]
            assert conn.execute("SELECT value FROM meta WHERE key='fixture_secret'").fetchone() is None
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        db = Database(database)
        try:
            with pytest.raises(SchemaVersionError, match='project backup'):
                await db.open()
        finally:
            await db.close()
        assert database.read_bytes() == before


async def test_backup_records_missing_corrupt_and_purged_material_without_forging_bytes(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        nodes = [(await imported(client, p['id']))['node'] for _ in range(4)]
        Path(nodes[0]['output_path']).unlink()
        Path(nodes[1]['output_path']).write_bytes(b'changed after collection')
        await store().purge_node(nodes[2]['id'])
        async with store()._tx():
            backup = await crexil_archives.create(store(), p['id'], {'kind': 'agent', 'name': 'Archive check'})
        assert backup['records']['nodes'] == 4
        by_owner = {file['owners'][0]: file for file in backup['files']}
        assert [by_owner[node['id']]['available'] for node in nodes] == [False, False, False, True]
        assert 'content hash' in by_owner[nodes[1]['id']]['error']
        assert 'Purged material' in by_owner[nodes[2]['id']]['error']
        path = config.STAGING_DIR / 'backups' / backup['id'] / 'project.crexil.zip'
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == 3
