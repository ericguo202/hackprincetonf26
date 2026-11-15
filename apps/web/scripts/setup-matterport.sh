#!/usr/bin/env bash
# Local setup for "View in Matterport" (ui/TourView.tsx). Run from anywhere:
#   apps/web/scripts/setup-matterport.sh
#
# Writes apps/web/public/matterport/ (gitignored):
#   bundle/          the self-hosted Matterport Showcase bundle (Bundle SDK). Only the
#                    bundle has the Scene API that places 3D objects in a tour.
#   furniture/v<N>/  furniture .glb copies without meshopt compression, which the
#                    bundle's loader cannot decode (worldgen/scripts/tour_furniture.mjs)
#
# Also needs VITE_MATTERPORT_SDK_KEY (repo-root .env locally, Render dashboard in
# production), with the page's domain on the key's allow list (localhost and
# web-eagy.onrender.com are). The Render web build runs this script before
# `pnpm build`; if it fails there, the build goes on without the tour.
set -euo pipefail

BUNDLE_VERSION="26.10.4_webgl-1728-g1cd7e0997a"
BUNDLE_URL="https://static.matterport.com/showcase-sdk/bundle/${BUNDLE_VERSION}/showcase-bundle.zip"

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$REPO/apps/web/public/matterport"
mkdir -p "$OUT"

if [ "$(cat "$OUT/bundle/version.txt" 2>/dev/null || true)" != "$BUNDLE_VERSION" ]; then
  echo "Downloading Showcase bundle $BUNDLE_VERSION"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/bundle.zip" "$BUNDLE_URL"
  rm -rf "$OUT/bundle"
  if command -v unzip >/dev/null; then
    unzip -q "$tmp/bundle.zip" -d "$OUT/bundle"
  else
    python3 -m zipfile -e "$tmp/bundle.zip" "$OUT/bundle"
  fi
  rm -rf "$tmp"
else
  echo "Showcase bundle $BUNDLE_VERSION already there"
fi

if [ ! -d "$REPO/worldgen/node_modules/@gltf-transform/core" ]; then
  (cd "$REPO/worldgen" && npm ci --silent)
fi
for dir in "$REPO"/apps/web/public/packs/furniture/v*/; do
  v="$(basename "$dir")"
  (cd "$REPO/worldgen" && node scripts/tour_furniture.mjs "$dir" "$OUT/furniture/$v")
done
echo "Done: $OUT"
