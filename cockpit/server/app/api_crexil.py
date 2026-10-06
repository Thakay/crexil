"""The shared preparation door for the Filmstrip and API clients.

Mutations are attributed, revision checked and idempotent by delivery ID. Import
keeps original bytes, embedded marks and reported relationships; it never claims
to have observed an external edit. Operations describe configured interfaces,
availability and evidence separately. No read implicitly starts generation.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from fastapi.responses import FileResponse, Response
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from . import audio, blind, config, preferences, wiring
from .crexil_schemas import (CollectExecution, ExecutionGroupView, CreateDirection, CreateProject, DirectionState,
    DirectionView, DisplaySelection, ImportMaterial, ImportResult, OperationView,
    ExecutionReview, SubmitDirection, SubmittedDirection,
    ProjectSnapshot, ProjectView, UpdateDirection, UpdateProject,
    ImportOperation, InspectOperation, RegistrationView, UpdateOperation, OperationDefinition,
    MaskView, MaskSourceView, SaveMask, ControlExecution, ExecutionControlView,
    Command, CreateSequence, UpdateSequence, UpdateSequenceStep, StartSequence, DecideSequence, SequenceView, SequenceReview,
    DeliverySelection, CreateDelivery, DeliveryReview, DeliveryView,
    ReuseSelection, CreateReuse, ReuseReview,
    ReserveArchive, RestoreArchive, ArchiveUpload, ArchiveRestoreResult, ProjectBackup,
    RecordAssessment, DecisionView, ProjectRecord, MaterialRecord, SetupOverview, OperationRequirements,
    BlindRunView, PreferenceExport, RevealRun)
from .crexil_store import Conflict, CreativeStore
from .db import new_id

router = APIRouter(prefix='/api/crexil', tags=['Crexil shared actions'])


def store() -> CreativeStore:
    if wiring.current is None:
        raise HTTPException(503, 'The project store is starting. Try again shortly.')
    return CreativeStore(wiring.current.db)


# Defects, not refusals: they reach the app's failure handler, which records
# them and answers 500 in words, instead of passing as "not found" (law 3).
DEFECTS = (KeyError, IndexError, ValidationError, json.JSONDecodeError, UnicodeError)


def refused(exc: Exception) -> HTTPException:
    """The one translation of a refusal into a plain-language response.
    Raises a defect unchanged; maps unfinished features to 501, conflicts to
    409, missing records to 404 and other refusals to 422."""
    if isinstance(exc, DEFECTS):
        raise exc
    if isinstance(exc, (blind.NotBuilt, preferences.NotBuilt)):
        return HTTPException(501, str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    return HTTPException(422, str(exc))


async def command(body, scope, action):
    try:
        return await store().command(body.request_id, scope, body.model_dump(), action)
    except (LookupError, ValueError) as exc:
        raise refused(exc) from exc


async def check_references(wid: str, ids: list[str], *, require_available=False):
    if len(set(ids)) != len(ids):
        raise ValueError('A source is listed more than once. Keep one entry with its contribution.')
    for nid in ids:
        node = await store().db.get_node(nid)
        if not node or node['workspace_id'] != wid:
            raise ValueError('A source belongs to another project or is missing. Import it into this project first.')
        if require_available and (node['purged_at'] or not Path(node['output_path']).is_file()):
            raise ValueError('A selected file is unavailable. Restore its material before choosing it.')


@router.post('/projects/{wid}/deliveries/review', response_model=DeliveryReview)
async def review_delivery(wid: str, body: DeliverySelection):
    from .crexil_delivery import review
    try:
        return await review(store().db, wid, body)
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.post('/projects/{wid}/deliveries', response_model=DeliveryView, status_code=201)
async def prepare_delivery(wid: str, body: CreateDelivery):
    from .crexil_delivery import prepare, state
    receipt = await command(body, 'delivery.prepare:' + wid, lambda: prepare(store().db, wid, body))
    return await state(store().db, receipt['id'])


@router.get('/projects/{wid}/deliveries', response_model=list[DeliveryView])
async def list_deliveries(wid: str):
    from .crexil_delivery import inventory
    try:
        return await inventory(store().db, wid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.get('/deliveries/{did}', response_model=DeliveryView)
async def delivery_state(did: str):
    from .crexil_delivery import state
    try:
        return await state(store().db, did)
    except LookupError as exc:
        raise refused(exc) from exc


@router.post('/deliveries/{did}/transfer', response_model=DeliveryView)
async def transfer_delivery(did: str, body: Command):
    from .crexil_delivery import state, transfer
    async def record():
        saved = await state(store().db, did)
        await store().db.append_event('delivery.transfer_requested', workspace_id=saved['workspace_id'],
            payload={'delivery_id': did, 'actor': body.actor.model_dump()})
        return {'id': did}
    await command(body, 'delivery.transfer:' + did, record)
    try:
        return await transfer(store().db, did)
    except ValueError as exc:
        raise refused(exc) from exc


@router.get('/projects/{wid}/deliveries.csv', response_class=Response)
async def delivery_csv(wid: str):
    from .crexil_delivery import csv_records
    try:
        return Response(await csv_records(store().db, wid), media_type='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="deliveries.csv"'})
    except LookupError as exc:
        raise refused(exc) from exc


@router.get('/projects', response_model=list[ProjectView])
async def projects():
    creative = store()
    return [await creative.project(p['id']) for p in await creative.db.list_workspaces()]


@router.post('/projects', response_model=ProjectView, status_code=201)
async def create_project(body: CreateProject):
    async def apply():
        creative = store()
        project = await creative.db.create_workspace(body.name.strip(), slug='project-' + new_id())
        await creative.db.conn.execute(
            'INSERT INTO project_details(workspace_id,brief) VALUES(?,?)', (project['id'], body.brief))
        await creative.db.append_event('project.created', workspace_id=project['id'],
            payload={'actor': body.actor.model_dump(), 'name': body.name, 'brief': body.brief})
        return await creative.project(project['id'])
    return await command(body, 'projects.create', apply)


@router.get('/projects/{wid}', response_model=ProjectSnapshot)
async def project_snapshot(wid: str):
    try:
        return await store().snapshot(wid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.get('/projects/{wid}/record', response_model=ProjectRecord)
async def project_record(wid: str, q: str = Query(default='', max_length=500), node_id: str | None = None,
                         direction_id: str | None = None, turn_id: str | None = None, before: int | None = Query(default=None, ge=1),
                         limit: int = Query(default=50, ge=1, le=100)):
    from .crexil_record import record
    try:
        return await record(store().db, wid, q=q, node_id=node_id, direction_id=direction_id, turn_id=turn_id, before=before, limit=limit)
    except LookupError as exc:
        raise refused(exc) from exc


@router.post('/projects/{wid}/assessments', response_model=DecisionView, status_code=201)
async def record_assessment(wid: str, body: RecordAssessment):
    from .crexil_record import assess
    return await command(body, 'assessment.record:' + wid, lambda: assess(store().db, wid, body))


@router.get('/material/{nid}/record', response_model=MaterialRecord)
async def material_record(nid: str):
    from .crexil_record import material_record as read
    try:
        found = await read(store().db, nid)
    except LookupError as exc:
        raise refused(exc) from exc
    node = await store().db.get_node(nid)
    return blind.mask('material_record', found) if await blind.is_hidden(store().db, node['turn_id']) else found


@router.get('/material/{nid}/audio', responses={200: {'content': {audio.MEDIA_TYPE: {}},
                                                      'description': 'the audio take, byte for byte'}})
async def material_audio(nid: str):
    """An audio take's own bytes, for listening. A blind run's takes stay playable."""
    node = await store().db.get_node(nid)
    if not node or node['payload'] != audio.PAYLOAD:
        raise HTTPException(404, 'This audio take was not found.')
    if node['purged_at'] or not Path(node['output_path']).is_file():
        raise HTTPException(404, 'This audio is unavailable. Its record remains.')
    return FileResponse(node['output_path'], media_type=audio.MEDIA_TYPE,
                        headers={'Cache-Control': 'public, max-age=31536000, immutable'})


@router.post('/turns/{tid}/reveal', response_model=BlindRunView)
async def reveal_run(tid: str, body: RevealRun):
    """Reveal a blind run's engines to every reader; an authored event (law 23)."""
    return await command(body, 'turns.reveal:' + tid, lambda: blind.reveal(store().db, tid, body))


@router.get('/projects/{wid}/preferences', response_model=PreferenceExport)
async def preference_export(wid: str):
    """The decision record: never a dataset, and it holds no audio."""
    try:
        return await preferences.export(store().db, wid)
    except (LookupError, ValueError) as exc:
        raise refused(exc) from exc


@router.get('/projects/{wid}/preferences.jsonl', response_class=Response,
            responses={200: {'content': {'application/x-ndjson': {}},
                             'description': 'one PreferenceRecord per line'}})
async def preference_lines(wid: str):
    try:
        found = await preferences.export(store().db, wid)
    except (LookupError, ValueError) as exc:
        raise refused(exc) from exc
    lines = ''.join(json.dumps(record, sort_keys=True) + '\n' for record in found['records'])
    return Response(lines, media_type='application/x-ndjson',
                    headers={'Content-Disposition': f'attachment; filename="decisions-{wid}.jsonl"'})


@router.patch('/projects/{wid}', response_model=ProjectView)
async def update_project(wid: str, body: UpdateProject):
    async def apply():
        creative = store()
        project = await creative.project(wid)
        if project['revision'] != body.revision:
            raise Conflict('The project changed in another view. Reload before saving your changes.')
        await creative.db.rename_workspace(wid, body.name.strip())
        await creative.db.conn.execute(
            'INSERT INTO project_details(workspace_id,brief,instructions,revision,limits_json) VALUES(?,?,?,?,?) '
            'ON CONFLICT(workspace_id) DO UPDATE SET brief=excluded.brief,'
            'instructions=excluded.instructions,revision=excluded.revision,limits_json=excluded.limits_json',
            (wid, body.brief, body.instructions, body.revision + 1,
             json.dumps(body.limits.model_dump() if body.limits is not None else project['limits'])))
        await creative.db.append_event('project.changed', workspace_id=wid,
            payload={'actor': body.actor.model_dump(), 'name': body.name,
                     'brief': body.brief, 'instructions': body.instructions,
                     'limits': body.limits.model_dump() if body.limits is not None else project['limits']})
        return await creative.project(wid)
    saved = await command(body, 'projects.update:' + wid, apply)
    await wiring.current.scheduler.resume_budget_holds(wid)
    return saved


@router.get('/execution/{scope}/{target_id}', response_model=ExecutionControlView)
async def execution_control_state(scope: Literal['project', 'run', 'sequence'], target_id: str):
    from .execution_controls import state
    try:
        return await state(store().db, scope, target_id)
    except LookupError as exc:
        raise refused(exc) from exc


@router.post('/execution/{scope}/{target_id}', response_model=ExecutionControlView)
async def control_execution(scope: Literal['project', 'run', 'sequence'], target_id: str, body: ControlExecution):
    from .execution_controls import record
    async def apply():
        return await record(store().db, scope, target_id, body)
    saved = await command(body, f'execution.control:{scope}:{target_id}', apply)
    await wiring.current.scheduler.apply_execution_controls()
    wiring.current.sequences.wake.set()
    return saved


@router.post('/projects/{wid}/sequences', response_model=SequenceView, status_code=201)
async def create_sequence(wid: str, body: CreateSequence):
    from .crexil_sequences import create
    return await command(body, 'sequences.create:' + wid, lambda: create(store().db, wid, body))


@router.get('/sequences/{sid}', response_model=SequenceView)
async def sequence_state(sid: str):
    from .crexil_sequences import state
    try:
        return await state(store().db, sid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.patch('/sequences/{sid}', response_model=SequenceView)
async def update_sequence(sid: str, body: UpdateSequence):
    from .crexil_sequences import update
    saved = await command(body, 'sequences.update:' + sid, lambda: update(store().db, sid, body))
    await wiring.current.scheduler.resume_budget_holds(saved['workspace_id'])
    wiring.current.sequences.wake.set()
    return saved


@router.get('/sequences/{sid}/review', response_model=SequenceReview)
async def review_sequence(sid: str):
    from .crexil_sequences import refresh, review
    try:
        await refresh(wiring.current, sid)
        return await review(wiring.current, sid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.patch('/sequences/{sid}/steps/{key}', response_model=SequenceView)
async def update_sequence_step(sid: str, key: str, body: UpdateSequenceStep):
    from .crexil_sequences import update_step
    saved = await command(body, f'sequences.step:{sid}:{key}', lambda: update_step(store().db, sid, key, body))
    wiring.current.sequences.wake.set()
    return saved


@router.post('/sequences/{sid}/start', response_model=SequenceView)
async def start_sequence(sid: str, body: StartSequence):
    from .crexil_sequences import refresh, start
    try:
        await refresh(wiring.current, sid)
    except LookupError as exc:
        raise refused(exc) from exc
    saved = await command(body, 'sequences.start:' + sid, lambda: start(wiring.current, sid, body))
    wiring.current.sequences.wake.set()
    return saved


@router.post('/sequences/{sid}/steps/{key}/decision', response_model=SequenceView)
async def sequence_decision(sid: str, key: str, body: DecideSequence):
    from .crexil_sequences import decide
    saved = await command(body, f'sequences.decide:{sid}:{key}', lambda: decide(store().db, sid, key, body))
    wiring.current.sequences.wake.set()
    return saved


@router.post('/sequences/{sid}/reuse', response_model=SequenceView, status_code=201)
async def reuse_sequence(sid: str, body: Command):
    from .crexil_sequences import reuse
    return await command(body, 'sequences.reuse:' + sid, lambda: reuse(store().db, sid, body))


@router.post('/projects/{wid}/directions', response_model=DirectionView, status_code=201)
async def create_direction(wid: str, body: CreateDirection):
    async def apply():
        await check_references(wid, [r.node_id for r in body.setup.references])
        return await store().new_direction(wid, body.title, body.setup.model_dump(),
                                           body.actor.model_dump(), body.row_idx)
    return await command(body, 'directions.create:' + wid, apply)


@router.patch('/directions/{did}', response_model=DirectionView)
async def update_direction(did: str, body: UpdateDirection):
    async def apply():
        before = await store().direction(did)
        await check_references(before['workspace_id'], [r.node_id for r in body.setup.references])
        return await store().revise_direction(did, body.revision,
            {'title': body.title, 'setup': body.setup.model_dump()}, body.actor.model_dump())
    return await command(body, 'directions.update:' + did, apply)


@router.post('/directions/{did}/state', response_model=DirectionView)
async def direction_state(did: str, body: DirectionState):
    async def apply():
        creative = store()
        before = await creative.direction(did)
        if before['revision'] != body.revision:
            raise Conflict('This direction changed in another view. Reload it first.')
        if body.action == 'reuse':
            return await creative.new_direction(before['workspace_id'], before['title'] + ' copy',
                                                 before['setup'], body.actor.model_dump())
        if body.action == 'discard':
            if before['state'] != 'draft':
                raise ValueError('Only unfinished preparation can be discarded. Set this direction aside instead.')
            changes = {'state': 'discarded'}
        else:
            changes = {'set_aside': body.action == 'set_aside'}
        return await creative.revise_direction(did, body.revision, changes, body.actor.model_dump())
    return await command(body, 'directions.state:' + did, apply)


@router.put('/directions/{did}/selection', response_model=DirectionView)
async def display_selection(did: str, body: DisplaySelection):
    async def apply():
        before = await store().direction(did)
        if not set(body.node_ids).issubset(before['node_ids']):
            raise ValueError('Choose outputs from this direction. Acting on sources across directions is a separate action.')
        await check_references(before['workspace_id'], body.node_ids, require_available=True)
        return await store().revise_direction(did, body.revision, {'selection': body.node_ids},
                                               body.actor.model_dump(), body.reason,
                                               facts=await blind.selection_facts(store().db, did, body.node_ids))
    return await command(body, 'directions.selection:' + did, apply)


def inspect_import(body: ImportMaterial):
    try:
        data = base64.b64decode(body.data_base64, validate=True)
        if len(data) > 128 * 1024 * 1024:
            raise ValueError('Import a file of at most 128 MiB. Export a smaller copy first.')
        if data.startswith(b'glTF') or Path(body.filename).suffix.lower() == '.glb':
            from .crexil_mesh import inspect_glb
            return data, 'GLB', None, None, inspect_glb(data)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP', 'TIFF'}:
                raise ValueError('Import a PNG, JPEG, WebP or TIFF image. Other formats need an explicit conversion.')
            width, height = image.size
            if width * height > 64_000_000:
                raise ValueError('This image is larger than 64 million pixels. Prepare a smaller copy first.')
            fmt = image.format
            metadata = {key: image.info[key] for key in ('workflow', 'prompt')
                        if isinstance(image.info.get(key), str)}
            image.verify()
        return data, fmt, width, height, metadata
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('The file could not be read as an image. Try the original file again.') from exc


@router.post('/projects/{wid}/material', response_model=ImportResult, status_code=201)
async def import_material(wid: str, body: ImportMaterial):
    try:
        data, fmt, width, height, metadata = await run_in_threadpool(inspect_import, body)
    except ValueError as exc:
        raise refused(exc) from exc

    async def apply():
        creative = store()
        await creative.project(wid)
        await check_references(wid, body.parents)
        sha = hashlib.sha256(data).hexdigest()
        ext, media = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'),
                      'WEBP': ('webp', 'image/webp'), 'TIFF': ('tiff', 'image/tiff'),
                      'GLB': ('glb', 'model/gltf-binary')}[fmt]
        config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(config.STAGING_DIR).free < len(data) + config.DISK_MARGIN_BYTES:
            raise ValueError('The staging drive needs more free space before this file can be imported.')
        # Each material owns its copy. Purging one never removes another's bytes.
        path = config.STAGING_DIR / 'imports' / new_id() / f'{sha}.{ext}'
        path.parent.mkdir(parents=True)
        try:
            with path.open('xb') as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            origin = {'kind': 'reported_external' if body.parents else 'imported',
                      'tool': body.tool, 'explanation': body.explanation,
                      'actor': body.actor.model_dump(), 'embedded_metadata': metadata}
            return await creative.receive_material(wid, path=path, sha256=sha,
                filename=body.filename, media_type=media, payload='3d' if fmt == 'GLB' else 'image',
                width=width, height=height, origin=origin, parents=body.parents, direction_id=body.direction_id)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return await command(body, 'material.import:' + wid, apply)


@router.get('/projects/{wid}/imports/{request_id}', response_model=ImportResult)
async def import_receipt(wid: str, request_id: str):
    row = await store().db._one('SELECT response_json FROM command_receipts WHERE request_id=?', (request_id,))
    saved = json.loads(row['response_json']) if row else {}
    if saved.get('node', {}).get('workspace_id') != wid or 'direction' not in saved:
        raise HTTPException(404, 'No accepted import was found for this request. Retry with the same file and request ID.')
    return saved


@router.post('/projects/{wid}/reuse/review', response_model=ReuseReview)
async def review_reuse(wid: str, body: ReuseSelection):
    from .crexil_reuse import review
    try:
        return await review(store().db, wid, body)
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.post('/projects/{wid}/reuse', response_model=ImportResult, status_code=201)
async def reuse_material(wid: str, body: CreateReuse):
    from .crexil_reuse import create
    staged = []
    made = None
    async def apply():
        nonlocal made
        made = await create(store().db, wid, body, staged)
        return made
    try:
        return await command(body, 'material.reuse:' + wid, apply)
    except BaseException:
        # A cancelled worker joins before rollback. Never delete accepted bytes.
        if not made or not await store().db.get_node(made['node']['id']):
            for directory in staged:
                shutil.rmtree(directory, ignore_errors=True)
        raise


@router.get('/material/{nid}/files/{fid}')
async def retained_material_file(nid: str, fid: str):
    from .material_files import get_file
    try:
        path, receipt = await get_file(store().db, nid, fid)
        return FileResponse(path, filename=receipt['filename'], media_type=receipt['media_type'])
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.get('/operations', response_model=list[OperationView])
async def operations():
    stack = wiring.current
    if stack is None:
        raise HTTPException(503, 'The operation catalogue is starting. Try again shortly.')
    from .crexil_execution import catalogue
    from .operation_registry import catalogue as registered
    return catalogue(stack) + await registered(stack)


@router.get('/setup', response_model=SetupOverview)
async def setup_overview():
    from .crexil_setup import overview
    if wiring.current is None:
        raise HTTPException(503, 'The connections are starting. Try again shortly.')
    return await overview(wiring.current)


@router.get('/operation-requirements', response_model=OperationRequirements)
async def operation_requirements(operation_id: str, revision: str):
    from .crexil_setup import requirements
    if wiring.current is None:
        raise HTTPException(503, 'The operation catalogue is starting. Try again shortly.')
    try:
        return await requirements(wiring.current, operation_id, revision)
    except LookupError as exc:
        raise refused(exc) from exc
    except Conflict as exc:
        raise refused(exc) from exc


@router.get('/directions/{did}/image-review', response_model=ExecutionReview)
async def review_image_preparation(did: str):
    from .image_preparation import review
    try:
        return await review(store().db, did)
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.post('/directions/{did}/prepare-image', response_model=ImportResult, status_code=201)
async def prepare_image(did: str, body: SubmitDirection):
    from .image_preparation import save
    return await command(body, 'material.prepare:' + did, lambda: save(store().db, did, body))


@router.get('/projects/{wid}/mask-source/{nid}', response_model=MaskSourceView)
async def mask_source(wid: str, nid: str):
    from .mask_assets import source
    try:
        info, _ = await source(store().db, wid, nid)
        return info
    except ValueError as exc:
        raise refused(exc) from exc


@router.get('/projects/{wid}/masks', response_model=list[MaskView])
async def saved_masks(wid: str):
    from .mask_assets import list_masks
    try:
        return await list_masks(store().db, wid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.post('/projects/{wid}/masks', response_model=MaskView, status_code=201)
async def save_mask(wid: str, body: SaveMask):
    from .mask_assets import save
    return await command(body, 'masks.save:' + wid, lambda: save(store().db, wid, body))


@router.get('/masks/{mid}/image')
async def mask_image(mid: str):
    from fastapi.responses import Response
    from .mask_assets import get, verified_bytes
    try:
        row = await get(store().db, mid)
        return Response(await verified_bytes(row), media_type='image/png')
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.get('/registrations', response_model=list[RegistrationView])
async def registrations():
    from .operation_registry import OperationRegistry
    return await OperationRegistry(store().db).list()


@router.post('/registrations', response_model=RegistrationView, status_code=201)
async def import_operation(body: ImportOperation):
    from .operation_registry import OperationRegistry
    return await command(body, 'operations.import', lambda: OperationRegistry(store().db).create(body))


@router.get('/registrations/{rid}', response_model=RegistrationView)
async def registration(rid: str):
    from .operation_registry import OperationRegistry
    try:
        return await OperationRegistry(store().db).get(rid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.patch('/registrations/{rid}', response_model=RegistrationView)
async def update_operation(rid: str, body: UpdateOperation):
    from .operation_registry import OperationRegistry
    return await command(body, 'operations.update:' + rid, lambda: OperationRegistry(store().db).update(rid, body))


@router.post('/registrations/{rid}/inspect')
async def inspect_operation(rid: str, body: InspectOperation):
    from .operation_registry import OperationRegistry, discover
    try:
        receipt = await store().delivered(body.request_id, 'operations.inspect:' + rid, body.model_dump())
    except Conflict as exc:
        raise refused(exc) from exc
    if receipt is not None:
        return receipt
    item = await registration(rid)
    if item['revision'] != body.revision:
        raise HTTPException(409, 'This operation changed in another view. Reload before inspecting it.')
    report = await discover(OperationDefinition.model_validate(item['definition']))
    async def apply():
        result = await OperationRegistry(store().db).save_inspection(rid, body.revision, report)
        await store().db.append_event('operation.inspected', payload={'registration_id': rid,
            'revision': body.revision, 'actor': body.actor.model_dump(), 'inspection_id': result['id']})
        return result
    return await command(body, 'operations.inspect:' + rid, apply)


@router.get('/registrations/{rid}/export')
async def export_operation(rid: str):
    from .operation_registry import secret_fields
    item = await registration(rid)
    if secret_fields(item['definition']['graph']):
        raise HTTPException(422, 'Remove embedded credentials from the adapted graph before exporting its reusable description.')
    return {'format': 'crexil-operation-v1', 'name': item['name'], 'definition': item['definition'],
        'source': {'name': item['source_name'], 'sha256': item['source_sha256'], 'kind': item['source_kind']},
        'adaptation_note': item['adaptation_note'],
        'binding_required': 'Bind this description to the receiving installation and inspect its runtime before a recorded test.'}


@router.get('/directions/{did}/review', response_model=ExecutionReview)
async def review_direction(did: str):
    from .crexil_execution import review
    from .operation_registry import refresh_for_direction
    try:
        await refresh_for_direction(wiring.current, did)
        return await review(wiring.current, did)
    except LookupError as exc:
        raise refused(exc) from exc


@router.post('/directions/{did}/submit', response_model=SubmittedDirection, status_code=201)
async def submit_direction(did: str, body: SubmitDirection):
    from .crexil_execution import freeze
    from .operation_registry import refresh_for_direction
    await refresh_for_direction(wiring.current, did)
    staged_directories = []
    try:
        result = await command(body, 'directions.submit:' + did,
            lambda: freeze(wiring.current, did, body, staged_directories=staged_directories))
    except BaseException:
        # Only discard temporary staging after proving acceptance did not commit.
        # A database failure that leaves commit status unknown retains the bytes.
        try:
            receipt = await store().db._one('SELECT 1 FROM command_receipts WHERE request_id=?', (body.request_id,))
            if receipt is None:
                for directory in staged_directories:
                    await run_in_threadpool(shutil.rmtree, directory)
        except Exception:
            pass
        raise
    # The receipt, frozen payloads and land-fields are already committed. A lost
    # response or restart only adopts these same slots; it never creates a run.
    await wiring.current.scheduler.adopt_prepared_turn(result['turn_id'])
    return result


@router.get('/outputs/{oid}/file')
async def output_file(oid: str):
    member = await store().db._one('SELECT o.*,n.purged_at FROM execution_outputs o '
        'LEFT JOIN nodes n ON n.id=o.node_id WHERE o.id=?', (oid,))
    if not member:
        raise HTTPException(404, 'This output was not found.')
    from .execution_outputs import purged_at
    if await purged_at(store().db, member) or not member['sha256'] or not member['output_path'] or not Path(member['output_path']).is_file():
        raise HTTPException(404, 'This file is unavailable. Its execution record remains.')
    turn = await store().db._one('SELECT s.turn_id FROM execution_groups g JOIN slots s ON s.id=g.slot_id WHERE g.id=?',
                                 (member['group_id'],))
    if turn and await blind.is_hidden(store().db, turn['turn_id']) and not blind.may_serve(dict(member)):
        raise HTTPException(404, 'This file is withheld while its run is blind. Reveal the run to read it.')
    return FileResponse(member['output_path'], media_type=member['media_type'],
                        filename=Path(member['filename']).name)


@router.post('/attempts/{sid}/collect', response_model=ExecutionGroupView)
async def collect_execution(sid: str, body: CollectExecution):
    async def record_request():
        db = store().db
        slot = await db.get_slot(sid)
        if not slot:
            raise LookupError('This attempt was not found.')
        if slot['attempt'] != body.take:
            raise ValueError('This attempt changed. Refresh its record.')
        await db.append_event('execution.collection_requested', **await db._slot_ids(slot),
                              payload={'take': body.take, 'actor': body.actor.model_dump()})
        return {'slot_id': sid, 'take': body.take}
    await command(body, 'execution.collect:' + sid, record_request)
    try:
        group = await wiring.current.scheduler.collect_existing(sid, body.take)
        from .execution_outputs import inventory
        slot = await store().db.get_slot(sid)
        ids = await store().db._slot_ids(slot)
        return next(g for g in await inventory(store().db, ids['workspace_id']) if g['id'] == group['id'])
    except ValueError as exc:
        raise refused(exc) from exc
    except Exception as exc:
        from .executors import EngineFailure
        if isinstance(exc, EngineFailure):
            raise HTTPException(503, str(exc)) from exc
        raise


@router.post('/projects/{wid}/backups', response_model=ProjectBackup, status_code=201)
async def create_backup(wid: str, body: Command):
    from .crexil_archives import create
    made = None
    async def apply():
        nonlocal made
        made = await create(store().db, wid, body.actor.model_dump())
        return made
    try:
        return await command(body, 'backups.create:' + wid, apply)
    except BaseException:
        if made:
            record = await store().db._one("SELECT 1 FROM events WHERE type='project.backed_up' "
                "AND json_extract(payload_json,'$.id')=? AND json_type(payload_json,'$.archive_origin') IS NULL", (made['id'],))
            if not record:
                (config.STAGING_DIR / 'backups' / made['id'] / 'project.crexil.zip').unlink(missing_ok=True)
        raise


@router.get('/backups/{bid}/file')
async def backup_file(bid: str):
    row = await store().db._one("SELECT payload_json FROM events WHERE type='project.backed_up' "
        "AND json_extract(payload_json,'$.id')=? AND json_type(payload_json,'$.archive_origin') IS NULL", (bid,))
    if not row:
        raise HTTPException(404, 'This backup could not be found.')
    record = json.loads(row['payload_json'])
    path = Path(record['path'])
    if not path.is_file():
        raise HTTPException(404, 'This backup file is missing. Create another backup of the project.')
    return FileResponse(path, media_type='application/zip', filename='project-' + bid + '.crexil.zip')


@router.post('/archive-uploads', response_model=ArchiveUpload, status_code=201)
async def reserve_archive(body: ReserveArchive):
    from .crexil_restore import reserve_upload
    return await command(body, 'archives.reserve', lambda: reserve_upload(store().db, body))


@router.get('/archive-uploads/{uid}', response_model=ArchiveUpload)
async def archive_upload_state(uid: str):
    from .crexil_restore import upload_state
    try:
        return await upload_state(store().db, uid)
    except LookupError as exc:
        raise refused(exc) from exc


@router.put('/archive-uploads/{uid}/file', response_model=ArchiveUpload)
async def upload_archive(uid: str, request: Request):
    from .crexil_restore import accept_upload, upload_lock
    try:
        async with upload_lock(uid):
            return await accept_upload(store().db, uid, request.stream())
    except LookupError as exc:
        raise refused(exc) from exc
    except ValueError as exc:
        raise refused(exc) from exc


@router.post('/archive-uploads/{uid}/restore', response_model=ArchiveRestoreResult, status_code=201)
async def restore_archive(uid: str, body: RestoreArchive):
    from .crexil_restore import restore, upload_state, upload_path, upload_lock
    made = None
    async def apply():
        nonlocal made
        saved = await upload_state(store().db, uid)
        if saved['state'] != 'ready' or saved['sha256'] != body.sha256:
            raise ValueError('This backup is not the reviewed upload. Upload and review it before restoring.')
        made = await restore(store().db, upload_path(uid), name=body.name, actor=body.actor.model_dump(), expected_sha256=body.sha256)
        return made
    async with upload_lock(uid):
        try:
            return await command(body, 'archives.restore:' + uid, apply)
        except BaseException:
            # Commit or receipt failure leaves no orphaned copy after rollback.
            if made and not await store().db.get_workspace(made['project_id']):
                shutil.rmtree(config.STAGING_DIR / 'restored' / made['project_id'], ignore_errors=True)
            raise
