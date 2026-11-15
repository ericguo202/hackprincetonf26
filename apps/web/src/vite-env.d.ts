/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Matterport Showcase SDK key. Not a secret. */
  readonly VITE_MATTERPORT_SDK_KEY?: string;
  /** Colyseus game service, e.g. wss://game-xxxx.onrender.com. Default ws://localhost:2567. */
  readonly VITE_GAME_URL?: string;
  /** Agent service base URL. Default "/agent" (Vite dev proxy to localhost:8000). */
  readonly VITE_AGENT_URL?: string;
  /** World pack base URL (worldgen publish output). */
  readonly VITE_PACK_BASE_URL?: string;
  /** "1" runs without the game server: local state only (AGENTS.md section 12). */
  readonly VITE_SOLO?: string;
  /** Muted looping clip played on catalog TV screens (world/TvScreen.tsx). Unset: TVs stay off. */
  readonly VITE_TV_VIDEO_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
