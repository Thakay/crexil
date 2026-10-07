"""A row's sections keep the order they were made in, whatever the clock says: through a
clock that steps back, through a backup and its restore, and beside sections migrated from
legacy work. Every section stays listed (law 7); only its place is at stake."""

import pytest

from app import db as store_module
from app.crexil_store import migrate_projects
from fakes import SteppingClock
from test_api_v4 import booted, store
from test_crexil import command, project
from test_crexil_restore import backup_upload, restored

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def offline_catalogue(monkeypatch):
    from app.providers import ProviderCatalog

    async def disabled(_self):
        return None
    monkeypatch.setattr(ProviderCatalog, '_refresh', disabled)


@pytest.fixture
def clock(monkeypatch):
    stepping = SteppingClock()
    monkeypatch.setattr(store_module, 'now_iso', stepping)
    return stepping


async def section(client, wid, title, row=0):
    made = await client.post(f'/api/crexil/projects/{wid}/directions',
                             json=command(title=title, setup={'prompt': title}, row_idx=row))
    assert made.status_code == 201, made.text
    return made.json()


async def titles(client, wid):
    return [d['title'] for d in (await client.get(f'/api/crexil/projects/{wid}')).json()['directions']]


async def test_a_clock_that_steps_back_never_reorders_a_row(tmp_path, monkeypatch, clock):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        made = [await section(client, p['id'], title) for title in ('Concept', 'Voice')]
        clock.step_back()
        made.append(await section(client, p['id'], 'Second voice'))
        made.append(await section(client, p['id'], 'Mesh', row=1))
        assert made[2]['created_at'] < made[0]['created_at']        # the clock did step back
        assert await titles(client, p['id']) == ['Concept', 'Voice', 'Second voice', 'Mesh']
        record = (await client.get(f"/api/crexil/projects/{p['id']}/record")).json()
        assert record['direction_ids'] == [d['id'] for d in made]


async def test_a_backup_and_its_restore_keep_each_rows_order(tmp_path, monkeypatch, clock):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        for title in ('Concept', 'Voice'):
            await section(client, p['id'], title)
        clock.step_back()
        await section(client, p['id'], 'Second voice')
        upload, _ = await backup_upload(client, p['id'])
        copy = await restored(client, upload, name='Restored')
        assert [d['title'] for d in copy['directions']] == ['Concept', 'Voice', 'Second voice']
        # A section made in the restored project follows its restored ones, and a restore
        # of the restored project keeps the order again.
        clock.step_back(5)
        await section(client, copy['project']['id'], 'Third voice')
        again, _ = await backup_upload(client, copy['project']['id'])
        twice = await restored(client, again, name='Restored twice')
        assert [d['title'] for d in twice['directions']] == ['Concept', 'Voice', 'Second voice', 'Third voice']


async def test_migrated_legacy_sections_lead_their_row_and_new_ones_follow_as_made(tmp_path, monkeypatch, clock):
    async with booted(tmp_path, monkeypatch) as (client, _):
        p = await project(client)
        db = store()
        # A run made before the project held sections, as the store's migration finds it.
        await db.create_turn_with_slots(p['id'], 'an old run', {}, [{'engine': 'testpreset'}])
        async with db._tx():
            await migrate_projects(db)
        clock.step_back(5)
        for title in ('Concept', 'Voice'):
            await section(client, p['id'], title)
        assert await titles(client, p['id']) == ['Direction 1', 'Concept', 'Voice']
