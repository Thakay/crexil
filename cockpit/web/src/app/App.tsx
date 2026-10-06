// App root: the ONLY module that loads global CSS (fonts, tokens,
// base) — one place to look for what the whole page inherits.
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "../styles/tokens.css";
import "../styles/base.css";
import "@xyflow/react/dist/style.css"; // the canvas, scoped to .react-flow

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HotkeysProvider } from "react-hotkeys-hook";
import { $api } from "../api/client";
import { frontDoor, href, onProjectSurface, useHashRoute } from "./routes";
import { Shell } from "./shell/Shell";
import styles from "./App.module.css";
import { Crexil } from "./crexil/Filmstrip";

// Drift detector: bundle id against the server's /api/health build id.
// Server unreachable → bundle id alone, never a crash.
function DriftFooter() {
  const bundle = import.meta.env.VITE_BUILD_ID ?? "dev";
  const health = $api.useQuery("get", "/api/health");
  const server = health.data?.build.id;
  return (
    <footer className={`mono-micro ${styles.footer}`}>
      {server === undefined
        ? `bundle ${bundle}`
        : `bundle ${bundle} · server ${server}${bundle !== server ? " · BUILD DRIFT" : ""}`}
    </footer>
  );
}

export default function App() {
  // Per-mount client (test-friendly); retry: false — against a localhost
  // server a failure is real and should be loud, not silently retried
  // (law 9). A query that needs retries sets them per query.
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  // One parser decides the surface in every build (routes.ts); the
  // project surface reads its own sub-route and query state.
  const route = useHashRoute();
  const door = frontDoor();
  return (
    <QueryClientProvider client={queryClient}>
      <HotkeysProvider>
        {onProjectSurface(route) ? (
          <Crexil />
        ) : (
          <div className={styles.page}>
            {route.view === "unknown" ? (
              <div className={styles.pad}>
                <p className="effect-line">
                  Nothing at this address.{" "}
                  <a href={href[door]}>Back to {door}</a>
                </p>
              </div>
            ) : (
              <Shell
                workspaceId={route.view === "workspace" ? route.id : null}
              />
            )}
            <DriftFooter />
          </div>
        )}
      </HotkeysProvider>
    </QueryClientProvider>
  );
}
