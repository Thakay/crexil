"""Unhandled-failure capture into the event log. Every failure is recorded
permanently, and this is the net for every failure path no kind names:
each of them still produces a loggable record.

The scheduler words the failures it understands (`plain_cause`, one line
per kind) and the ledger already keeps each of them as `slot.failed` with
cause · kind · attempt · engine · cost. Everything else (a take that
crashes the cockpit's OWN code, a lane loop, a background task, a route,
a task nobody awaited) would otherwise reach only the process log, which
the artist cannot open. This module routes all of it into the same ledger as
`cockpit.failure` rows: the site, the error, a bounded traceback, and the
best-effort ids of whatever was in hand (the events table is deliberately
un-FK'd for exactly this — db.py).

Three shapes to keep:
- `record` never raises and never blocks the caller on its own failure —
  a capture that could crash its host would be the bug it exists to log.
  The process log always gets the traceback first (uvicorn / journal).
- Repeats fold. The same site raising the same error inside
  REPEAT_WINDOW_S writes one row, not one per tick (a probe failing every
  15 s under a benched lane would otherwise fill the ledger with one
  cause — law 1's "one cause, one message" for the record). The next row
  after the window carries how many were folded.
- Sync entry points (`watch`, the asyncio loop handler) hand the write
  to the running loop; when the loop is going away or the store is closed
  the log line is all there is, and that is stated in it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any

from .db import Database

log = logging.getLogger("cockpit.capture")

FAILURE_EVENT = "cockpit.failure"
REPEAT_WINDOW_S = 60.0       # identical site+error inside this: folded
MESSAGE_CHARS = 800          # the exception's own text, bounded
TRACEBACK_CHARS = 4000       # the tail of the formatted traceback


def describe(exc: BaseException) -> dict[str, str]:
    """The record's view of an exception: type, message and the tail of
    its traceback (chained causes included), each bounded — a stack of
    ComfyUI node messages or a pydantic dump can run to pages."""
    message = str(exc)[:MESSAGE_CHARS]
    typ = type(exc).__name__
    try:
        tb = "".join(traceback.format_exception(exc))
    except Exception:            # a hostile __str__ / broken frame
        tb = ""
    if len(tb) > TRACEBACK_CHARS:
        tb = "…" + tb[-TRACEBACK_CHARS:]
    return {"error": f"{typ}: {message}" if message else typ,
            "exc_type": typ, "message": message, "traceback": tb}


class Capture:
    def __init__(self, db: Database) -> None:
        self.db = db
        # (site, exc_type, message) -> [last written mono, folded since, seq]
        self._recent: dict[tuple[str, str, str], list] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_handler: Any = None
        self._pending: set[asyncio.Task] = set()

    # --- the one write ---

    async def record(self, site: str, exc: BaseException, *,
                     workspace_id: str | None = None,
                     turn_id: str | None = None,
                     slot_id: str | None = None,
                     node_id: str | None = None,
                     engine: str | None = None,
                     attempt: int | None = None,
                     charged_usd: float | None = None,
                     note: str | None = None) -> dict | None:
        """Append one `cockpit.failure` row. Returns the row that stands
        for this failure — the fresh one, or the earlier one it folded
        into — or None when the store couldn't take it (closed, or the
        write itself failed: then the process log is the record and says
        so). Never raises."""
        desc = describe(exc)
        log.error("unhandled failure at %s: %s", site, desc["error"],
                  exc_info=exc)
        key = (site, desc["exc_type"], desc["message"])
        now = time.monotonic()
        seen = self._recent.get(key)
        if seen and now - seen[0] < REPEAT_WINDOW_S:
            seen[1] += 1
            return {"seq": seen[2], "folded": True}
        folded = seen[1] if seen else 0
        if not self.db.opened:
            log.error("store closed — the failure at %s is recorded in the "
                      "log only", site)
            return None
        payload = {"site": site, "kind": "internal", "engine": engine,
                   "attempt": attempt, **desc, "note": note,
                   "repeats_folded": folded}
        try:
            row = await self.db.append_event(
                FAILURE_EVENT, workspace_id=workspace_id, turn_id=turn_id,
                slot_id=slot_id, node_id=node_id, charged_usd=charged_usd,
                payload=payload)
        except Exception:
            log.exception("could not write the failure record for %s — "
                          "the log line above is the record", site)
            return None
        self._recent[key] = [now, 0, row["seq"]]
        return row

    # --- sync entries: done-callbacks and the loop's own handler ---

    def record_soon(self, site: str, exc: BaseException, **ids) -> None:
        """Schedule `record` on the running loop — for callbacks that
        cannot await. If no loop is running (interpreter exit, a task
        collected after shutdown) the log line stands alone."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            log.error("no running loop — the failure at %s is recorded in "
                      "the log only: %s", site, describe(exc)["error"],
                      exc_info=exc)
            return
        t = loop.create_task(self.record(site, exc, **ids),
                             name=f"capture-{site}")
        self._pending.add(t)
        t.add_done_callback(self._pending.discard)

    def watch(self, task: asyncio.Task, site: str, **ids) -> None:
        """Record the exception a task ends with (cancellation is not a
        failure). Retrieving it here also keeps asyncio from re-reporting
        it as "never retrieved" at collection."""
        def done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                self.record_soon(site, exc, **ids)
        task.add_done_callback(done)

    async def drain(self) -> None:
        """Let scheduled writes land — stop calls this before the store
        closes so a capture from the last tick isn't lost to teardown."""
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    # --- the net under everything: asyncio's own unhandled-exception hook ---

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """Chain onto the loop's exception handler: every "Task exception
        was never retrieved" and every failing callback in the process
        becomes a row. The previous handler (asyncio's default logger)
        still runs first."""
        self._loop = loop
        self._previous_handler = loop.get_exception_handler()

        def handler(lp: asyncio.AbstractEventLoop, context: dict) -> None:
            try:
                if self._previous_handler is not None:
                    self._previous_handler(lp, context)
                else:
                    lp.default_exception_handler(context)
            except Exception:
                log.exception("previous loop exception handler failed")
            exc = context.get("exception")
            if not isinstance(exc, BaseException) or \
                    isinstance(exc, asyncio.CancelledError):
                return
            task = context.get("future") or context.get("task")
            name = getattr(task, "get_name", lambda: None)()
            site = f"asyncio:{name}" if name else "asyncio"
            try:
                self.record_soon(site, exc,
                                 note=str(context.get("message") or "")[:200]
                                 or None)
            except Exception:
                log.exception("capture from the loop handler failed")

        loop.set_exception_handler(handler)

    def uninstall(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.set_exception_handler(self._previous_handler)
        self._loop = None
        self._previous_handler = None
