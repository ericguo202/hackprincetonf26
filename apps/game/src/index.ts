// Entry point. Listens on PORT (default 2567). Render runs `npm start`.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { listen } from "@colyseus/tools";
import app from "./app.config.ts";

// Locally, share the repo-root .env with the agent (GAME_INTERNAL_SECRET). On Render, env vars are set.
const rootEnv = fileURLToPath(new URL("../../../.env", import.meta.url));
if (existsSync(rootEnv)) process.loadEnvFile(rootEnv);

listen(app);
