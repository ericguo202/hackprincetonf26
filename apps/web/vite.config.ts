import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev: /agent proxies the agent service (uvicorn :8000). The game server is reached
// directly at VITE_GAME_URL (default ws://localhost:2567).
export default defineConfig({
  plugins: [react()],
  // Read the repo-root .env. Only VITE_* vars are exposed to the browser.
  envDir: "../..",
  server: {
    proxy: {
      "/agent": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/agent/, "") },
    },
  },
  test: { environment: "jsdom" },
});
