// Hash routes: hand-rolled, no router library. Hash
// routing is also what keeps deep links F5-safe under the server's
// StaticFiles(html=True) mount; real paths would 404 on reload.
// Navigation is plain <a href> — native and keyboard-operable.
//
// `#/w` is Home and `#/w/<id>` a workspace; the bare URL (no hash, `#`,
// `#/`) is Home too, so `http://localhost:8000/` is the front door.
// Links always spell `#/w` — one canonical form for deep links and specs.
import { useSyncExternalStore } from "react";

export type Route =
  | { view: "workspaces" }
  | { view: "workspace"; id: string }
  | { view: "unknown" };

export function parseHash(hash: string): Route {
  if (hash === "" || hash === "#" || hash === "#/") return { view: "workspaces" };
  const m = /^#\/w(?:\/([^/]+))?\/?$/.exec(hash);
  if (!m) return { view: "unknown" };
  return m[1] ? { view: "workspace", id: m[1] } : { view: "workspaces" };
}

const subscribe = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};

export function useHashRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => location.hash);
  return parseHash(hash);
}

export const href = {
  workspaces: "#/w",
  workspace: (id: string) => `#/w/${id}`,
};
