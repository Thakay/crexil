"""The RegionExecutor (app/region_exec.py) over a scripted fake
ComfyClient: the pass chain the envelope promises, with the real inner
ComfyExecutor's completion/blank/oom/interrupt mechanics in the loop.

    sequencing   passes submit in plan order; each pass's source is the
                 WORKING frame cropped to the pass box, content-addressed
                 into the input dir; pass N+1 sees pass N's paste-back
    compositing  every pass clips to its effective mask; instruction-tier
                 feather stays inside that mask; KEEP and source alpha
                 stay exact after every pass
    record       the as-run region_plan rides ExecutionResult.submitted_graph
                 with every pass's exact graph + prompt_id
    failures     a pass failure is the take's failure with the pass named
                 and the kind preserved (oom stays oom, blank keeps its
                 artefact); a missing staged file is an error in words
    cancel       pre-flight cancel stops before submit; mid-pass cancel
                 interrupts the inner pass and nothing further submits
    protocol     start refuses an empty plan; reconcile is "gone"

The plan/envelope builder is test_regionrun.py's; the route that stages
the files is test_api_v4_regions.py's.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from app import config, regions
from app import presets as presets_mod
from app import regionrun
from app.executors import EngineFailure
from app.executors import DispatchRequest
from app.region_exec import RegionExecutor

pytestmark = pytest.mark.anyio

SIZE = (1024, 768)
CATALOG = presets_mod.load_presets()


def content_png(color, size):
    """A decodable plate WITH content (an inner square) so A5a passes."""
    im = Image.new("RGB", size, color)
    inner = tuple((c + 120) % 256 for c in color)
    im.paste(inner, (size[0] // 4, size[1] // 4,
                     size[0] // 2, size[1] // 2))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def flat_png(color, size):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def rect_mask(box, size=SIZE):
    m = Image.new("L", size, 0)
    m.paste(255, box)
    return m


def region(rid, action, z, prompt=None):
    return {"id": rid, "action": action, "z_order": z, "prompt": prompt,
            "name": rid}


KEEP_BOX = (0, 0, 300, 768)
MODIFY_BOX = (500, 300, 800, 560)
DELETE_BOX = (700, 40, 850, 160)   # clear of KEEP — else it is occluded


def make_envelope(tmp_path, preset_id="raw-sdxl", seed=11,
                  include_delete=True, source_mode="RGB", modify_box=MODIFY_BOX):
    """A real staged envelope: keep + modify (+ delete) on a noise source."""
    source = Image.effect_noise(SIZE, 60).convert("RGB")
    if source_mode == "RGBA":
        source = source.convert("RGBA")
        alpha = Image.linear_gradient("L").resize(SIZE)
        source.putalpha(alpha)
    src_path = tmp_path / "source.png"
    source.save(src_path)

    rows = [region("rk", "keep", 1), region("rm", "modify", 2, "blue flame")]
    masks = {"rk": rect_mask(KEEP_BOX), "rm": rect_mask(modify_box)}
    if include_delete:
        rows.append(region("rd", "delete", 3))
        masks["rd"] = rect_mask(DELETE_BOX)
    plan = regionrun.build_region_plan(rows, masks, SIZE, None)
    for p in plan["passes"]:
        mp = tmp_path / f"mask-p{p['idx']}.png"
        mp.write_bytes(p["mask"])
        p["mask_path"] = str(mp)
        p["mask_sha256"] = regions.sha256_bytes(p["mask"])
        p["mask_upload"] = f"cockpit-mask-p{p['idx']}.png"
    keep_path = tmp_path / "keep.png"
    keep_path.write_bytes(plan["keep_mask"])

    preset = CATALOG[preset_id]
    specs = {s["key"]: s for s in preset["inputs"]
             if s.get("supported", True)
             and ("region" in s["capabilities"] if s.get("capabilities")
                  else True)}
    applied = {k: s.get("default") for k, s in specs.items()
               if s.get("type") != "seed"}
    env = regionrun.build_engine_envelope(
        preset, applied, specs, plan,
        source={"path": str(src_path), "sha256": regions.sha256_bytes(src_path.read_bytes()),
                "name": "cockpit-src.png"},
        keep={"path": str(keep_path), "sha256": regions.sha256_bytes(plan["keep_mask"])},
        region_ids=["rk", "rm", "rd"])
    env = regionrun.stamp_take(env, seed, "tok", 0)
    return env, source, plan


class FakeComfy:
    """Duck-typed ComfyClient: per-pass scripted outcomes.

    script entries, consumed per submit:
      ("land", size)        -> a content plate at that size
      ("flat", size)        -> a flat plate (the A5a case)
      ("error", exc_type)   -> history reports an execution error
      ("gate",)             -> history stays empty until .gate is set,
                               then reports an interrupted-style error
    """

    def __init__(self, script):
        self.script = list(script)
        self.by_pid = {}
        self.submitted = []
        self.uploads = {}
        self.gate = asyncio.Event()
        self.interrupts = 0

    def start_ws_mirror(self, handler):
        self.handler = handler

    async def close(self):
        pass

    async def submit(self, graph):
        step = self.script[len(self.submitted)]
        pid = f"pid-{len(self.submitted)}"
        self.submitted.append((pid, graph))
        self.by_pid[pid] = step
        return pid

    async def history(self, pid):
        step = self.by_pid[pid]
        if step[0] == "gate":
            if not self.gate.is_set():
                return None
            return {"status": {"status_str": "error", "messages": [
                ["execution_error", {"exception_type": "Interrupted",
                                     "exception_message": "interrupted"}]]}}
        if step[0] == "error":
            return {"status": {"status_str": "error", "messages": [
                ["execution_error", {"exception_type": step[1],
                                     "exception_message": step[2]}]]}}
        return {"status": {"status_str": "success"},
                "outputs": {"9": {"images": [
                    {"filename": f"{pid}.png", "type": "output",
                     "subfolder": ""}]}}}

    async def view(self, filename, subfolder="", type_="output"):
        pid = filename[:-4]
        step = self.by_pid[pid]
        color = (40 * (int(pid[-1]) + 2) % 255, 80, 120)
        size = step[1]
        if step[0] == "flat":
            return flat_png((0, 0, 0), size)
        return content_png(color, size)

    async def queue_state(self):
        running = [[0, pid] for pid, s in self.by_pid.items()
                   if s[0] == "gate"]
        return {"queue_pending": [], "queue_running": running}

    async def interrupt(self):
        self.interrupts += 1
        self.gate.set()

    async def delete_queued(self, pid):
        self.gate.set()

    async def upload_image(self, blob, name):
        self.uploads[name] = blob
        return name


def box_size(env, idx):
    b = env["region_plan"]["passes"][idx]["box"]
    return (SIZE if b is None else (b[2] - b[0], b[3] - b[1]))


def request(env):
    return DispatchRequest(slot_id="s1", engine="raw-sdxl",
                           submitted_graph=env, verb="region", seed=11)


@pytest.fixture
def fast_poll(monkeypatch):
    monkeypatch.setattr(config, "COMFY_POLL_S", 0.01)
    monkeypatch.setattr(config, "HISTORY_CONFIRM_INTERVAL_S", 0.005)


# --- the chain ---


async def test_passes_run_in_order_on_working_frame_crops(tmp_path,
                                                          fast_poll):
    env, source, _ = make_envelope(tmp_path)
    fake = FakeComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    ex = RegionExecutor(fake)
    result = await ex.wait(await ex.start(request(env)))

    assert len(fake.submitted) == 2
    # every submitted graph carries a content-addressed source crop that
    # was actually uploaded
    for _pid, graph in fake.submitted:
        titles = presets_mod.find_nodes_by_title(graph, "source_image")
        name = graph[titles[0]]["inputs"]["image"]
        assert name.startswith("cockpit-") and name in fake.uploads
    # pass 0 (remove) saw the PRISTINE source cropped to its own box;
    # pass 1 (modify) saw the WORKING frame — pass 0's result pasted in —
    # cropped to its box
    p0 = env["region_plan"]["passes"][0]
    p0_name = fake.submitted[0][1][presets_mod.find_nodes_by_title(
        fake.submitted[0][1], "source_image")[0]]["inputs"]["image"]
    p0_crop = Image.open(io.BytesIO(fake.uploads[p0_name])).convert("RGB")
    assert p0_crop.tobytes() == source.crop(tuple(p0["box"])).tobytes()
    p1_name = fake.submitted[1][1][presets_mod.find_nodes_by_title(
        fake.submitted[1][1], "source_image")[0]]["inputs"]["image"]
    p1_crop = Image.open(io.BytesIO(fake.uploads[p1_name]))
    assert p1_crop.size == box_size(env, 1)
    # the delete region (inside pass 1's working frame if boxes overlap, or
    # at least the final result) shows pass 0's plate, not the source
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.size == SIZE


async def test_keep_is_byte_exact_and_work_regions_changed(tmp_path,
                                                           fast_poll):
    env, source, _ = make_envelope(tmp_path)
    fake = FakeComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    ex = RegionExecutor(fake)
    result = await ex.wait(await ex.start(request(env)))
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.crop(KEEP_BOX).tobytes() == source.crop(KEEP_BOX).tobytes()
    center = ((MODIFY_BOX[0] + MODIFY_BOX[2]) // 2,
              (MODIFY_BOX[1] + MODIFY_BOX[3]) // 2)
    assert out.getpixel(center) != source.getpixel(center)


async def test_instruction_tier_pastes_only_inside_the_mask(tmp_path,
                                                            fast_poll):
    env, source, _ = make_envelope(tmp_path, preset_id="raw-flux2",
                                   include_delete=False)
    assert len(env["region_plan"]["passes"]) == 1     # the modify pass only
    fake = FakeComfy([("land", box_size(env, 0))])
    ex = RegionExecutor(fake)
    result = await ex.wait(await ex.start(request(env)))
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    # the modify pass's box is bigger than its mask; in-box-off-mask pixels
    # (allowing the feather) must still be the working frame's
    b = env["region_plan"]["passes"][0]["box"]
    probe = (MODIFY_BOX[0] - 8, MODIFY_BOX[1] - 8)
    assert b[0] <= probe[0] and b[1] <= probe[1]      # inside the box
    assert out.getpixel(probe) == source.getpixel(probe)
    center = ((MODIFY_BOX[0] + MODIFY_BOX[2]) // 2,
              (MODIFY_BOX[1] + MODIFY_BOX[3]) // 2)
    assert out.getpixel(center) != source.getpixel(center)


@pytest.mark.parametrize("preset_id", ["raw-sdxl", "raw-flux2"])
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
async def test_all_runtime_tiers_preserve_outside_and_overlapping_keep(
        tmp_path, fast_poll, preset_id, mode):
    env, source, plan = make_envelope(
        tmp_path, preset_id=preset_id, source_mode=mode,
        modify_box=(200, 300, 800, 560))
    fake = FakeComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    executor = RegionExecutor(fake)
    result = await executor.wait(await executor.start(request(env)))
    out = Image.open(io.BytesIO(result.data))
    edit = Image.new("L", SIZE, 0)
    for p in plan["passes"]:
        edit = ImageChops.lighter(edit, regions.load_mask(p["mask"], SIZE))
    outside = ImageChops.invert(edit)
    blank = Image.new(mode, SIZE)
    assert out.mode == mode
    assert Image.composite(out, blank, outside).tobytes() == \
        Image.composite(source, blank, outside).tobytes()
    assert out.crop(KEEP_BOX).tobytes() == source.crop(KEEP_BOX).tobytes()
    assert out.getpixel((600, 400)) != source.getpixel((600, 400))
    if mode == "RGBA":
        assert out.getchannel("A").tobytes() == source.getchannel("A").tobytes()


async def test_keep_is_restored_before_the_next_pass_upload(tmp_path, fast_poll):
    env, source, _ = make_envelope(tmp_path)
    # Even a prepared pass mask that overlaps KEEP cannot damage the protected
    # source supplied to the next operation. Both crops include the KEEP edge.
    for p in env["region_plan"]["passes"]:
        p["box"] = None
    mask_path = env["region_plan"]["passes"][0]["mask"]["path"]
    rect_mask((0, 0, 1024, 768)).save(mask_path)
    env["region_plan"]["passes"][0]["mask"]["sha256"] = regions.sha256_bytes(
        Path(mask_path).read_bytes())
    fake = FakeComfy([("land", SIZE), ("land", SIZE)])
    executor = RegionExecutor(fake)
    await executor.wait(await executor.start(request(env)))
    graph = fake.submitted[1][1]
    node = presets_mod.find_nodes_by_title(graph, "source_image")[0]
    uploaded = Image.open(io.BytesIO(fake.uploads[graph[node]["inputs"]["image"]]))
    assert uploaded.crop(KEEP_BOX).tobytes() == source.crop(KEEP_BOX).tobytes()


async def test_as_run_record_carries_graphs_and_prompt_ids(tmp_path,
                                                           fast_poll):
    env, _, _ = make_envelope(tmp_path)
    fake = FakeComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    ex = RegionExecutor(fake)
    result = await ex.wait(await ex.start(request(env)))
    rec = result.submitted_graph["region_plan"]
    assert rec["keep_applied"] is True
    assert [p["prompt_id"] for p in rec["passes"]] == ["pid-0", "pid-1"]
    for p, (_pid, graph) in zip(rec["passes"], fake.submitted, strict=True):
        assert p["graph"] == graph                    # the EXACT submission


# --- failures ---


async def test_pass_failure_names_the_pass_and_keeps_the_kind(tmp_path,
                                                              fast_poll):
    env, _, _ = make_envelope(tmp_path)
    fake = FakeComfy([("land", box_size(env, 0)),
                      ("error", "torch.OutOfMemoryError",
                       "CUDA out of memory")])
    ex = RegionExecutor(fake)
    with pytest.raises(EngineFailure) as ei:
        await ex.wait(await ex.start(request(env)))
    assert ei.value.kind == "oom"
    assert "pass 2/2 (modify)" in str(ei.value)


async def test_blank_pass_keeps_its_artefact(tmp_path, fast_poll):
    env, _, _ = make_envelope(tmp_path)
    fake = FakeComfy([("flat", box_size(env, 0))])
    ex = RegionExecutor(fake)
    with pytest.raises(EngineFailure) as ei:
        await ex.wait(await ex.start(request(env)))
    assert ei.value.kind == "blank"
    assert ei.value.artefact is not None
    assert "pass 1/2 (remove)" in str(ei.value)


async def test_missing_source_is_an_error_in_words(tmp_path, fast_poll):
    env, _, _ = make_envelope(tmp_path)
    env["region_plan"]["source"]["path"] = str(tmp_path / "gone.png")
    ex = RegionExecutor(FakeComfy([]))
    with pytest.raises(EngineFailure) as ei:
        await ex.wait(await ex.start(request(env)))
    assert ei.value.kind == "error"
    assert "went missing" in str(ei.value)


@pytest.mark.parametrize(("target", "reason"), [
    ("source", "source image"), ("keep", "KEEP mask"),
    (0, "pass 1 mask"), (1, "pass 2 mask"),
])
async def test_changed_frozen_bytes_refuse_the_entire_take_before_upload(
        tmp_path, target, reason):
    env, _, _ = make_envelope(tmp_path)
    plan = env["region_plan"]
    ref = plan[target] if isinstance(target, str) else plan["passes"][target]["mask"]
    # Still a valid image at the right size: digest drift is the refusal reason.
    Path(ref["path"]).write_bytes(flat_png((255, 0, 255), SIZE))
    fake = FakeComfy([])
    executor = RegionExecutor(fake)
    with pytest.raises(EngineFailure, match=f"{reason} changed.*SHA256") as failure:
        await executor.wait(await executor.start(request(env)))
    assert failure.value.kind == "error"
    assert fake.uploads == {}
    assert fake.submitted == []


async def test_verified_masks_remain_frozen_when_staging_changes_during_upload(
        tmp_path, fast_poll):
    env, source, plan = make_envelope(tmp_path)

    class ChangedStagingComfy(FakeComfy):
        async def upload_image(self, blob, name):
            for ref in [env["region_plan"]["source"], env["region_plan"]["keep"],
                        *(p["mask"] for p in env["region_plan"]["passes"])]:
                rect_mask((0, 0, *SIZE)).save(ref["path"])
            return await super().upload_image(blob, name)

    fake = ChangedStagingComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    executor = RegionExecutor(fake)
    result = await executor.wait(await executor.start(request(env)))
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    edit = Image.new("L", SIZE, 0)
    for p in plan["passes"]:
        edit = ImageChops.lighter(edit, regions.load_mask(p["mask"], SIZE))
    outside = ImageChops.invert(edit)
    blank = Image.new("RGB", SIZE)
    assert Image.composite(out, blank, outside).tobytes() == \
        Image.composite(source, blank, outside).tobytes()
    assert out.crop(KEEP_BOX).tobytes() == source.crop(KEEP_BOX).tobytes()
    assert out.getpixel((600, 400)) != source.getpixel((600, 400))
    assert len(fake.submitted) == 2


async def test_start_refuses_an_empty_plan(tmp_path):
    ex = RegionExecutor(FakeComfy([]))
    with pytest.raises(EngineFailure, match="no plan"):
        await ex.start(DispatchRequest(slot_id="s1", engine="raw-sdxl",
                                       submitted_graph={"region_plan":
                                                        {"passes": []}}))


# --- cancel ---


async def test_preflight_cancel_stops_before_any_submit(tmp_path, fast_poll):
    env, _, _ = make_envelope(tmp_path)
    fake = FakeComfy([])
    ex = RegionExecutor(fake)
    h = await ex.start(request(env))
    await ex.cancel(h)
    with pytest.raises(EngineFailure) as ei:
        await ex.wait(h)
    assert ei.value.kind == "interrupted"
    assert fake.submitted == []


@pytest.mark.parametrize("cancel_pass", [0, 1])
async def test_cancel_during_upload_never_dispatches_that_pass(
        tmp_path, fast_poll, cancel_pass):
    env, _, _ = make_envelope(tmp_path)
    entered = asyncio.Event()
    released = asyncio.Event()

    class GatedUploadComfy(FakeComfy):
        async def upload_image(self, blob, name):
            if len(self.submitted) == cancel_pass:
                entered.set()
                await released.wait()
            return await super().upload_image(blob, name)

    fake = GatedUploadComfy([("land", box_size(env, 0)), ("land", box_size(env, 1))])
    executor = RegionExecutor(fake)
    handle = await executor.start(request(env))
    waiter = asyncio.create_task(executor.wait(handle))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await executor.cancel(handle)
        released.set()
        with pytest.raises(EngineFailure, match="interrupted by cancel") as failure:
            await asyncio.wait_for(waiter, timeout=5)
        assert failure.value.kind == "interrupted"
        assert len(fake.submitted) == cancel_pass
        assert fake.interrupts == 0
    finally:
        released.set()
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


async def test_midpass_cancel_interrupts_and_stops_the_chain(tmp_path,
                                                             fast_poll):
    env, _, _ = make_envelope(tmp_path)
    fake = FakeComfy([("gate",), ("land", box_size(env, 1))])
    ex = RegionExecutor(fake)
    h = await ex.start(request(env))
    waiter = asyncio.create_task(ex.wait(h))
    for _ in range(200):
        if fake.submitted:
            break
        await asyncio.sleep(0.005)
    await ex.cancel(h)
    with pytest.raises(EngineFailure) as ei:
        await waiter
    assert ei.value.kind == "interrupted"
    assert len(fake.submitted) == 1                   # pass 2 never submitted
    assert fake.interrupts == 1


# --- protocol ---


async def test_reconcile_is_gone_and_verify_charge_none(tmp_path):
    ex = RegionExecutor(FakeComfy([]))
    rec = await ex.reconcile({"job_ref": None})
    assert rec.kind == "gone"
    assert await ex.verify_charge("x") is None


async def test_protected_checkpoint_precedes_later_pass_failure(tmp_path, fast_poll):
    from dataclasses import replace
    env, source, _ = make_envelope(tmp_path, source_mode='RGBA')
    collected = []

    async def save(output):
        if output.data is not None:
            collected.append(output)
            # The callback represents durable server acceptance before return.
            (tmp_path / f'checkpoint-{len(collected)}.png').write_bytes(output.data)

    class ObservedComfy(FakeComfy):
        async def submit(self, graph):
            if len(self.submitted) == 1:
                protected = next(o for o in collected if o.identity == 'pass:0/protected')
                assert protected.role == 'intermediate'
                assert Image.open(io.BytesIO(protected.data)).crop(KEEP_BOX).tobytes() == source.crop(KEEP_BOX).tobytes()
                assert any(path.read_bytes() == protected.data for path in tmp_path.glob('checkpoint-*.png'))
                assert protected.submitted_graph['region_plan']['passes'][0]['prompt_id'] == 'pid-0'
            return await super().submit(graph)

    fake = ObservedComfy([('land', box_size(env, 0)), ('error', 'RuntimeError', 'later pass failed')])
    executor = RegionExecutor(fake)
    with pytest.raises(EngineFailure, match='pass 2/2'):
        await executor.wait(await executor.start(replace(request(env), on_output=save)))
    assert len(fake.submitted) == 2
    assert [o.role for o in collected if o.role != 'runtime input'] == ['raw pass output', 'intermediate']
    assert not any(o.role == 'final' for o in collected)
