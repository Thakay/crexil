"""D5, class D's trust-critical case: the promote target moved,
renamed or unmounted. Promote is the one irreversible act and the one
place the app writes into the artist's real tree, so the destination is
validated immediately before writing (never guessed, never created), and
a promotion is all-or-nothing — no manifest ever sits there without its
image. The v4 verb wires a route and a face to this seam."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from app import config, promote
from app.promote import PromoteTargetError, promote_candidate, validate_target


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STAGING_DIR", tmp_path / "staging")
    # every promote target is fenced to the configured roots. These
    # cases build their tree under tmp_path, so tmp_path IS the drive here
    # — stated rather than assumed, because the default fence
    # (PROMOTE_DIR itself) would refuse them, correctly.
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [tmp_path])
    img = tmp_path / "take.png"
    img.write_bytes(b"\x89PNG fake bytes")
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"mask")
    return img, mask


def test_a_good_target_takes_the_whole_promotion_and_leaves_no_temp(
        tmp_path, staged):
    img, mask = staged
    root = tmp_path / "PROMOTED"
    root.mkdir()
    png, man, staged_man = promote_candidate(
        img, {"asset": "a1"}, "raw-sdxl", "a1",
        [(mask, "a1.inputs/mask.png")], root=root)
    assert png == root / "raw-sdxl" / "a1.png"
    assert png.read_bytes() == img.read_bytes()
    assert json.loads(man.read_text()) == {"asset": "a1"}
    assert (root / "raw-sdxl" / "a1.inputs" / "mask.png").read_bytes() == b"mask"
    assert staged_man == config.STAGING_DIR / "manifests" / "a1.json"
    assert staged_man.exists()
    assert not [p for p in (root / "raw-sdxl").iterdir()
                if p.name.startswith(".promote-")]


def test_a_missing_target_is_refused_in_words_and_never_created(
        tmp_path, staged):
    img, _ = staged
    root = tmp_path / "PROMOTED"           # renamed away
    with pytest.raises(PromoteTargetError) as e:
        promote_candidate(img, {"asset": "a2"}, "raw-sdxl", "a2", root=root)
    assert "isn't there" in str(e.value)
    assert "point the cockpit at another folder" in str(e.value)
    assert "the take is untouched" in str(e.value)
    assert not root.exists()                  # no mkdir -p look-alike
    # the record side is fine: the staged manifest was written first
    assert (config.STAGING_DIR / "manifests" / "a2.json").exists()


def test_a_target_that_is_a_file_is_refused(tmp_path, staged):
    img, _ = staged
    root = tmp_path / "PROMOTED"
    root.write_text("not a folder")
    with pytest.raises(PromoteTargetError, match="is a file, not a folder"):
        promote_candidate(img, {}, "raw-sdxl", "a3", root=root)
    assert root.read_text() == "not a folder"


def test_an_unmounted_windows_drive_is_named_before_anything_else(
        tmp_path, staged, monkeypatch):
    """D5 with the drive gone (not mounted). The mount check runs before the
    existence check, so a missing /mnt/d/PROMOTED says "not mounted",
    not "isn't there". (The fence sits ahead of both; inside it, this
    order stands. The target here is on its own drive.)"""
    img, _ = staged
    monkeypatch.setattr(os.path, "ismount", lambda p: False)
    root = Path("/mnt/zz/PROMOTED")
    with pytest.raises(PromoteTargetError) as e:
        promote_candidate(img, {}, "raw-sdxl", "a4", root=root,
                          roots=[Path("/mnt/zz")])
    assert "/mnt/zz" in str(e.value) and "isn't mounted" in str(e.value)
    assert not root.exists()


def test_validate_target_never_writes_and_reads_config_by_default(
        tmp_path, monkeypatch):
    root = tmp_path / "P"
    root.mkdir()
    monkeypatch.setattr(config, "PROMOTE_DIR", root)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [root])
    assert validate_target() == root
    assert list(root.iterdir()) == []
    monkeypatch.setattr(config, "PROMOTE_DIR", tmp_path / "gone")
    with pytest.raises(PromoteTargetError):
        validate_target()


def test_the_fence_defaults_to_the_promote_dir_itself_so_omission_is_safe():
    """The default is `PROMOTE_ROOTS or [PROMOTE_DIR]`, which matters
    more than it looks: a deployment that forgets the variable can only
    promote into its own tree and that tree's subfolders. The failure mode
    of omission is a fence that is too TIGHT, never one that is open."""
    import importlib
    # unset, and set-but-empty: both must land on the tight default
    for env in ({}, {"PROMOTE_ROOTS": ""}):
        with mock.patch.dict(
                os.environ, {"PROMOTE_DIR": "/mnt/d/PROMOTED", **env},
                clear=True):
            fresh = importlib.reload(config)
            assert [Path("/mnt/d/PROMOTED")] == fresh.PROMOTE_ROOTS
            assert not promote.inside_roots(Path("/tmp"),
                                            fresh.PROMOTE_ROOTS)
            assert not promote.inside_roots(Path("/mnt/d"),
                                            fresh.PROMOTE_ROOTS)
            assert promote.inside_roots(Path("/mnt/d/PROMOTED/hero"),
                                        fresh.PROMOTE_ROOTS)
    importlib.reload(config)          # leave the module as we found it


def test_the_fence_reads_a_colon_separated_list_like_PATH():
    import importlib
    with mock.patch.dict(os.environ, {"PROMOTE_ROOTS": "/mnt/d:/mnt/e"},
                         clear=False):
        fresh = importlib.reload(config)
        assert [Path("/mnt/d"), Path("/mnt/e")] == fresh.PROMOTE_ROOTS
    importlib.reload(config)


def test_a_target_that_breaks_partway_leaves_nothing_behind(
        tmp_path, staged, monkeypatch):
    """No half-write: the manifest moves first and the image last; if the
    image's move fails, the manifest is taken back and the temp dir goes."""
    img, _ = staged
    root = tmp_path / "PROMOTED"
    root.mkdir()
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(Path(dst).name)
        if Path(dst).name == "a5.png":
            raise OSError(5, "Input/output error")   # drive yanked
        return real_replace(src, dst)
    monkeypatch.setattr(promote.os, "replace", flaky)
    with pytest.raises(PromoteTargetError) as e:
        promote_candidate(img, {"asset": "a5"}, "raw-sdxl", "a5", root=root)
    assert "nothing of a5 was left there" in str(e.value)
    assert calls[-2:] == ["a5.json", "a5.png"]       # manifest first, image last
    preset_dir = root / "raw-sdxl"
    assert not (preset_dir / "a5.json").exists()      # rolled back
    assert not (preset_dir / "a5.png").exists()
    assert not [p for p in preset_dir.iterdir()
                if p.name.startswith(".promote-")]


def test_the_error_is_an_oserror_for_oserror_minded_callers():
    """The class keeps the OSError shape, so an OSError-minded caller
    still gets the D5 words."""
    e = PromoteTargetError("x", remedy="y", target=Path("/z"))
    assert isinstance(e, OSError)
    assert str(e) == "x — y"


# --- which disk, read from the mount table ---

def test_where_it_lives_makes_only_claims_the_kernel_can_back(monkeypatch):
    """A container can bind-mount /mnt/d/PROMOTED at /promote, and a
    `/mnt/<drive>` prefix test would call that
    the container's own disk and print the OPPOSITE of the truth on the
    artist's screen — so the positive claim comes from the mount table."""
    mounts = {"/mnt/d", "/promote", "/"}
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) in mounts)

    # the dev shape: the Windows drive, named
    assert promote.where_it_lives(Path("/mnt/d/PROMOTED")) \
        == "on the Windows drive mounted at /mnt/d"
    # the deployed shape: a bind mount, said as one — no guess at which
    # host disk sits behind it
    assert promote.where_it_lives(Path("/promote")) \
        == "on the volume mounted at /promote"
    assert promote.where_it_lives(Path("/promote/hero-sword")) \
        == "on the volume mounted at /promote"
    # nothing mounted anywhere along the path: the one place the cockpit
    # says "my own disk", and it says what that means
    line = promote.where_it_lives(Path("/home/artist/out"))
    assert "the cockpit's own disk" in line
    assert "nothing promoted here leaves this machine" in line


def test_an_unmounted_windows_drive_is_still_caught_by_the_path_test():
    """validate_target's /mnt check stays a one-way test on the SPELLING —
    it only ever refuses, so a container's differently-spelled bind mount
    can't be wrongly rejected by it."""
    assert promote._mount_root(Path("/mnt/d/PROMOTED")) == Path("/mnt/d")
    assert promote._mount_root(Path("/promote")) is None


# --- the fence, and the artist's spelling ---

def test_a_target_off_the_drive_is_refused_before_the_filesystem_is_touched(
        tmp_path):
    """The hole this closes (probed live, 2026-08-27): /tmp and /data EXIST
    in the container and are writable, so every other check passes them,
    and a promotion would land where a rebuild or `down -v` erases it.
    Being off the drive is a fact about the path, so it is answered first,
    without touching the filesystem at all."""
    drive = tmp_path / "d"
    (drive / "PROMOTED").mkdir(parents=True)
    outside = tmp_path / "container-tmp"
    outside.mkdir()                                # exists AND writable
    with pytest.raises(PromoteTargetError) as e:
        validate_target(outside, [drive])
    assert f"{outside} is outside" in e.value.message
    assert "only promote onto the drive it was given" in e.value.message
    assert "pick a folder on that drive" in e.value.remedy
    # and the legal ones still pass
    assert validate_target(drive / "PROMOTED", [drive]) \
        == drive / "PROMOTED"
    assert validate_target(drive, [drive]) == drive


def test_the_fence_survives_dot_dot_and_symlinks_out_of_the_drive(tmp_path):
    """A path-prefix test on the RAW string would let `.../d/../elsewhere`
    through. The check resolves first, so both escapes land outside."""
    drive = tmp_path / "d"
    drive.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert not promote.inside_roots(drive / ".." / "elsewhere", [drive])
    link = drive / "escape"
    link.symlink_to(elsewhere, target_is_directory=True)
    assert not promote.inside_roots(link, [drive])
    # a symlink that stays on the drive is fine
    home = drive / "PROMOTED"
    home.mkdir()
    inner = drive / "shortcut"
    inner.symlink_to(home, target_is_directory=True)
    assert promote.inside_roots(inner, [drive])


def test_the_refusal_names_the_fence_in_the_artists_spelling(tmp_path):
    """A fence you can't see is just a locked door — the refusal says
    where you MAY go, and says it as D:\\, not /mnt/d."""
    with pytest.raises(PromoteTargetError) as e:
        validate_target(tmp_path / "nope", [Path("/mnt/d")])
    assert "outside D:\\" in e.value.message
    # off a Windows drive there is only one spelling, and it is used
    with pytest.raises(PromoteTargetError) as e:
        validate_target(Path("/srv/x"), [tmp_path / "roots"])
    assert f"outside {tmp_path / 'roots'}" in e.value.message


def test_several_roots_are_all_legal_and_all_named(tmp_path):
    a, b = tmp_path / "d", tmp_path / "e"
    a.mkdir(), b.mkdir()
    assert promote.inside_roots(a / "x", [a, b])
    assert promote.inside_roots(b / "y", [a, b])
    assert not promote.inside_roots(tmp_path / "f", [a, b])
    assert promote._describe_roots([a, b]) == f"{a} or {b}"


def test_promote_candidate_is_fenced_too_not_just_the_re_point(tmp_path,
                                                               staged):
    """The fence lives in validate_target, which every write goes through —
    so a root smuggled past the route (a stale meta row, a hand-edited
    .env) is still refused at the moment of writing, with nothing written."""
    img, _ = staged
    outside = tmp_path / "outside"
    outside.mkdir()
    monkey_roots = [tmp_path / "drive"]
    (tmp_path / "drive").mkdir()
    with pytest.raises(PromoteTargetError, match="is outside"):
        promote.promote_candidate(img, {}, "ws", "a9", root=outside,
                                  roots=monkey_roots)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("typed,expected", [
    ("D:\\PROMOTED", "/mnt/d/PROMOTED"),
    ("d:/PROMOTED", "/mnt/d/PROMOTED"),
    ("D:\\", "/mnt/d"),
    ("D:\\A\\B\\C", "/mnt/d/A/B/C"),
    ("  D:\\PROMOTED  ", "/mnt/d/PROMOTED"),
    ("/mnt/d/PROMOTED", "/mnt/d/PROMOTED"),   # already ours
    ("PROMOTED", "PROMOTED"),                  # neither: left alone
    ("", ""),
])
def test_the_artists_spelling_is_translated_at_the_edge(typed, expected):
    assert promote.from_windows(typed) == expected


@pytest.mark.parametrize("path,expected", [
    ("/mnt/d/PROMOTED", "D:\\PROMOTED"),
    ("/mnt/d/A/B", "D:\\A\\B"),
    ("/mnt/d", "D:\\"),
    ("/tmp", None),                    # not a drive: no second spelling
    ("/mnt/wsl/x", None),              # /mnt, but not a drive letter
    ("/mnt", None),
])
def test_the_cockpits_path_is_printed_back_in_the_artists_spelling(
        path, expected):
    assert promote.to_windows(Path(path)) == expected


def test_target_state_carries_both_spellings_and_the_fence(tmp_path,
                                                           monkeypatch):
    drive = tmp_path / "d"
    home = drive / "PROMOTED"
    home.mkdir(parents=True)
    monkeypatch.setattr(config, "PROMOTE_ROOTS", [drive])
    st = promote.target_state(home)
    assert st["ok"] is True
    assert st["path"] == str(home)
    assert st["windows_path"] is None          # tmp_path isn't a drive
    assert st["roots"] == [str(drive)]
    assert st["roots_said"] == str(drive)


# --- the promoted file keeps the artefact's own extension ---


def _root(tmp_path):
    root = tmp_path / "PROMOTED"
    root.mkdir()
    return root


def test_a_mesh_lands_as_a_glb_not_as_a_png(tmp_path, staged):
    """The promoted `<asset_id>.*` keeps the artefact's own suffix.
    A GLB promoted under a hardcoded `.png` would be a glTF file wearing an
    image's name — and every consumer of D:, funnel.py included, reads the
    suffix rather than the bytes."""
    img, _mask = staged
    root = _root(tmp_path)
    glb = tmp_path / "trellis2.glb"
    glb.write_bytes(b"glTF-ish bytes")
    art, man, _ = promote_candidate(glb, {"asset_id": "m1"}, "_3d/props",
                                    "m1", root=root)
    assert art == root / "_3d" / "props" / "m1.glb"
    assert art.read_bytes() == b"glTF-ish bytes"
    assert man == root / "_3d" / "props" / "m1.json"
    # and the image lane is untouched — same name it always had
    art, _man, _ = promote_candidate(img, {"asset_id": "i1"}, "raw-sdxl",
                                     "i1", root=root)
    assert art == root / "raw-sdxl" / "i1.png"


def test_an_artefact_with_no_suffix_still_lands_as_a_png(tmp_path, staged):
    """The image lane's own default, kept: nothing in the tree is ever
    named `<asset_id>` with no extension at all."""
    root = _root(tmp_path)
    bare = tmp_path / "nameless"
    bare.write_bytes(b"pixels")
    art, _man, _ = promote_candidate(bare, {}, "raw-sdxl", "b1", root=root)
    assert art == root / "raw-sdxl" / "b1.png"


def test_the_turntable_rides_beside_the_mesh_not_under_inputs(tmp_path,
                                                              staged):
    """Promote carries the orbit out as an extra file. It sits BESIDE the
    GLB rather than under `<asset_id>.inputs/`, because it is not an input
    the mesh was made from — it is the only way to see what the mesh is
    without a 3D viewer."""
    root = _root(tmp_path)
    glb = tmp_path / "trellis2.glb"
    glb.write_bytes(b"mesh")
    orbit = tmp_path / "trellis2.turntable.webp"
    orbit.write_bytes(b"orbit")
    art, _man, _ = promote_candidate(
        glb, {}, "_3d/props", "m2",
        [(orbit, "m2.turntable.webp")], root=root)
    assert art == root / "_3d" / "props" / "m2.glb"
    assert (root / "_3d" / "props" / "m2.turntable.webp").read_bytes() \
        == b"orbit"


def test_missing_planned_related_file_refuses_the_whole_delivery(tmp_path, staged):
    image, mask = staged
    root = tmp_path / 'delivery'
    root.mkdir()
    mask.unlink()
    with pytest.raises(PromoteTargetError):
        promote_candidate(image, {'asset': 'missing-related'}, 'project', 'missing-related',
                          [(mask, 'missing-related.outputs/mask.png')], root=root)
    assert not list((root / 'project').rglob('*.png'))
    assert not list((root / 'project').rglob('*.json'))


@pytest.mark.parametrize('corrupt_primary', [False, True])
def test_delivery_refuses_bytes_that_differ_from_collected_receipts(tmp_path, staged, corrupt_primary):
    import hashlib
    image, mask = staged
    root = tmp_path / 'checked-delivery'
    root.mkdir()
    manifest = {'material': {'sha256': hashlib.sha256(image.read_bytes()).hexdigest()},
                'execution': {'outputs': [{'file': 'checked.outputs/mask.png',
                                          'sha256': hashlib.sha256(mask.read_bytes()).hexdigest()}]}}
    (image if corrupt_primary else mask).write_bytes(b'changed after collection')
    with pytest.raises(promote.PromoteMaterialError, match='Nothing was delivered'):
        promote_candidate(image, manifest, 'project', 'checked', [(mask, 'checked.outputs/mask.png')], root=root)
    assert not list((root / 'project').rglob('*.png'))
    assert not list((root / 'project').rglob('*.json'))
