import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev server proxies the FastAPI app (uvicorn on :8000) for sockets, data and assets.
export default defineConfig({
  plugins: [react()],
  // Read the repo-root .env. Only VITE_* vars are exposed to the browser.
  envDir: "../..",
  server: {
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/data": "http://localhost:8000",
      "/assets": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: { environment: "jsdom" },
});
