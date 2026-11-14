# HANDOFF

Cross-lane notes and blockers. Append only. Format:

`YYYY-MM-DD HH:MM  lane-x -> lane-y: note`

---
2026-11-13 20:40  scaffold -> all: TypeScript pinned to ^6 (typescript-eslint does not support TS 7 yet); jsdom pinned to ^26 (jsdom 31 needs Node >= 22.15). Node 22.13 corepack has stale signing keys: run `npm i -g corepack@latest` and make sure %APPDATA%\npm is ahead of Program Files\nodejs on PATH, or pnpm fails with "Cannot find matching keyid".
2026-11-13 20:40  scaffold -> all: types are generated from contracts/. Web: `pnpm gen:types` -> src/bridge/contracts.gen.ts. Server: `uv run python gen_models.py` -> apps/server/contracts_gen/. Re-run both after any contract change.
2026-11-13 21:10  scaffold -> all: CoStar has no API key. All CoStar data is local in costar-hackathon-kit/ (start at index.md; data/*.jsonl via Git LFS, run `git lfs pull` after cloning). `COSTAR_API_KEY` removed from .env.example; use `COSTAR_KIT_DIR`. Added `VITE_MATTERPORT_SDK_KEY` (Showcase SDK key, not a secret, browser-exposed via Vite; read it as `import.meta.env.VITE_MATTERPORT_SDK_KEY`). Vite now loads env from the repo-root .env (envDir); only VITE_* vars reach the browser.
