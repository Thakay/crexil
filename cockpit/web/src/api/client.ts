// API client: openapi-fetch typed against the generated paths,
// wrapped by openapi-react-query. A route change regenerates the paths and
// tsc flags every stale call site — that is the migration net by design.
//
// Invalidation rule: derive query keys via $api.queryOptions(...).queryKey —
// never hand-assemble [method, path] tuples.
import createFetchClient from "openapi-fetch";
import createClient from "openapi-react-query";
import type { paths } from "../generated/openapi";

// Same-origin always (FastAPI serves the SPA in prod; the vite proxy covers
// dev). location.origin rather than "" because Request() refuses relative
// URLs outside a real document — vitest's jsdom included.
// fetch resolves lazily: openapi-fetch otherwise captures globalThis.fetch at
// client creation (module import), which would defeat test-time fetch stubs.
export const fetchClient = createFetchClient<paths>({
  baseUrl: location.origin,
  fetch: (input) => globalThis.fetch(input),
});

export const $api = createClient(fetchClient);
