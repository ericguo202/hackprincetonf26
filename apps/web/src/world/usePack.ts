// Lane C: load the pack once per page. Everything downstream takes the
// LoadedPack as a prop (rooms per house and the furniture catalog included),
// so nothing here writes to store.ts.

import { useEffect, useState } from "react";

import { FIXTURE_RAW } from "./fixtures";
import { loadPack, type LoadedPack } from "./pack";

export type PackStatus =
  | { status: "loading" }
  | { status: "ready"; pack: LoadedPack }
  | { status: "error"; error: string };

export function usePack(): PackStatus {
  const [state, setState] = useState<PackStatus>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    loadPack({ base: import.meta.env.VITE_PACK_BASE_URL, fixtures: FIXTURE_RAW })
      .then((pack) => alive && setState({ status: "ready", pack }))
      .catch((e: Error) => {
        console.error("[pack]", e);
        if (alive) setState({ status: "error", error: e.message });
      });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}
