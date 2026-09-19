import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component-test harness. Kept separate from vite.config.ts so the
// build config stays untouched; vitest picks this file up by name.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
