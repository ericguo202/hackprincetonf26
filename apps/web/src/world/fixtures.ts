// Lane C: the contract fixtures, as raw text, for USE_FIXTURES / no-pack mode.
// Imported with ?raw so they go through the same parse + validate path as a
// fetched pack: if Lane A changes a fixture so it no longer matches the
// contract, the client says so instead of rendering garbage.
//
// Kept apart from pack.ts so pack.ts stays importable without Vite.

import manifestRaw from "../../../../contracts/fixtures/manifest.json?raw";
import listingRaw from "../../../../contracts/fixtures/listing.json?raw";
import roomRaw from "../../../../contracts/fixtures/room.json?raw";
import assetRaw from "../../../../contracts/fixtures/asset.json?raw";

export const FIXTURE_RAW = {
  manifest: manifestRaw,
  /** One Listing; the real index is an array of them. */
  listing: listingRaw,
  /** One Room; a real rooms.json is an array of them. */
  room: roomRaw,
  /** One Asset; a real catalog.json is an array of them. */
  asset: assetRaw,
} as const;
