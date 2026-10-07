"""A character package: one delivery's takes in the folders a game engine imports,
each beside its manifest v4, with original bytes and hashes, a PNG only where an
engine cannot read the original, the record summary and the decision record, and
the recovery every delivery has (laws 7, 20, 23). What no game engine imports,
a provider's receipt and a mesh's voxel field, stays in the store with its record
in the manifest, and no manifest carries a base64 copy of a take or a source.

No engine is installed here, so importability is proven on the formats: real
parsers read every file's header (PNG, WebP, TIFF, GLB, PCM WAV), the manifests'
JSON and every listed hash against the bytes on disk."""

import asyncio
import base64
import csv
import hashlib
import io
import json
import random
import re
import struct
import threading
import wave
from pathlib import Path, PurePosixPath

import httpx
import pytest
from PIL import Image

import fakes
from app import config, crexil_delivery, decisions, manifests, wiring
from app.elevenlabs import ElevenLabsExecutor
from app.executors import OpenRouterExecutor
from app.mesh import FIELDS_SUFFIX, fields_sidecar
from fakes import FakeExecutor, Step
from test_api_v4 import booted as image_bench
from test_api_v4 import store
from test_audio_budget import priced
from test_audio_contract import PREVIEW, booted_audio, designed, draft, submit
from test_blind_runs import HOSTED, LOCAL, cast, casting, identity_terms, leaks, revision_of
from test_crexil import command, imported, project
from test_crexil_execution import attest
from test_crexil_execution import reviewed as review_of
from test_crexil_hosted import ENDPOINT, discover, hosted, limits
from test_crexil_mesh import draft_mesh
from test_crexil_restore import backup_upload, restored
from test_elevenlabs import BASE as ELEVENLABS
from test_elevenlabs import FakeElevenLabs, free_row, sounded

pytestmark = pytest.mark.anyio

LINE = 'Bring me the ore, and mind the coals.'
SECONDS, RATE = 3.0, 24000
AGENT = {'kind': 'agent', 'name': 'Studio assistant'}
ORDER = ('art', 'sketch', 'model', 'voice_preview', 'voice_line', 'sound_effect')
FOLDERS = {'art': 'Art', 'sketch': 'Art', 'model': 'Models', 'voice_preview': 'Audio/Voice/Previews',
           'voice_line': 'Audio/Voice', 'sound_effect': 'Audio/SFX'}
VOXELS = b'PK\x03\x04 the voxel field TRELLIS.2 keeps for re-extraction' * 64
# A base64 run this long in a manifest is a copy of media, never a hash or a word.
COPY = re.compile(r'[A-Za-z0-9+/]{120,}')


@pytest.fixture(autouse=True)
def handoff(tmp_path, monkeypatch):
    target = tmp_path / 'handoff'
    target.mkdir()
    monkeypatch.setattr(config, 'PROMOTE_DIR', target)
    monkeypatch.setattr(config, 'PROMOTE_ROOTS', [target])
    return target


def picture(fmt='PNG', size=(12, 8), color=(150, 90, 40, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, format=fmt, **({'lossless': True} if fmt == 'WEBP' else {}))
    return buffer.getvalue()


def noise_png(side: int) -> bytes:
    """A PNG that does not compress, so its size is the size of real pixels."""
    buffer = io.BytesIO()
    Image.frombytes('RGB', (side, side), random.Random(7).randbytes(side * side * 3)).save(buffer, format='PNG')
    return buffer.getvalue()


def triangle_glb() -> bytes:
    """A glTF 2.0 binary holding one triangle."""
    positions = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
                'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
                'buffers': [{'byteLength': len(positions)}],
                'bufferViews': [{'buffer': 0, 'byteLength': len(positions), 'target': 34962}],
                'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                               'min': [0, 0, 0], 'max': [1, 1, 0]}]}
    text = json.dumps(document).encode()
    text += b' ' * (-len(text) % 4)
    body = struct.pack('<I4s', len(text), b'JSON') + text + struct.pack('<I4s', len(positions), b'BIN\0') + positions
    return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body


# --- the format checks an engine's importer makes first ---

def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    assert struct.unpack('>I4s', data[8:16]) == (13, b'IHDR')
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    return struct.unpack('>II', data[16:24])


def webp_size(data: bytes) -> tuple[int, int]:
    assert data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    assert struct.unpack('<I', data[4:8])[0] == len(data) - 8
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == 'WEBP'
        return image.size


def glb_document(data: bytes) -> dict:
    assert struct.unpack('<4sII', data[:12]) == (b'glTF', 2, len(data))
    size, kind = struct.unpack('<I4s', data[12:20])
    assert kind == b'JSON' and size % 4 == 0
    document = json.loads(data[20:20 + size])
    binary, kind = struct.unpack('<I4s', data[20 + size:28 + size])
    assert kind == b'BIN\0' and binary >= document['buffers'][0]['byteLength']
    return document


def wav_facts(data: bytes) -> tuple[int, int, int, int]:
    assert data[:4] == b'RIFF' and data[8:12] == b'WAVE'
    with wave.open(io.BytesIO(data)) as sound:       # the standard reader takes PCM only
        return sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getnframes()


def pixels(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert('RGBA').tobytes()


# --- making the takes ---

def booted(tmp_path, monkeypatch, **kw):
    """The audio bench with a mesh runner that keeps a voxel field beside each mesh,
    as TRELLIS.2 does. Takes last three seconds, as long as the local voice engine
    needs a voice to be."""
    tone = fakes.tone_wav
    monkeypatch.setattr(fakes, 'tone_wav', lambda **shape: tone(**{'seconds': SECONDS, **shape}))
    mesh = FakeExecutor('mesh', 'local', default=Step(data=triangle_glb(), filename='mesh.glb',
                                                      sidecars={FIELDS_SUFFIX: VOXELS}))
    return booted_audio(tmp_path, monkeypatch, executors={'mesh': mesh}, **kw)


async def material(client, wid: str, filename: str, data: bytes) -> dict:
    response = await client.post(f'/api/crexil/projects/{wid}/material', json=command(
        filename=filename, data_base64=base64.b64encode(data).decode()))
    assert response.status_code == 201, response.text
    return response.json()['node']


async def mesh_of(client, wid: str, source: dict) -> dict:
    """A 3D run of one image, its recorded description left empty."""
    direction = await draft_mesh(client, wid, [source])
    setup = {**direction['setup'], 'prompt': ''}
    edited = await client.patch(f"/api/crexil/directions/{direction['id']}", json=command(
        revision=direction['revision'], title=direction['title'], setup=setup))
    assert edited.status_code == 200, edited.text
    _, (mesh,) = await submit(client, edited.json())
    return mesh


async def character(client, fakes):
    """A dwarf blacksmith in every medium: a generated portrait, a sketch imported as
    WebP, a mesh of the portrait, three designed voices, a line spoken in the first
    and a paid sound effect. Returns the project id, its takes by role and the
    three designed voices."""
    attest()
    fakes['comfy'].default = Step(data=picture())
    p = await project(client)
    wid = p['id']
    _, (portrait,) = await submit(client, await draft(client, wid, 'testpreset', 'A dwarf blacksmith, soot on his apron'))
    sketch = await material(client, wid, 'Rune sketch.webp', picture('WEBP'))
    mesh = await mesh_of(client, wid, portrait)
    _, previews = await designed(client, wid)
    preview = next(take for take in previews if take['output_index'] == 0)
    _, (line,) = await submit(client, await draft(client, wid, 'audio:speak:qwen3-tts', LINE, refs=[preview['id']]))
    await limits(client, p, spending_limit_usd=1, spending_policy='published_tariff')
    _, (sound,) = await submit(client, await priced(client, wid, 'audio:sfx:elevenlabs-sfx', 'Heavy boots on wet stone',
                                                    controls={'duration_seconds': 1.5}))
    takes = {'art': portrait, 'sketch': sketch, 'model': mesh, 'voice_preview': preview, 'voice_line': line,
             'sound_effect': sound}
    return wid, takes, previews


async def run_of(client, wid: str, nid: str) -> dict:
    snapshot = (await client.get(f'/api/crexil/projects/{wid}')).json()
    return next(d for d in snapshot['directions'] if nid in d['node_ids'])


async def pick(client, wid: str, nid: str, reason: str) -> None:
    run = await run_of(client, wid, nid)
    chosen = await client.put(f"/api/crexil/directions/{run['id']}/selection", json=command(
        revision=await revision_of(client, wid, run['id']), node_ids=[nid], reason=reason))
    assert chosen.status_code == 200, chosen.text


async def reviewed(client, wid: str, ids: list[str]) -> tuple[dict, dict]:
    selection = {'node_ids': ids, 'destination': str(config.PROMOTE_DIR)}
    review = await client.post(f'/api/crexil/projects/{wid}/deliveries/review', json=selection)
    assert review.status_code == 200, review.text
    return selection, review.json()


async def prepared(client, wid: str, ids: list[str]) -> tuple[dict, dict]:
    selection, review = await reviewed(client, wid, ids)
    assert review['ready'], review['errors']
    response = await client.post(f'/api/crexil/projects/{wid}/deliveries',
                                 json=command(**selection, review_token=review['token']))
    assert response.status_code == 201, response.text
    return review, response.json()


async def sent(client, did: str) -> dict:
    response = await client.post(f'/api/crexil/deliveries/{did}/transfer', json=command())
    assert response.status_code == 200, response.text
    return response.json()


async def package_root(did: str) -> Path:
    intent = await crexil_delivery._intent(store(), did)
    return Path(intent['destination']) / intent['package']['folder']


def tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob('*') if path.is_file())


# --- the package ---

async def test_a_character_package_lands_every_medium_where_a_game_engine_imports_it(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        wid, takes, previews = await character(client, fakes)
        await pick(client, wid, takes['art']['id'], 'The soot reads at thumbnail size')
        await pick(client, wid, takes['voice_preview']['id'], 'Warmest growl, clearest consonants')
        review, delivery = await prepared(client, wid, [takes[role]['id'] for role in ORDER])
        assert [[f['path'] for f in item['files']] for item in review['items']] == \
            [[f['path'] for f in item['files']] for item in delivery['items']]   # review shows the final names
        result = await sent(client, delivery['id'])
        assert [item['state'] for item in result['items']] == ['complete'] * len(ORDER)
        root = await package_root(delivery['id'])
        on_disk = tree(root)

        # Every name is one each engine's importer takes as it is: no dot in a folder, one before the extension.
        for path in on_disk:
            *folders, name = path.split('/')
            assert all(re.fullmatch(r'[A-Za-z0-9_-]+', folder) for folder in folders), path
            assert re.fullmatch(r'[A-Za-z0-9_-]+\.[a-z0-9]+', name), path

        # package.json lists every other file with its role, path and hash, and the hashes hold.
        listing = json.loads((root / 'package.json').read_bytes())
        assert (listing['kind'], listing['package_version'], listing['delivery_id']) == ('character_package', 2, delivery['id'])
        assert listing['not_delivered'] == [] and listing['actor'] == {'kind': 'human', 'name': 'Artist'}
        listed = {entry['path']: entry for entry in listing['files']}
        assert sorted([*listed, 'package.json']) == on_disk
        for path, entry in listed.items():
            data = (root / path).read_bytes()
            assert (hashlib.sha256(data).hexdigest(), len(data)) == (entry['sha256'], entry['size_bytes']), path

        # Each take sits in its medium's folder with its original bytes and its manifest v4 beside it.
        primary = {entry['node_id']: entry for entry in listing['files'] if entry.get('asset_id')}
        written = {}
        for role in ORDER:
            node, entry = takes[role], primary[takes[role]['id']]
            assert str(PurePosixPath(entry['path']).parent) == FOLDERS[role]
            assert (root / entry['path']).read_bytes() == Path(node['output_path']).read_bytes()
            assert entry['manifest'] == entry['path'].rsplit('.', 1)[0] + '.json'
            manifest = json.loads((root / entry['manifest']).read_bytes())
            assert (manifest['manifest_version'], manifest['node_id'], manifest['asset_id']) == (4, node['id'], entry['asset_id'])
            assert manifest['material']['sha256'] == entry['sha256'] and manifest['created_at']
            written[role] = manifest
        assert [primary[takes[role]['id']]['role'] for role in ORDER] == \
            ['art', 'art', 'model', 'voice_preview', 'voice_line', 'sound_effect']
        assert re.fullmatch(r'Audio/Voice/\d+A_bring-me-the-ore-and-mind-the-coals\.wav', primary[takes['voice_line']['id']]['path'])
        assert re.fullmatch(r'Art/import-[0-9a-f]{6}_rune-sketch\.webp', primary[takes['sketch']['id']]['path'])
        assert re.fullmatch(r'Art/\d+A_a-dwarf-blacksmith-soot-on-his-apron\.png', primary[takes['art']['id']]['path'])
        # A mesh with no description of its own takes its source's words.
        assert re.fullmatch(r'Models/\d+A_a-dwarf-blacksmith-soot-on-his-apron\.glb', primary[takes['model']['id']]['path'])

        # Real parsers read every format; only the WebP needs a PNG, for Unity and Unreal Engine.
        assert png_size((root / primary[takes['art']['id']]['path']).read_bytes()) == (12, 8)
        sketch = (root / primary[takes['sketch']['id']]['path']).read_bytes()
        assert webp_size(sketch) == (12, 8)
        conversions = [entry for entry in listing['files'] if entry['role'] == 'conversion']
        assert len(conversions) == 1
        converted = conversions[0]
        assert converted['conversion_of'] == primary[takes['sketch']['id']]['path']
        assert converted['path'] == converted['conversion_of'].removesuffix('.webp') + '.png'
        assert converted['needed_by'] == ['Unity', 'Unreal Engine'] and converted['media_type'] == 'image/png'
        png = (root / converted['path']).read_bytes()
        assert png_size(png) == (12, 8) and pixels(png) == pixels(sketch)
        model = glb_document((root / primary[takes['model']['id']]['path']).read_bytes())
        assert model['asset']['version'] == '2.0' and model['meshes'][0]['primitives'][0]['attributes'] == {'POSITION': 0}
        for role in ('voice_preview', 'voice_line', 'sound_effect'):
            assert wav_facts((root / primary[takes[role]['id']]['path']).read_bytes()) == (1, 2, RATE, int(SECONDS * RATE))

        # A source the package also delivers is referenced at its package path, never copied twice.
        assert written['model']['input_images'][0]['file'] == '../' + primary[takes['art']['id']]['path']
        assert written['voice_line']['input_images'][0]['file'] == \
            'Previews/' + PurePosixPath(primary[takes['voice_preview']['id']]['path']).name
        assert not [path for path in on_disk if '_inputs/' in path]
        # The attempt's other voices keep their records, not their bytes.
        siblings = [o for o in written['voice_preview']['execution']['outputs'] if o['node_id'] != takes['voice_preview']['id']]
        assert {o['node_id'] for o in siblings} == {take['id'] for take in previews} - {takes['voice_preview']['id']}
        assert all(o['file'] is None and o['sha256'] for o in siblings)
        assert sum(path.endswith('.wav') for path in on_disk) == 3
        # The mesh's voxel field, the 3D engine's own state, stays in the store: its record travels, not its bytes.
        (field,) = [o for o in written['model']['execution']['outputs'] if o['filename'].endswith(FIELDS_SUFFIX)]
        assert (field['file'], field['available'], field['role']) == (None, True, 'companion')
        assert (field['sha256'], field['size_bytes']) == (hashlib.sha256(VOXELS).hexdigest(), len(VOXELS))
        assert fields_sidecar(takes['model']['output_path']).read_bytes() == VOXELS
        assert not [path for path in on_disk if path.endswith('.npz') or '_outputs/' in path]
        assert not [role for role, manifest in written.items() if COPY.search(json.dumps(manifest))]

        # Audio manifests carry the measured facts, the words, the voice and the provider's terms.
        line, sound, preview = written['voice_line'], written['sound_effect'], written['voice_preview']
        assert {key: line['audio'][key] for key in ('container', 'codec', 'sample_rate', 'channels', 'duration_s')} == \
            {'container': 'wav', 'codec': 'pcm_s16le', 'sample_rate': RATE, 'channels': 1, 'duration_s': SECONDS}
        assert isinstance(line['audio']['loudness_lufs'], float) and isinstance(line['audio']['peak_dbfs'], float)
        assert line['audio']['text'] == LINE and line['audio']['voice']['provider'] == 'voice-runner'
        assert line['audio']['terms'] is None and preview['audio']['text'] == PREVIEW
        assert line['audio_version'] == sound['audio_version'] == preview['audio_version'] == manifests.AUDIO_VERSION == 1
        assert (line['verb'], line['transport'], line['provider'], line['local_only'], line['resolution']) == \
            ('speak', 'local', 'voice-runner', True, None)
        assert line['replayable'] == manifests.REPLAY_LOCAL_AUDIO
        assert line['model']['family'] == takes['voice_line']['external']['model_id']
        assert (sound['verb'], sound['transport'], sound['provider'], sound['local_only']) == \
            ('sfx', 'elevenlabs', 'elevenlabs', False)
        assert sound['replayable'] == manifests.REPLAY_EXTERNAL
        assert sound['audio']['terms']['no_ai_input'] is True and sound['audio']['terms']['allows'] == ['elevenlabs']
        assert written['art']['audio'] is written['art']['audio_version'] is None
        assert written['art']['resolution'] == {'width': 12, 'height': 8}

        # The record summary has one row per take, with its path and hash.
        rows = list(csv.DictReader(io.StringIO((root / 'records.csv').read_text())))
        assert list(rows[0]) == crexil_delivery.SUMMARY
        assert [row['node_id'] for row in rows] == [takes[role]['id'] for role in ORDER]
        assert all(row['delivery_id'] == delivery['id'] and row['delivered_at'] and listed[row['path']]['sha256'] == row['sha256']
                   for row in rows)

        # The decision record holds who chose which take, among which, and why: never audio, never a path.
        text = (root / 'decisions.json').read_text()
        record = json.loads(text)
        assert record['purpose'] == decisions.PURPOSE
        picks = {line['reason']: line for line in record['records'] if line['kind'] == 'selection'}
        voice = picks['Warmest growl, clearest consonants']
        assert voice['chosen'] == [takes['voice_preview']['id']] and voice['author'] == {'kind': 'human', 'name': 'Artist'}
        assert sorted(c['node_id'] for c in voice['candidates']) == sorted(take['id'] for take in previews)
        assert picks['The soot reads at thumbnail size']['chosen'] == [takes['art']['id']]
        assert 'RIFF' not in text and 'base64' not in text and str(tmp_path) not in text
        assert not re.search(r'\.(wav|png|glb|webp|json)\b', text)
        for role in ORDER:
            assert len(await store().node_events(takes[role]['id'], 'node.promoted')) == 1


async def test_an_elevenlabs_sound_lands_in_the_package_as_stereo_at_its_requested_length(tmp_path, monkeypatch):
    """The real adapter against a synthetic ElevenLabs that answers as the live
    account does: interleaved stereo PCM, after a provider receipt."""
    monkeypatch.setattr(config, 'ELEVENLABS_URL', ELEVENLABS)
    fake = FakeElevenLabs().on('POST', '/v1/sound-generation', sounded())
    engines = {'elevenlabs': ElevenLabsExecutor(transport=httpx.MockTransport(fake))}
    async with booted_audio(tmp_path, monkeypatch, rows=[free_row('elevenlabs-sfx', 'test-eleven-sfx')],
                            executors=engines) as (client, _):
        wid = (await project(client))['id']
        sound = await draft(client, wid, 'audio:sfx:test-eleven-sfx', 'Forge strike on the anvil',
                            controls={'duration_seconds': 2.5})
        _, (strike,) = await submit(client, sound)
        _, delivery = await prepared(client, wid, [strike['id']])
        await sent(client, delivery['id'])
        root = await package_root(delivery['id'])
        (entry,) = [e for e in json.loads((root / 'package.json').read_bytes())['files'] if e.get('asset_id')]
        # Named by its grid label, which counts no receipt, and two channels for the 2.5 s asked for.
        assert entry['path'] == 'Audio/SFX/0A_forge-strike-on-the-anvil.wav'
        assert wav_facts((root / entry['path']).read_bytes()) == (2, 2, 48000, 120000)
        manifest = json.loads((root / entry['manifest']).read_bytes())
        assert {key: manifest['audio'][key] for key in ('container', 'codec', 'sample_rate', 'channels', 'duration_s')} \
            == {'container': 'wav', 'codec': 'pcm_s16le', 'sample_rate': 48000, 'channels': 2, 'duration_s': 2.5}
        assert isinstance(manifest['audio']['loudness_lufs'], float) and isinstance(manifest['audio']['peak_dbfs'], float)
        assert (manifest['turn']['idx'], manifest['slot']['pos'], manifest['take']) == (0, 0, 1)
        # The provider's receipt stays in the store; the manifest names it by hash and keeps its ids.
        (receipt,) = [o for o in manifest['execution']['outputs'] if o['role'] == 'provider receipt']
        assert receipt['file'] is None and receipt['available'] and receipt['sha256'] and receipt['size_bytes']
        assert (manifest['external']['charge_basis'], manifest['external']['charged_micro_usd']) == ('published_tariff', 0)
        assert tree(root) == sorted([entry['path'], entry['manifest'], *crexil_delivery.PACKAGE_FILES])


async def test_a_hosted_image_travels_once_without_its_receipt_or_a_base64_copy_in_its_manifest(tmp_path, monkeypatch):
    """A hosted continuation as OpenRouter answers it: its request carries the source
    as a data URL, and its answer each image as base64, which the store keeps whole."""
    image = noise_png(160)
    async with image_bench(tmp_path, monkeypatch) as (client, _):
        discover()
        orc = wiring.current.openrouter

        def answer(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'endpoints': [ENDPOINT]})
            return httpx.Response(200, json={'id': 'gen-1', 'created': 42, 'usage': {'cost': .09},
                                             'data': [{'b64_json': base64.b64encode(image).decode()}] * 2})
        await orc._client.aclose()
        orc._client = httpx.AsyncClient(base_url=config.OPENROUTER_URL, transport=httpx.MockTransport(answer))
        wiring.current.scheduler._executors['openrouter'] = OpenRouterExecutor(orc)
        wid = (await project(client))['id']
        source = (await imported(client, wid))['node']
        run = await hosted(client, wid, action='continue', controls={'n': 2},
                           references=[{'node_id': source['id'], 'contribution': 'Keep the patina'}])
        check = await review_of(client, run)
        assert check['ready'], check['errors']
        submitted = await client.post(f"/api/crexil/directions/{run['id']}/submit",
                                      json=command(revision=run['revision'], review_token=check['token']))
        assert submitted.status_code == 201, submitted.text
        for _ in range(200):
            takes = [n for n in await store().list_nodes(wid) if n['turn_id'] == submitted.json()['turn_id']]
            if len(takes) == 2 and (await store().get_turn(takes[0]['turn_id']))['slots'][0]['status'] == 'landed':
                break
            await asyncio.sleep(.01)
        first = next(take for take in takes if take['output_index'] == 0)
        # The receipt the store keeps holds the provider's whole answer, base64 included.
        receipt_row = await store()._one("SELECT o.output_path FROM execution_outputs o JOIN execution_groups g "
                                         "ON g.id=o.group_id WHERE g.slot_id=? AND o.role='provider receipt'",
                                         (first['slot_id'],))
        assert base64.b64encode(image).decode() in Path(receipt_row['output_path']).read_text()
        _, delivery = await prepared(client, wid, [first['id']])
        await sent(client, delivery['id'])
        root = await package_root(delivery['id'])
    listing = json.loads((root / 'package.json').read_bytes())
    (entry,) = [e for e in listing['files'] if e.get('asset_id')]
    assert (root / entry['path']).read_bytes() == image and entry['sha256'] == hashlib.sha256(image).hexdigest()
    assert tree(root) == sorted([entry['path'], entry['manifest'], *crexil_delivery.PACKAGE_FILES,
                                 entry['path'].rsplit('.', 1)[0] + '_inputs/source.png'])
    text = (root / entry['manifest']).read_text()
    manifest = json.loads(text)
    # The take travels once: its manifest is smaller than it, and holds no base64 copy of it or its source.
    assert len(text) < len(image) and not COPY.search(text)
    outputs = {o['role']: o for o in manifest['execution']['outputs'] if o['node_id'] in (None, first['id'])}
    copied = {'omitted': 'base64', 'sha256': entry['sha256'], 'size_bytes': len(image)}
    assert outputs['unclassified']['descriptor']['provider']['b64_json'] == copied
    assert outputs['provider receipt']['file'] is None and outputs['provider receipt']['sha256']
    sibling = next(o for o in manifest['execution']['outputs'] if o['node_id'] not in (None, first['id']))
    assert sibling['file'] is None and sibling['descriptor']['provider']['b64_json'] == copied
    (reference,) = manifest['external']['request']['input_references']
    assert reference['image_url']['url'] == {'omitted': 'base64', 'media_type': 'image/png',
                                             'sha256': manifest['input_images'][0]['sha256'],
                                             'size_bytes': len(Path(source['output_path']).read_bytes())}
    assert manifest['external']['generation_id'] == 'gen-1' and manifest['cost']['charged_usd'] == .09


async def test_a_mesh_travels_without_its_voxel_field_even_when_the_field_is_gone(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        attest()
        fakes['comfy'].default = Step(data=picture())
        wid = (await project(client))['id']
        _, (portrait,) = await submit(client, await draft(client, wid, 'testpreset', 'A dwarf blacksmith'))
        mesh = await mesh_of(client, wid, portrait)
        fields_sidecar(mesh['output_path']).unlink()
        _, delivery = await prepared(client, wid, [mesh['id']])
        assert (await sent(client, delivery['id']))['items'][0]['state'] == 'complete'
        root = await package_root(delivery['id'])
    (entry,) = [e for e in json.loads((root / 'package.json').read_bytes())['files'] if e.get('asset_id')]
    manifest = json.loads((root / entry['manifest']).read_bytes())
    (field,) = [o for o in manifest['execution']['outputs'] if o['filename'].endswith(FIELDS_SUFFIX)]
    assert (field['file'], field['available'], field['sha256']) == (None, False, hashlib.sha256(VOXELS).hexdigest())
    assert not [path for path in tree(root) if path.endswith('.npz')]


async def test_review_hashes_each_file_once_outside_the_lock_and_prepare_hashes_nothing(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, fakes):
        wid, takes, _ = await character(client, fakes)
        ids = [takes[role]['id'] for role in ORDER]
        hashed, entered, release = [], threading.Event(), threading.Event()
        original = crexil_delivery._hash

        def held(path):
            hashed.append(str(path))
            entered.set()
            assert release.wait(timeout=10)
            return original(path)
        monkeypatch.setattr(crexil_delivery, '_hash', held)
        selection = {'node_ids': ids, 'destination': str(config.PROMOTE_DIR)}
        review = asyncio.create_task(client.post(f'/api/crexil/projects/{wid}/deliveries/review', json=selection))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            # A read that takes the store's lock completes while the review hashes a file.
            snapshot = await asyncio.wait_for(client.get(f'/api/crexil/projects/{wid}'), timeout=5)
            assert snapshot.status_code == 200
        finally:
            release.set()
        reviewed_once = (await review).json()
        assert reviewed_once['ready'], reviewed_once['errors']
        assert hashed and len(hashed) == len(set(hashed))
        assert takes['sketch']['output_path'] in hashed and takes['sound_effect']['output_path'] in hashed

        hashed.clear()
        _, again = await reviewed(client, wid, ids)
        assert hashed == []                                 # a second review reads the cache
        def forbidden(*args, **kwargs):
            raise AssertionError('Preparing a delivery must not hash or convert a file')
        monkeypatch.setattr(crexil_delivery, '_hash', forbidden)
        monkeypatch.setattr(crexil_delivery, '_converted', forbidden)
        accepted = await client.post(f'/api/crexil/projects/{wid}/deliveries',
                                     json=command(**selection, review_token=again['token']))
        assert accepted.status_code == 201, accepted.text
        monkeypatch.setattr(crexil_delivery, '_hash', original)
        assert all(item['state'] == 'complete' for item in (await sent(client, accepted.json()['id']))['items'])


async def test_a_retry_keeps_the_same_identities_and_the_package_files_land_last(tmp_path, monkeypatch, handoff):
    async with booted(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        plate = await material(client, wid, 'Anvil plate.png', picture())
        scan = await material(client, wid, 'Hammer scan.tiff', picture('TIFF', color=(20, 120, 200, 255)))
        _, delivery = await prepared(client, wid, [plate['id'], scan['id']])
        original = crexil_delivery._chunks

        def unplugged(source):
            if str(source) == scan['output_path']:
                raise OSError('Drive disconnected')
            return original(source)
        monkeypatch.setattr(crexil_delivery, '_chunks', unplugged)
        first = await sent(client, delivery['id'])
        assert [item['state'] for item in first['items']] == ['complete', 'incomplete']
        assert not any(Path(f['path']).exists() for f in first['items'][1]['files'])
        root = await package_root(delivery['id'])
        assert not (root / 'package.json').exists()        # the package is whole before it is listed
        kept = {f['path']: Path(f['path']).stat().st_mtime_ns for f in first['items'][0]['files']}

        monkeypatch.setattr(crexil_delivery, '_chunks', original)
        resumed = await sent(client, delivery['id'])
        assert all(item['state'] == 'complete' for item in resumed['items'])
        assert [(i['asset_id'], i['files']) for i in resumed['items']] == [(i['asset_id'], i['files']) for i in delivery['items']]
        assert kept == {path: Path(path).stat().st_mtime_ns for path in kept}
        listing = json.loads((root / 'package.json').read_bytes())
        converted = next(entry for entry in listing['files'] if entry['role'] == 'conversion')
        assert converted['needed_by'] == ['Godot'] and converted['conversion_of'].endswith('.tiff')
        assert png_size((root / converted['path']).read_bytes()) == (12, 8)
        assert pixels((root / converted['path']).read_bytes()) == pixels(Path(scan['output_path']).read_bytes())

        package = (root / 'package.json').stat().st_mtime_ns
        await sent(client, delivery['id'])                   # nothing left to do: nothing is written again
        assert (root / 'package.json').stat().st_mtime_ns == package
        events = await store().events_since(0, workspace_id=wid)
        assert sum(e['type'] == 'delivery.package' for e in events) == 1
        assert [e['payload']['state'] for e in events if e['type'] == 'delivery.item'] == ['complete', 'incomplete', 'complete']
        for node in (plate, scan):
            assert len(await store().node_events(node['id'], 'node.promoted')) == 1
        assert sorted(tree(handoff)) == sorted(str(Path(root.name) / path) for path in tree(root))


async def test_a_blind_take_is_refused_until_its_reveal_and_names_no_engine_meanwhile(tmp_path, monkeypatch):
    async with casting(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        _, turn, takes = await cast(client, wid)
        terms = await identity_terms(turn)
        hidden = takes[0]
        selection, review = await reviewed(client, wid, [hidden['id']])
        assert not review['ready'] and review['items'][0]['files'] == []
        assert 'blind run' in review['items'][0]['error'] and not leaks(review, terms)
        refused = await client.post(f'/api/crexil/projects/{wid}/deliveries', json=command(**selection, review_token=review['token']))
        assert refused.status_code == 422 and 'blind run' in refused.json()['detail']
        assert await crexil_delivery.inventory(store(), wid) == []
        revealed = await client.post(f"/api/crexil/turns/{turn['id']}/reveal", json=command(reason='Done listening'))
        assert revealed.status_code == 200, revealed.text
        _, delivery = await prepared(client, wid, [hidden['id']])
        item = (await sent(client, delivery['id']))['items'][0]
        assert item['state'] == 'complete' and '/Audio/Voice/Previews/' in item['files'][0]['path']
        manifest = json.loads(Path(next(f['path'] for f in item['files'] if f['path'].endswith('.json'))).read_bytes())
        assert manifest['engine'] in (LOCAL, HOSTED) and manifest['audio']['voice']['provider'] in ('voice-runner', 'elevenlabs')


async def test_an_agent_reviews_a_delivery_but_only_the_artist_prepares_and_sends_it(tmp_path, monkeypatch, handoff):
    async with booted(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        node = await material(client, wid, 'Anvil plate.png', picture())
        selection, review = await reviewed(client, wid, [node['id']])
        assert review['ready']
        asked = await client.post(f'/api/crexil/projects/{wid}/deliveries',
                                  json={**command(**selection, review_token=review['token']), 'actor': AGENT})
        assert asked.status_code == 403 and "artist's act" in asked.json()['detail']
        assert await crexil_delivery.inventory(store(), wid) == []
        accepted = await client.post(f'/api/crexil/projects/{wid}/deliveries',
                                     json=command(**selection, review_token=review['token']))
        assert accepted.status_code == 201, accepted.text   # the agent's refusal left the reviewed plan intact
        did = accepted.json()['id']
        pushed = await client.post(f'/api/crexil/deliveries/{did}/transfer', json={**command(), 'actor': AGENT})
        assert pushed.status_code == 403 and tree(handoff) == []
        assert (await sent(client, did))['items'][0]['state'] == 'complete'


async def test_preparing_needs_the_current_review_of_exactly_this_request(tmp_path, monkeypatch):
    async with booted(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        node = await material(client, wid, 'Anvil plate.png', picture())
        url = f'/api/crexil/projects/{wid}/deliveries'
        selection, review = await reviewed(client, wid, [node['id']])
        unknown = await client.post(url, json=command(**selection, review_token='0' * 64))
        assert unknown.status_code == 409 and 'Review this delivery again' in unknown.json()['detail']
        elsewhere = await client.post(url, json=command(node_ids=[node['id']], destination=str(config.PROMOTE_DIR / 'other'),
                                                        review_token=review['token']))
        assert elsewhere.status_code == 409
        selection, review = await reviewed(client, wid, [node['id']])
        assert (await client.post(url, json=command(**selection, review_token=review['token']))).status_code == 201
        twice = await client.post(url, json=command(**selection, review_token=review['token']))
        assert twice.status_code == 409                     # one reviewed plan makes one delivery
        selection, review = await reviewed(client, wid, [node['id']])
        crexil_delivery._reviews.clear()                     # a restart forgets reviewed plans
        assert (await client.post(url, json=command(**selection, review_token=review['token']))).status_code == 409
        # Reviewed with no destination, prepared with the hand-off folder the review resolved,
        # as the delivery room sends it: the same request.
        review = (await client.post(f'/api/crexil/projects/{wid}/deliveries/review',
                                    json={'node_ids': [node['id']], 'destination': ''})).json()
        assert review['destination'] == str(config.PROMOTE_DIR.resolve())
        resolved = await client.post(url, json=command(node_ids=[node['id']], destination=review['destination'],
                                                       review_token=review['token']))
        assert resolved.status_code == 201, resolved.text
        assert len(await crexil_delivery.inventory(store(), wid)) == 2


async def test_a_restored_package_keeps_its_receipts_as_history_and_never_sends_again(tmp_path, monkeypatch, handoff):
    async with booted(tmp_path, monkeypatch) as (client, _):
        wid = (await project(client))['id']
        node = await material(client, wid, 'Anvil plate.png', picture())
        _, delivery = await prepared(client, wid, [node['id']])
        await sent(client, delivery['id'])
        written = tree(handoff)
        upload, _ = await backup_upload(client, wid)
        copy = await restored(client, upload)
        history = (await client.get(f"/api/crexil/projects/{copy['project']['id']}/deliveries")).json()
        assert [item['state'] for item in history[0]['items']] == ['complete']
        intent = await crexil_delivery._intent(store(), history[0]['id'])
        assert intent['package']['state'] == 'complete' and intent['archive_origin']
        again = await client.post(f"/api/crexil/deliveries/{history[0]['id']}/transfer", json=command())
        assert again.status_code == 422 and 'Restored delivery history cannot transfer' in again.json()['detail']
        assert tree(handoff) == written
