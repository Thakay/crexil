// Poll loop over GET /api/events (the canvas's feed; slot.* rides it too).
// The browser has no websocket feed: a 2s seq-cursor poll is the intended
// live feed. Cursor bootstraps from /api/queue's seq so history never
// replays; the poll pauses while the tab is hidden and ticks immediately on
// return.
import { useEffect, useRef } from "react";
import { fetchClient } from "../api/client";
import type { Event } from "../types";

const TICK_MS = 2000;

export function useEvents(
  workspaceId: string | null,
  onEvents: (events: Event[]) => void,
): void {
  const handler = useRef(onEvents);
  handler.current = onEvents;

  useEffect(() => {
    if (workspaceId === null) return;
    let cursor: number | null = null;
    let inFlight = false;
    let stopped = false;

    const tick = async () => {
      if (inFlight || stopped || document.hidden) return;
      inFlight = true;
      try {
        if (cursor === null) {
          const q = await fetchClient.GET("/api/queue");
          if (!stopped && q.data !== undefined) cursor = q.data.seq;
          return; // next tick reads from the tip
        }
        const r = await fetchClient.GET("/api/events", {
          params: { query: { since: cursor, workspace_id: workspaceId } },
        });
        if (stopped || r.data === undefined) return;
        cursor = r.data.seq;
        if (r.data.events.length > 0) handler.current(r.data.events);
      } catch {
        // server away: the query layer owns loud failure; the poll
        // just tries again next tick
      } finally {
        inFlight = false;
      }
    };

    void tick();
    const id = window.setInterval(() => void tick(), TICK_MS);
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [workspaceId]);
}
