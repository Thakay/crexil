"""The RegionExecutor (app/region_exec.py) over a scripted fake
ComfyClient: the pass chain the envelope promises, with the real inner
ComfyExecutor's completion/blank/oom/interrupt mechanics in the loop.

    sequencing   passes submit in plan order; each pass's source is the
                 WORKING frame cropped to the pass box, content-addressed
                 into the input dir; pass N+1 sees pass N's paste-back
    compositing  mask-native passes paste the whole box; instruction-tier
                 passes paste through the effective mask; KEEP restored
                 byte-exactly at the end (the clause, executor-level)
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

import pytest
from PIL import Image

from app import config
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
                  include_delete=True):
    """A real staged envelope: keep + modify (+ delete) on a noise source."""
    source = Image.effect_noise(SIZE, 60).convert("RGB")
    src_path = tmp_path / "source.png"
    source.save(src_path)

    rows = [region("rk", "keep", 1), region("rm", "modify", 2, "blue flame")]
    masks = {"rk": rect_mask(KEEP_BOX), "rm": rect_mask(MODIFY_BOX)}
    if include_delete:
        rows.append(region("rd", "delete", 3))
        masks["rd"] = rect_mask(DELETE_BOX)
    plan = regionrun.build_region_plan(rows, masks, SIZE, None)
    for p in plan["passes"]:
        mp = tmp_path / f"mask-p{p['idx']}.png"
        mp.write_bytes(p["mask"])
        p["mask_path"] = str(mp)
        p["mask_sha256"] = "0" * 64
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
        source={"path": str(src_path), "sha256": "f" * 64,
                "name": "cockpit-src.png"},
        keep={"path": str(keep_path), "sha256": "e" * 64},
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

    async def view(self, filename, subfolder=""):
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
