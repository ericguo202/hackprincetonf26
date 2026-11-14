"""Phase 2a: fetch Matterport detail (incl. room boundary vectors) for every home-linked model.

uv run python -m worldgen.scout.mp_fetch [--only-splats] [--limit N]

Order: the 211 residential splat models, then every other model linked to a home in listings.jsonl,
then the 21 commercial splat models. At most 4 concurrent requests; each response is cached under
out/scout/cache/mp_detail/<id>.json, so a restart skips finished models. Errors go to
out/scout/mp_fetch_errors.jsonl (retried on the next run).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from worldgen.scout import mp
from worldgen.scout.common import MAX_CONCURRENCY, OUT, Cache, append_jsonl, now_iso, read_jsonl


def model_order() -> list[tuple[str, str]]:
    """[(model_id, group)] in fetch order, de-duplicated."""
    homes = list(read_jsonl(OUT / "listings.jsonl"))
    seen: set[str] = set()
    order: list[tuple[str, str]] = []

    def add(mid: str, group: str) -> None:
        if mid and mid not in seen:
            seen.add(mid)
            order.append((mid, group))

    for h in homes:
        for mid in h["splat_models_all"]:
            add(mid, "splat_residential")
    for h in homes:
        for mid in h["matterport_model_ids"]:
            add(mid, "home")
    for c in read_jsonl(OUT / "commercial_splats.jsonl"):
        add(c["model_id"], "splat_commercial")
    return order


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--only-splats", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--workers", type=int, default=MAX_CONCURRENCY)
    args = p.parse_args(argv)
    workers = min(args.workers, MAX_CONCURRENCY)

    cache = Cache("mp_detail")
    order = model_order()
    if args.only_splats:
        order = [o for o in order if o[1].startswith("splat")]
    todo = []
    for mid, group in order:
        hit = cache.get(mid)
        if hit is None or hit.get("query_version") != mp.QUERY_VERSION:
            todo.append((mid, group))
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(order)} models, {len(order) - len(todo)} cached, {len(todo)} to fetch", file=sys.stderr)

    t0 = time.time()
    done = errs = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(mp.model_detail, mid, cache): (mid, group) for mid, group in todo}
        for fut in as_completed(futs):
            mid, group = futs[fut]
            try:
                rec = fut.result()
                gql_errors = rec["response"].get("errors")
                if gql_errors:
                    append_jsonl(
                        OUT / "mp_fetch_errors.jsonl",
                        [{"at": now_iso(), "model_id": mid, "group": group, "graphql_errors": gql_errors}],
                    )
            except Exception as e:  # noqa: BLE001 - record and continue; next run retries
                errs += 1
                append_jsonl(
                    OUT / "mp_fetch_errors.jsonl",
                    [{"at": now_iso(), "model_id": mid, "group": group, "error": str(e)[:300]}],
                )
            done += 1
            if done % 100 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  {done}/{len(todo)} fetched, {errs} errors, {rate:.1f}/s", file=sys.stderr)
    return 0 if errs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
