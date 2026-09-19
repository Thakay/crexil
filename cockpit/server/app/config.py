"""Central config. Everything env-driven with dev defaults derived from the
repo layout; the Docker image overrides via environment (all paths are
WSL/ext4-native except PROMOTE_DIR, the one promotion boundary)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# repo root in dev checkout: app/ -> server/ -> cockpit/ -> repo.
# In the container the tree is shallower, so compute lazily and only
# when COCKPIT_REPO_ROOT is not set.
def _dev_repo_root() -> Path:
    parents = Path(__file__).resolve().parents
    return parents[3] if len(parents) > 3 else Path("/repo")


REPO_ROOT = Path(os.environ.get("COCKPIT_REPO_ROOT") or _dev_repo_root())

#.env at the repo root is the single local-secrets file
load_dotenv(REPO_ROOT / ".env", override=False)

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
# The 3D engine runner: the second native GPU service, reached
# exactly like ComfyUI (the container has no GPU and no engine venvs, so
# the "local subprocess" is spawned host-side and asked over HTTP).
# Down is a BENCH, never a crash: the lane says so and the image lane runs on.
THREED_URL = os.environ.get("THREED_URL", "http://127.0.0.1:8189").rstrip("/")
# A cold mesh take is 155-216 s (measured); the poll is the runner's own clock,
# so this is only how often the cockpit asks — not a schedule it imposes.
THREED_POLL_S = float(os.environ.get("COCKPIT_THREED_POLL_S", "2"))
PRESETS_DIR = Path(os.environ.get("COCKPIT_PRESETS_DIR", REPO_ROOT / "presets"))
WORKFLOWS_DIR = Path(os.environ.get("COCKPIT_WORKFLOWS_DIR", REPO_ROOT / "workflows"))
# Authored tables the web reads as-is: configs/dials.json (the six-dial
# normalization) and configs/engines.json (the capability matrix).
# Mounted read-only in compose like presets/.
CONFIGS_DIR = Path(os.environ.get("COCKPIT_CONFIGS_DIR", REPO_ROOT / "configs"))
STAGING_DIR = Path(os.environ.get("COCKPIT_STAGING_DIR", REPO_ROOT / ".staging" / "cockpit"))
# The one store (db.py, schema v4+). The file keeps its `-v4` name as the
# schema moves on. db.py upgrades schema 4 and later in place; a file older
# than schema 4 is refused (SchemaVersionError), never migrated or overwritten.
DB_PATH = Path(os.environ.get("COCKPIT_DB",
                              REPO_ROOT / "cockpit" / "server" / "cockpit-v4.sqlite"))
# The promotion boundary: the one Windows-mounted path in the system. Resolved
# absolute because the promote face PRINTS it: a relative value
# would name a different folder depending on where the process was started,
# and the plinth refuses that ambiguity from a re-point for the same reason.
PROMOTE_DIR = Path(
    os.path.expanduser(os.environ.get("PROMOTE_DIR", "/mnt/d/PROMOTED"))
).resolve()
# where a promotion is ALLOWED to go, colon-separated like PATH. The
# cockpit mounts the promotion drive rather than the single folder, so the
# artist can steer within it (the re-point) — and this is the fence that
# keeps a typo on the drive. Defaulting to PROMOTE_DIR itself means an
# unconfigured deployment can only ever promote into its own tree and that
# tree's subfolders: safe by omission, never open by omission.
PROMOTE_ROOTS = [
    Path(os.path.expanduser(p)).resolve()
    for p in os.environ.get("PROMOTE_ROOTS", "").split(":") if p
] or [PROMOTE_DIR]
STATIC_DIR = Path(os.environ.get("COCKPIT_STATIC_DIR",
                                 Path(__file__).resolve().parents[1] / "static"))
MODELS_ROOT = Path(os.path.expanduser(os.environ.get("MODELS_ROOT", "~/models")))

# --- external providers (OpenRouter, single key) ---
# Model ids + price estimates live in providers.json (never in code); the one
# secret is the OpenRouter key. OPENROUTER_URL is overridable so the
# the e2e can point the adapter at a mock.
PROVIDERS_FILE = Path(os.environ.get("COCKPIT_PROVIDERS_FILE",
                                     REPO_ROOT / "providers.json"))
OPENROUTER_URL = os.environ.get(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()

# Build stamp: baked at image build (scripts/deploy.sh passes git rev +
# UTC time as build args). "dev" = uncontainerized run; "unknown" = image
# built without args (plain `docker compose up -d --build`) — both visible
# in /api/health and the UI footer so deployment drift self-announces.
BUILD_ID = os.environ.get("COCKPIT_BUILD_ID", "dev")
BUILD_TIME = os.environ.get("COCKPIT_BUILD_TIME", "")

# --- scheduler (two lanes: local serial, external concurrent) ---
# Take timeouts: empty string = unbounded. Local stays unbounded by default —
# first-run model warm-up can legitimately exceed any sane cap (A6 has no
# default limit); external relies on the adapter's 300 s read timeout unless set.
EXTERNAL_CONCURRENCY = int(os.environ.get("COCKPIT_EXTERNAL_CONCURRENCY", "4"))
_lt = os.environ.get("COCKPIT_LOCAL_TAKE_TIMEOUT_S", "")
LOCAL_TAKE_TIMEOUT_S = float(_lt) if _lt else None
_et = os.environ.get("COCKPIT_EXTERNAL_TAKE_TIMEOUT_S", "")
EXTERNAL_TAKE_TIMEOUT_S = float(_et) if _et else None
RETRY_BACKOFF_S = float(os.environ.get("COCKPIT_RETRY_BACKOFF_S", "2"))
# B4: the gap between the three free bill re-asks after a charged-
# but-blank response. The e2e harness
# shortens it so the check is watchable within a spec's patience.
BILL_CHECK_GAP_S = float(os.environ.get("COCKPIT_BILL_CHECK_GAP_S", "20"))
# B2: a rate-limited take retries on the provider's clock, which is its
# Retry-After when it names one (capped, so a hostile header can't park a
# slot for an hour), else this default, up to RATE_LIMIT_RETRIES times.
RATE_LIMIT_RETRIES = int(os.environ.get("COCKPIT_RATE_LIMIT_RETRIES", "4"))
RATE_LIMIT_BACKOFF_S = float(os.environ.get("COCKPIT_RATE_LIMIT_BACKOFF_S", "10"))
RATE_LIMIT_MAX_WAIT_S = float(os.environ.get("COCKPIT_RATE_LIMIT_MAX_WAIT_S", "120"))
# C2: an OOM while something else holds at least this much of the
# card is "the card is held", not "the frame is too big" — the local lane
# pauses and re-checks the card every GPU_RECHECK_S until the hold clears.
# The floor sits above the Windows desktop's own slice (~1–1.5 GB with a
# display attached) so the everyday state never reads as a hold.
GPU_FOREIGN_HOLD_BYTES = int(float(os.environ.get(
    "COCKPIT_GPU_FOREIGN_HOLD_GB", "2.5")) * (1 << 30))
GPU_RECHECK_S = float(os.environ.get("COCKPIT_GPU_RECHECK_S", "15"))
# E6: free disk at Run. A run whose takes the staging volume can't
# hold is refused in words (the composer disarms first). Bytes per pixel
# is read from this card's own landed takes; this is the fallback before
# any have landed (a 1024² PNG from a diffusion model runs ~1.5 MB).
DISK_FALLBACK_BYTES_PER_PX = float(os.environ.get(
    "COCKPIT_DISK_FALLBACK_BYTES_PER_PX", "1.5"))
DISK_MARGIN_BYTES = int(os.environ.get("COCKPIT_DISK_MARGIN_BYTES",
                                       str(64 << 20)))
# Law-test harness: scripted executors + control routes. Set only by
# playwright.config.ts's webServer env — never in compose/deploy.
HARNESS_EXECUTORS = os.environ.get("COCKPIT_HARNESS_EXECUTORS", "") == "1"
COMFY_POLL_S = float(os.environ.get("COCKPIT_COMFY_POLL_S", "1.0"))
HISTORY_CONFIRM_TRIES = int(os.environ.get("COCKPIT_HISTORY_CONFIRM_TRIES", "20"))
HISTORY_CONFIRM_INTERVAL_S = float(
    os.environ.get("COCKPIT_HISTORY_CONFIRM_INTERVAL_S", "0.5"))
