"""Promotion from inside the cockpit: the copy-out to the artist's tree.

Same contract as scripts/promote.py (the standalone CLI): the promoted tree
is the ONLY Windows-mounted path in the system; never symlinks. The
manifest is also kept in ext4 staging so cockpit state survives without the
promote folder.

Promote is the one irreversible act and the one place the app touches the
artist's real production tree, and a folder rename is routine there. So:

- `validate_target` runs IMMEDIATELY before writing, never after, and never
  guesses: the root must already exist as a directory (it is never created
  — a `mkdir -p` on a missing `/mnt/d/…` would silently build a look-alike
  tree on the WSL disk), its mount must be mounted, and it must be writable.
  A broken target is `PromoteTargetError` in plain words with the re-point
  remedy; nothing has been written.
- No half-write: every file of the promotion is staged into one temp dir
  inside the destination and moved into place last, manifest first and the
  artefact as the final move; a move that fails rolls the earlier ones back.
  The record (the node, the staged manifest) is untouched either way.
- `target_state` says what the target IS right now in the artist's words —
  the face reads it before the hold, never as a promise; the write still
  validates immediately before it moves.
- The destination sub-path is the WORKSPACE's slug (`_3d/<slug>` for
  meshes), a relative path either way.
- `browse` (bottom of the file) lists one level of the drive as handles,
  fenced by the same `inside_roots` before the folder is touched, and
  `validate_target` badges each entry — so the picker never composes a
  path, only hands one of these back.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
from pathlib import Path

from . import config


class PromoteMaterialError(ValueError):
    """Collected bytes no longer match their receipt; delivery is refused."""


def _verify_collected_copy(path: Path, relative: str, manifest: dict, primary: bool = False) -> None:
    expected = (manifest.get('material') or {}).get('sha256') if primary else None
    for item in [*((manifest.get('execution') or {}).get('outputs') or []), *(manifest.get('input_images') or []),
                 *((manifest.get('material') or {}).get('related_files') or [])]:
        if item.get('file') == relative and item.get('sha256'):
            expected = item['sha256']
    for item in manifest.get('regions') or []:
        if item.get('mask_file') == relative:
            expected = item.get('mask_sha256')
    if expected:
        with path.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != expected:
            raise PromoteMaterialError('A file changed since it was collected. Restore its original bytes or collect this attempt again before delivery. Nothing was delivered.')


class PromoteTargetError(OSError):
    """The destination can't take the promotion — said in the artist's
    words, with what to do. Subclasses OSError so any OSError-minded caller
    (`except OSError`) still catches it; the promote route answers it as a
    409 with the two halves apart."""

    def __init__(self, message: str, *, remedy: str, target: Path):
        super().__init__(f"{message} — {remedy}")
        self.message = message
        self.remedy = remedy
        self.target = target


# D5's second half — what to do. Worded to hold wherever it is read: the
# plinth draws a re-point control right under it, a curl caller
# has only .env. Neither is named, because the sentence has to be true on
# both.
REPOINT = ("put the folder back, or point the cockpit at another folder, "
           "then promote again; the take is untouched")


DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def from_windows(text: str) -> str:
    r"""`D:\PROMOTED` -> `/mnt/d/PROMOTED`.

    The artist names folders the way Windows does (`D:\...`), and so does
    a typical .env. Translating at the edge costs
    one regex and removes the whole class of "give the folder's full path,
    starting from /" refusals aimed at a path that was perfectly clear.
    Anything that isn't a drive-letter spelling is returned untouched — a
    POSIX path is already the answer.
    """
    m = DRIVE_RE.match(text.strip())
    if m is None:
        return text.strip()
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    return str(Path("/mnt") / drive / rest) if rest else f"/mnt/{drive}"


def to_windows(path: Path) -> str | None:
    r"""`/mnt/d/PROMOTED` -> `D:\PROMOTED`, or None when the path
    isn't on a Windows drive. The face prints this BESIDE the real path,
    never instead of it — the cockpit's own spelling is what a refusal, a
    manifest and a log will say."""
    parts = path.parts
    if len(parts) < 3 or parts[0] != "/" or parts[1] != "mnt":
        return None
    drive = parts[2]
    if len(drive) != 1 or not drive.isalpha():
        return None
    tail = "\\".join(parts[3:])
    return f"{drive.upper()}:\\{tail}" if tail else f"{drive.upper()}:\\"


def _mount_root(path: Path) -> Path | None:
    """`/mnt/<drive>` for a Windows-mounted path, else None."""
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "/" and parts[1] == "mnt":
        return Path(parts[0], parts[1], parts[2])
    return None


def _describe_roots(roots: list[Path]) -> str:
    """The legal set in the artist's spelling, for a refusal that can be
    acted on — a fence you can't see is just a locked door."""
    return " or ".join(to_windows(r) or str(r) for r in roots)


def inside_roots(root: Path, roots: list[Path] | None = None) -> bool:
    """Is this target within the drive(s) the cockpit may promote into
   ? Path-based, and on RESOLVED paths, so `..` and a symlink out
    of the drive both land outside."""
    roots = roots if roots is not None else config.PROMOTE_ROOTS
    resolved = root.resolve()
    return any(resolved == r or r in resolved.parents for r in roots)


def validate_target(root: Path | None = None,
                    roots: list[Path] | None = None) -> Path:
    """The promote root, proven usable right now — or PromoteTargetError.
    Never creates anything.

    Order matters. The fence is checked FIRST, before the target is
    touched at all: `/tmp` and `/data` exist inside the container and are
    writable, so every later check would pass them and a promotion would
    land somewhere a rebuild erases. Being outside the drive is a fact
    about the path, so it needs no filesystem at all to answer.
    """
    root = Path(root if root is not None else config.PROMOTE_DIR)
    allowed = roots if roots is not None else config.PROMOTE_ROOTS
    if not inside_roots(root, allowed):
        raise PromoteTargetError(
            f"the promote target {root} is outside {_describe_roots(allowed)}"
            " — the cockpit can only promote onto the drive it was given",
            remedy="pick a folder on that drive and promote again; the take "
                   "is untouched", target=root)
    mount = _mount_root(root)
    if mount is not None and not os.path.ismount(mount):
        raise PromoteTargetError(
            f"the promote target {root} sits on {mount}, and that drive "
            "isn't mounted", remedy=REPOINT, target=root)
    if not root.exists():
        raise PromoteTargetError(
            f"the promote target {root} isn't there — moved, renamed or "
            "not yet created", remedy=REPOINT, target=root)
    if not root.is_dir():
        raise PromoteTargetError(
            f"the promote target {root} is a file, not a folder",
            remedy=REPOINT, target=root)
    if not os.access(root, os.W_OK):
        raise PromoteTargetError(
            f"the promote target {root} can't be written to",
            remedy="fix its permissions and promote again; the take is "
                   "untouched", target=root)
    return root


def _atomic_write_json(payload: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, dest)


def promote_candidate(artefact_path: Path, manifest: dict, dest_rel: str,
                      asset_id: str,
                      extra_files: list[tuple[Path, str]] = (),
                      *, root: Path | None = None,
                      roots: list[Path] | None = None
                      ) -> tuple[Path, Path, Path]:
    """Returns (promoted_artefact, promoted_manifest, staged_manifest).

    `dest_rel` is the destination folder RELATIVE to the promote root — the
    workspace slug (`_3d/<slug>` for meshes).

    **The promoted file keeps the artefact's own extension**. Promote
    writes `<asset_id>.*`, and the star is a PNG only when the payload
    is one: a mesh promoted under a hardcoded `.png` would be a
    glTF file wearing an image's name — the one thing a promoted tree must
    never contain, since every consumer of the promote folder (funnel.py
    included) reads
    the suffix, not the bytes. The source's suffix is the answer; a file
    with none still lands `.png`, which is what the image lane always did.

    extra_files are (source path, dest path relative to that folder)
    pairs: region masks + the source image go under
    `<asset_id>.inputs/` so the manifest is reproducible from the promote
    folder alone,
    and a mesh's turntable goes beside the GLB.

    Raises PromoteTargetError (D5) with nothing written to the promoted
    tree; the staged manifest in ext4 is written first and stays — it is
    cockpit state, not the artist's tree.
    """
    staged_manifest = config.STAGING_DIR / "manifests" / f"{asset_id}.json"
    _atomic_write_json(manifest, staged_manifest)

    # Immediately before writing, fence included — so a root
    # smuggled past the route (a stale setting, a hand-edited .env) is
    # still refused here, with nothing written to the artist's tree.
    root = validate_target(root, roots)
    target = root / dest_rel
    art_dest = target / f"{asset_id}{artefact_path.suffix or '.png'}"
    man_dest = target / f"{asset_id}.json"
    # stage everything inside the destination, then move — one temp dir,
    # so a crash or a vanished drive leaves at most a dot-prefixed folder
    # to sweep, never a manifest without its artefact
    tmpdir = target / f".promote-{asset_id}.tmp-{os.getpid()}"
    plan: list[tuple[Path, Path]] = []      # (staged, final), artefact LAST
    try:
        tmpdir.mkdir(parents=True, exist_ok=False)
        staged_man = tmpdir / man_dest.name
        shutil.copyfile(staged_manifest, staged_man)
        plan.append((staged_man, man_dest))
        for src, rel in extra_files:
            staged = tmpdir / "extra" / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, staged)
            _verify_collected_copy(staged, rel, manifest)
            plan.append((staged, target / rel))
        staged_art = tmpdir / art_dest.name
        shutil.copyfile(artefact_path, staged_art)
        _verify_collected_copy(staged_art, art_dest.name, manifest, primary=True)
        plan.append((staged_art, art_dest))
    except PromoteMaterialError:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise PromoteTargetError(
            f"couldn't write into the promote target {target}: {e}",
            remedy=REPOINT, target=target) from e

    moved: list[Path] = []
    try:
        for staged, final in plan:
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, final)
            moved.append(final)
    except OSError as e:
        for done in moved:                   # no half-write: roll back
            done.unlink(missing_ok=True)
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise PromoteTargetError(
            f"the promote target {target} broke partway — nothing of "
            f"{asset_id} was left there: {e}",
            remedy=REPOINT, target=target) from e
    shutil.rmtree(tmpdir, ignore_errors=True)
    return art_dest, man_dest, staged_manifest


def _nearest_mount(path: Path) -> Path | None:
    """The closest mount point at or above `path`, or None if the walk
    reaches `/` without finding one (i.e. it is on the root filesystem)."""
    for candidate in [path, *path.parents]:
        try:
            if os.path.ismount(candidate):
                return None if candidate == Path("/") else candidate
        except OSError:
            return None
    return None


def where_it_lives(root: Path) -> str:
    """Which disk this root is on, in the artist's words. The promotion
    boundary makes the promoted tree the ONE Windows-mounted path in the
    system, so a re-point onto the cockpit's own disk is legal but must
    never be silent: the face prints this line beside the path.

    Read from the MOUNT TABLE, not from the spelling of the path. A
    container can bind-mount the promotion drive at any path (for example
    `/promote`), and a `/mnt/<drive>` prefix test would call that the
    container's own disk and print the exact opposite of the truth. So the
    only positive claim made here is one the kernel can
    back — a mount point — and "the cockpit's own disk" is said only when
    nothing along the path is mounted at all.
    """
    mount = _nearest_mount(root)
    if mount is None:
        return ("on the cockpit's own disk — not a mounted asset tree, so "
                "nothing promoted here leaves this machine")
    if _mount_root(root) == mount:
        return f"on the Windows drive mounted at {mount}"
    return f"on the volume mounted at {mount}"


def target_state(root: Path | None = None) -> dict:
    """What the promote target IS right now, for the face — path, whether
    it would take a promotion, and on a broken one the same two halves
    `PromoteTargetError` carries (D5: what happened, what to do).

    This is a READING, never a promise: `promote_candidate` validates
    again immediately before it writes, because the drive can go
    between this call and the hold.
    """
    root = Path(root if root is not None else config.PROMOTE_DIR)
    roots = config.PROMOTE_ROOTS
    common = {"path": str(root), "windows_path": to_windows(root),
              "where": where_it_lives(root),
              "roots": [str(r) for r in roots],
              "roots_said": _describe_roots(roots)}
    try:
        validate_target(root)
    except PromoteTargetError as e:
        return {**common, "ok": False, "message": e.message,
                "remedy": e.remedy}
    return {**common, "ok": True, "message": None, "remedy": None}


# --- the browse surface ---

# Folders the picker never lists: the drive's own housekeeping
# (`$RECYCLE.BIN`, `System Volume Information`) and dot-prefixed ones. Not a
# fence — `inside_roots` is the fence — just noise the artist would never
# promote into, kept out of a list that is otherwise every folder there is.
_UNLISTED = {"System Volume Information"}

# The browse level above every root: the drive(s) themselves. The empty
# string on purpose — it is what `?path=` carries, so the face asks for the
# top the same way it asks for any folder, and `parent` names it the same way.
TOP = ""

# D5's second half, for a folder that can't be LOOKED AT (as opposed to
# promoted into): the remedy is to come back down from the drive.
START_OVER = "start from the drive and step in from there"


def _unlisted(name: str) -> bool:
    return name.startswith((".", "$")) or name in _UNLISTED


def _handle(path: Path, name: str, roots: list[Path]) -> dict:
    """One folder as the picker hands it back: the path the server minted,
    both spellings, and whether it could take a promotion right now — with
    the reason when not, so an unfit folder is shown de-emphasised rather
    than hidden (law 12: badge, never filter)."""
    try:
        validate_target(path, roots)
    except PromoteTargetError as e:
        return {"name": name, "path": str(path),
                "windows_path": to_windows(path), "ok": False,
                "why": e.message}
    return {"name": name, "path": str(path), "windows_path": to_windows(path),
            "ok": True, "why": None}


def browse(path: Path | None = None, roots: list[Path] | None = None) -> dict:
    """The folders under `path`, as handles. `None` is the top: the
    roots themselves, the fence enumerated.

    A handle, never a path: every string handed back here is one the server
    minted, and the same string a re-point takes verbatim — so the face
    that uses this never composes a path of its own, and everything it can
    choose was inside the fence when it was offered. Reads only; nothing is
    created or resolved on the artist's behalf.

    Raises PromoteTargetError for a folder that can't be listed at all —
    outside the fence (checked FIRST, before the folder is touched, for the
    reason `validate_target` gives), on an unmounted drive, missing, or a
    file. A folder that is there but couldn't take a promotion (read-only)
    still lists, with `here.ok` false and the reason beside it.
    """
    roots = roots if roots is not None else config.PROMOTE_ROOTS
    if path is None:
        return {"here": None, "parent": None,
                "folders": [_handle(r, to_windows(r) or str(r), roots)
                            for r in roots],
                "note": None}
    if not inside_roots(path, roots):
        raise PromoteTargetError(
            f"{path} is outside {_describe_roots(roots)} — the cockpit can "
            "only look around the drive it was given",
            remedy=START_OVER, target=path)
    mount = _mount_root(path)
    if mount is not None and not os.path.ismount(mount):
        raise PromoteTargetError(
            f"{path} sits on {mount}, and that drive isn't mounted",
            remedy=START_OVER, target=path)
    if not path.exists():
        raise PromoteTargetError(
            f"{path} isn't there — moved, renamed or not yet created",
            remedy=START_OVER, target=path)
    if not path.is_dir():
        raise PromoteTargetError(f"{path} is a file, not a folder",
                                 remedy=START_OVER, target=path)
    resolved = path.resolve()
    at_root = any(resolved == r.resolve() for r in roots)
    parent = ((TOP if len(roots) > 1 else None) if at_root
              else str(path.parent))
    note = None
    try:
        with os.scandir(path) as it:
            names = sorted((e.name for e in it
                            if e.is_dir() and not _unlisted(e.name)),
                           key=str.lower)
    except OSError as e:
        names = []
        note = ("this folder can't be read from inside the cockpit, so "
                f"what's in it can't be listed — {e.strerror or e}")
    return {"here": _handle(path, path.name or str(path), roots),
            "parent": parent,
            "folders": [_handle(path / n, n, roots) for n in names],
            "note": note}
