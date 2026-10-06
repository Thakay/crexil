// Hash routes: hand-rolled, no router library. Hash
// routing is also what keeps deep links F5-safe under the server's
// StaticFiles(html=True) mount; real paths would 404 on reload.
// Navigation is plain <a href> — native and keyboard-operable.
//
// One parser serves every build. The legacy canvas owns `#/w` (Home) and
// `#/w/<id>` (a workspace). The project surface owns `#/projects`,
// `#/projects/<id>`, `#/operations` and `#/connections`, each optionally
// followed by `?query` state that the surface reads itself. The bare URL
// (no hash, `#`, `#/`) is the build's front door: the legacy Home, unless
// the build chooses the project surface (see `frontDoor`).
// Links always spell the canonical forms (`#/w`, `#/projects`) for deep
// links and specs.
import { useSyncExternalStore } from "react";

export type FrontDoor = "workspaces" | "projects";

export type ProjectSurfaceRoute =
  | { view: "projects" }
  | { view: "project"; id: string }
  | { view: "operations" }
  | { view: "connections" };

export type Route =
  | { view: "workspaces" }
  | { view: "workspace"; id: string }
  | ProjectSurfaceRoute
  | { view: "unknown" };

// The build-time variables, as Vite exposes them (import.meta.env).
type BuildEnv = Readonly<Record<string, unknown>>;

// The bare URL's destination, chosen at build time:
// VITE_CREXIL_FRONT_DOOR=projects|workspaces names it explicitly; otherwise
// a preview build (VITE_CREXIL_PREVIEW=1) opens on the project surface and
// every other build on the legacy Home. Any other value means the default.
export function frontDoor(env: BuildEnv = import.meta.env): FrontDoor {
  const chosen = env.VITE_CREXIL_FRONT_DOOR;
  if (chosen === "projects" || chosen === "workspaces") return chosen;
  return env.VITE_CREXIL_PREVIEW === "1" ? "projects" : "workspaces";
}

const WORKSPACES = /^#\/w(?:\/([^/]+))?\/?$/;
const PROJECTS = /^#\/projects(?:\/([^/?]*))?(?:\?.*)?$/;
const OPERATIONS = /^#\/operations\/?(?:\?.*)?$/;
const CONNECTIONS = /^#\/connections\/?(?:\?.*)?$/;

export function parseHash(hash: string, door: FrontDoor = frontDoor()): Route {
  if (hash === "" || hash === "#" || hash === "#/") return { view: door };
  const workspace = WORKSPACES.exec(hash);
  if (workspace)
    return workspace[1]
      ? { view: "workspace", id: workspace[1] }
      : { view: "workspaces" };
  const project = PROJECTS.exec(hash);
  if (project)
    return project[1] ? { view: "project", id: project[1] } : { view: "projects" };
  if (OPERATIONS.test(hash)) return { view: "operations" };
  if (CONNECTIONS.test(hash)) return { view: "connections" };
  return { view: "unknown" };
}

export function onProjectSurface(route: Route): route is ProjectSurfaceRoute {
  return (
    route.view === "projects" ||
    route.view === "project" ||
    route.view === "operations" ||
    route.view === "connections"
  );
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
  projects: "#/projects",
};
