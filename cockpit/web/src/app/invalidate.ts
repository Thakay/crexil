// One list of what a workspace verb can move: the detail (turns +
// slots), the node feed (canvas + tray), the queue, the tray's own
// readout, and the money corner, whose query is keyed with the
// browser's tz offset: the bare options here are a key PREFIX, which is
// how react-query matches it. Every mutation and the events poll invalidate through here so
// a new surface joins in one place, not in four. Keys always come from
// $api.queryOptions, never hand-built tuples.
import type { QueryClient } from "@tanstack/react-query";
import { $api } from "../api/client";

export function invalidateWorkspace(
  queryClient: QueryClient,
  workspaceId: string,
): void {
  const path = { params: { path: { wid: workspaceId } } };
  for (const opts of [
    $api.queryOptions("get", "/api/workspaces/{wid}", path),
    $api.queryOptions("get", "/api/workspaces/{wid}/nodes", path),
    $api.queryOptions("get", "/api/workspaces/{wid}/tray", path),
    $api.queryOptions("get", "/api/queue"),
    $api.queryOptions("get", "/api/costs"),
    // The sidebar list belongs here too: every workspace verb touches the
    // row it acts on, which re-ranks the list (most-recently-touched
    // first) and moves its node/promoted counts. The list is a workspace
    // surface like the rest, not a static index.
    $api.queryOptions("get", "/api/workspaces"),
  ])
    void queryClient.invalidateQueries({ queryKey: opts.queryKey });
}
