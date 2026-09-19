"""Keep-alive serve loop for the engine runners.

One shared loop instead of three copies: an engine runner that can hold its
pipeline resident calls `serve()` after loading, and from then on each take
arrives as one JSON line over a unix socket instead of a fresh process. The
caller of record is `tools/3d/runner.py`, which owns the child's lifecycle
(spawn, evict by SIGTERM); the per-take client is `funnel.py`, whose
validate/turntable tail runs unchanged either way.

stdlib only — this module is imported inside every engine venv.

Protocol (one request per connection, newline-delimited JSON):

    -> {"for": "<identity>", "args": ["--image", ...]}
    <- {"accepted": true}                    the take is about to run
    <- {"ok": true, "exit_code": 0, ...}     ... and how it ended

A request whose `for` does not match this child's identity is refused in
words BEFORE `accepted` — the client falls back to a cold spawn, so a
mismatched socket can never run the wrong engine. `accepted` is the line
that splits the two failure worlds: no `accepted` means the take never
started (safe to run cold); EOF after `accepted` means the resident child
died mid-take (fail the take honestly, never double-run).

The socket is bound only after the pipeline has loaded, so "the socket
accepts a connection" IS the ready signal the runner waits on. SIGTERM is
the eviction verb and is left at its default: the child dies where it
stands, and the measured cost of that is ≤6 s to a clean card.
"""
from __future__ import annotations

import json
import os
import socket
import time
import traceback
from contextlib import suppress
from pathlib import Path


def rss_mb() -> int | None:
    """This process's host RSS: the budget residency actually spends (WSL
    caps host RAM)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _reply(f, payload: dict) -> None:
    with suppress(OSError):
        f.write((json.dumps(payload) + "\n").encode())
        f.flush()


def serve(identity: str, socket_path: str, take, ready_info: dict | None = None) -> int:
    """Run takes forever. `take(argv)` runs one and raises on failure; the
    caller has already loaded whatever it keeps resident. Requests are served
    one at a time — the runner serialises jobs anyway, so concurrency here
    would only be a second copy of the one-heavy-job law."""
    sp = Path(socket_path)
    sp.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        sp.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sp))
    os.chmod(sp, 0o600)
    srv.listen(1)
    print(f"SERVE READY {identity} "
          + json.dumps((ready_info or {}) | {"rss_mb": rss_mb()}), flush=True)
    while True:
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        try:
            line = f.readline()
            if not line:                    # the runner's readiness probe
                continue
            try:
                req = json.loads(line)
            except ValueError:
                _reply(f, {"ok": False, "error": "the request was not JSON"})
                continue
            if not isinstance(req, dict) or req.get("for") != identity:
                _reply(f, {"ok": False,
                           "refused": f"this resident child is {identity}, "
                                      f"not {req.get('for')!r} — run cold"})
                continue
            _reply(f, {"accepted": True})
            t0 = time.time()
            try:
                take(req.get("args") or [])
                _reply(f, {"ok": True, "exit_code": 0,
                           "wall_s": round(time.time() - t0, 1),
                           "rss_mb": rss_mb()})
            except SystemExit as e:         # argparse refusals land here
                code = e.code if isinstance(e.code, int) else 1
                _reply(f, {"ok": False, "exit_code": code or 1,
                           "error": "the take's arguments were refused — "
                                    "the resident child's log has the words"})
            except Exception as e:
                _reply(f, {"ok": False, "exit_code": 1,
                           "error": f"{type(e).__name__}: {e}",
                           "trace_tail": traceback.format_exc()[-2000:],
                           "rss_mb": rss_mb()})
        finally:
            with suppress(OSError):
                f.close()
            with suppress(OSError):
                conn.close()
