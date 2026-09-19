/// <reference types="vite/client" />

// Build stamp: import.meta.env.VITE_BUILD_ID. Vite exposes VITE_* from
// the process env natively (Dockerfile ENV VITE_BUILD_ID=$GIT_REV), so no
// define is needed and vitest sees undefined → "dev". The footer compares it
// against the server's /api/health build id; mismatch renders a DRIFT warning.
interface ImportMetaEnv {
  readonly VITE_BUILD_ID?: string;
}
