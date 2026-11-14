"""Latitude/longitude <-> block frame (AGENTS.md section 7, contracts/manifest.schema.json).

Block frame: origin at the block centre (manifest `origin`), ground level; +X east, +Y up, +Z south; meters.
A local tangent plane with the WGS84 radii of curvature at the origin: under 1 cm of error within the 200 m
of a block, which is all the game needs."""

from __future__ import annotations

import math

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


def _radii(lat0: float) -> tuple[float, float]:
    """(meridian radius M, prime vertical radius N) at latitude lat0, meters."""
    s = math.sin(math.radians(lat0))
    w = 1.0 - WGS84_E2 * s * s
    return WGS84_A * (1.0 - WGS84_E2) / w**1.5, WGS84_A / math.sqrt(w)


def enu_m(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """(east, north) meters of (lat, lon) from the origin (lat0, lon0)."""
    m, n = _radii(lat0)
    east = math.radians(lon - lon0) * n * math.cos(math.radians(lat0))
    north = math.radians(lat - lat0) * m
    return east, north


def latlon(east: float, north: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Inverse of enu_m."""
    m, n = _radii(lat0)
    lat = lat0 + math.degrees(north / m)
    lon = lon0 + math.degrees(east / (n * math.cos(math.radians(lat0))))
    return lat, lon


def block_xz(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Block-frame (x, z) of (lat, lon): x = east, z = south."""
    e, n = enu_m(lat, lon, lat0, lon0)
    return e, -n


def distance_m(lat: float, lon: float, lat0: float, lon0: float) -> float:
    e, n = enu_m(lat, lon, lat0, lon0)
    return math.hypot(e, n)
