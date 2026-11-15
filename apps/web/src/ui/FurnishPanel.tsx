// Manual furnishing UI (build mode, world/buildState.ts): the catalog drawer
// and the hint bar. The 3D side (ghost, picking, input) is world/BuildController.
//
// The cursor stays free in build mode, so both are shown together:
//   - the catalog (collapsible): search, room tabs, one square tile per asset
//     with its picture, name, colour and style, and size in feet. Clicking a
//     tile holds that piece; move the cursor onto the room to aim it.
//   - the hint bar (top centre): what is held, whether it can go here (and
//     why not), and the keys.
//
// Tiles show the catalog's `thumb` image when the asset has one, else a
// render of the asset's own mesh (world/thumbnails.ts), made only once the
// tile scrolls into view.

import { useEffect, useMemo, useRef, useState } from "react";

import type { Asset } from "../contracts.gen";
import { useStore } from "../store";
import { useBuild } from "../world/buildState";
import { sizeLabel } from "../world/placement";
import { cachedThumbnail, thumbnailFor } from "../world/thumbnails";
import { filterAssets, groupOf, ROOM_GROUPS } from "./catalogGroups";

type Props = {
  catalog: Record<string, Asset>;
  /** Published root that asset `file` and `thumb` paths resolve against; null = no meshes. */
  packBaseUrl: string | null;
};

export function FurnishPanel({ catalog, packBaseUrl }: Props) {
  const active = useBuild((s) => s.active);
  const catalogOpen = useBuild((s) => s.catalogOpen);
  const zone = useStore((s) => s.me.zone);
  if (!active || zone === "block") return null;
  return (
    <>
      <BuildHint catalog={catalog} />
      {catalogOpen ? <Catalog catalog={catalog} packBaseUrl={packBaseUrl} /> : null}
    </>
  );
}

function Catalog({ catalog, packBaseUrl }: Props) {
  const [tab, setTab] = useState("living");
  const [query, setQuery] = useState("");
  const held = useBuild((s) => s.held);
  const pick = useBuild((s) => s.pick);
  const setActive = useBuild((s) => s.setActive);
  const setCatalogOpen = useBuild((s) => s.setCatalogOpen);
  const pieces = useStore((s) => s.houses[s.me.zone]?.objects.length ?? 0);
  const assets = useMemo(() => filterAssets(catalog, query ? "all" : tab, query), [catalog, tab, query]);
  const tabs = useMemo(() => {
    const present = new Set(Object.values(catalog).map((a) => groupOf(a.category)));
    return [...ROOM_GROUPS.filter((g) => present.has(g.id)), ...(present.has("other") ? [{ id: "other", label: "Other" }] : [])];
  }, [catalog]);
  const heldId = held?.kind === "new" ? held.asset_id : null;

  function choose(a: Asset) {
    pick(a.id);
    // Hand the keyboard back to the game (R, Q, WASD) if the search box had it.
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  }

  return (
    <div className="furnish-panel" role="dialog" aria-label="Furnish by hand">
      <div className="furnish-head">
        <div>
          <strong>Furnish by hand</strong>
          <span className="furnish-sub">
            {pieces} {pieces === 1 ? "piece" : "pieces"} in this house · pick one, then click in the room to place it
          </span>
        </div>
        <div className="furnish-actions">
          <button type="button" className="furnish-hide" onClick={() => setCatalogOpen(false)}>
            Hide catalog
          </button>
          <button type="button" className="furnish-done" onClick={() => setActive(false)}>
            Done <kbd>G</kbd>
          </button>
        </div>
      </div>
      <div className="furnish-tools">
        <input
          type="search"
          placeholder="Search: sofa, crib, walnut, modern…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && (e.currentTarget.blur(), setQuery(""))}
        />
        <div className="furnish-tabs" role="tablist">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={!query && tab === t.id}
              className={!query && tab === t.id ? "on" : ""}
              onClick={() => {
                setQuery("");
                setTab(t.id);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div className="furnish-grid">
        {assets.length === 0 ? <p className="furnish-empty">Nothing in the library matches “{query}”.</p> : null}
        {assets.map((a) => (
          <Tile key={a.id} asset={a} root={packBaseUrl} selected={a.id === heldId} onPick={() => choose(a)} />
        ))}
      </div>
      <div className="furnish-foot">
        Click a placed piece to move it, <kbd>X</kbd> removes it. Pieces are real products: Amazon Berkeley Objects,
        Amazon.com (CC BY 4.0).
      </div>
    </div>
  );
}

function Tile({ asset, root, selected, onPick }: { asset: Asset; root: string | null; selected: boolean; onPick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const direct = asset.thumb && root !== null ? `${root}/${asset.thumb}` : null;
  const meshUrl = root !== null ? `${root}/${asset.file}` : null;
  const [rendered, setRendered] = useState<{ url: string; src: string | null } | null>(null);

  useEffect(() => {
    if (direct || !meshUrl || !ref.current) return;
    let alive = true;
    const show = (src: string | null) => alive && setRendered({ url: meshUrl, src });
    const known = cachedThumbnail(meshUrl);
    if (known) {
      void known.then(show);
      return () => {
        alive = false;
      };
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        io.disconnect();
        void thumbnailFor(meshUrl).then(show);
      },
      { rootMargin: "120px" },
    );
    io.observe(ref.current);
    return () => {
      alive = false;
      io.disconnect();
    };
  }, [direct, meshUrl]);

  const src = direct ?? (rendered?.url === meshUrl ? rendered.src : null);
  const sub = [asset.color, asset.style].filter(Boolean).join(" · ");
  const credit = [asset.source.product_name, asset.source.brand].filter(Boolean).join(" · ");
  return (
    <button
      ref={ref}
      type="button"
      className={`furnish-tile${selected ? " on" : ""}`}
      onClick={onPick}
      title={credit || asset.name}
    >
      <span className="furnish-thumb">
        {src ? (
          <img src={src} alt="" draggable={false} />
        ) : (
          <span className="furnish-thumb-empty" data-loading={meshUrl && !direct && rendered === null ? "1" : undefined}>
            {asset.category.replace(/_/g, " ")}
          </span>
        )}
        {asset.cover !== "none" ? <span className={`furnish-cover ${asset.cover}`}>{asset.cover} cover</span> : null}
      </span>
      <span className="furnish-name">{asset.name}</span>
      {sub ? <span className="furnish-meta">{sub}</span> : null}
      <span className="furnish-meta">{sizeLabel(asset.size_m)}</span>
    </button>
  );
}

function BuildHint({ catalog }: { catalog: Record<string, Asset> }) {
  const held = useBuild((s) => s.held);
  const status = useBuild((s) => s.status);
  const hover = useBuild((s) => s.hover);
  const catalogOpen = useBuild((s) => s.catalogOpen);
  const setCatalogOpen = useBuild((s) => s.setCatalogOpen);
  const hoverAsset = useStore((s) => {
    const o = hover ? s.houses[s.me.zone]?.objects.find((p) => p.instance_id === hover) : undefined;
    return o ? catalog[o.asset_id] : undefined;
  });
  const asset = held ? catalog[held.asset_id] : undefined;
  const showCatalog = catalogOpen ? null : (
    <button type="button" className="build-hint-btn" onClick={() => setCatalogOpen(true)}>
      Show catalog
    </button>
  );

  if (held && asset) {
    const verdict = !status
      ? "Point at the floor"
      : status.valid
        ? status.snapped === "wall"
          ? "Against the wall · click to place"
          : status.snapped === "surface"
            ? "On top · click to place"
            : "Click to place"
        : status.why;
    return (
      <div className="build-hint">
        <div className="build-hint-main">
          <strong>{held.kind === "move" ? `Moving the ${asset.name.toLowerCase()}` : asset.name}</strong>
          <span className={status && !status.valid ? "bad" : "ok"}>{verdict}</span>
          {showCatalog}
        </div>
        <div className="build-hint-keys">
          <kbd>Drag</kbd> look <kbd>WASD</kbd> walk <kbd>R</kbd>/<kbd>Wheel</kbd> rotate <kbd>Alt</kbd> no snap{" "}
          <kbd>Q</kbd>/<kbd>Esc</kbd> put back
          {held.kind === "move" ? (
            <>
              {" "}
              <kbd>X</kbd> remove
            </>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <div className="build-hint">
      <div className="build-hint-main">
        {hoverAsset ? (
          <>
            <strong>{hoverAsset.name}</strong>
            <span className="ok">Click to move it · X to remove</span>
          </>
        ) : (
          <span>Pick a piece from the catalog, or click a placed piece to move it</span>
        )}
        {showCatalog}
      </div>
      <div className="build-hint-keys">
        <kbd>Drag</kbd> look <kbd>WASD</kbd> walk <kbd>Esc</kbd>/<kbd>G</kbd> done
      </div>
    </div>
  );
}
