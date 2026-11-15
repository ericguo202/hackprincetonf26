"""Plain-English sentences for audit findings, computed in code (AGENTS.md rule 5).

The concierge's answer is read aloud to home buyers, so it quotes these sentences instead of field names,
m2 values or numbers it worked out itself. Areas are shown in square feet (AGENTS.md section 5).
"""

from __future__ import annotations

SQFT_PER_M2 = 10.7639


def sqft(m2: float) -> int:
    return round(m2 * SQFT_PER_M2)


def house_name(listing: dict | None, fallback: str) -> str:
    """'7014 Navidad Road, Houston, TX 77083' -> '7014 Navidad Road'."""
    address = (listing or {}).get("address")
    return address.split(",")[0].strip() if address else fallback


def _count(n: object, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def finding_sentence(f: dict, house: str) -> str | None:
    """One sentence for a warn or conflict finding; None for info (checked and consistent)."""
    if f.get("severity") == "info":
        return None
    check, listed, seen = f.get("check"), f.get("listed"), f.get("observed")
    num = isinstance(listed, int | float) and isinstance(seen, int | float)
    if check == "area_vs_scan" and num and listed:
        pct = round(100 * abs(seen / listed - 1))
        size = "smaller" if seen < listed else "larger"
        return (
            f"{house}'s listing says {sqft(listed):,} sq ft, but the scan measures about "
            f"{sqft(seen):,} sq ft ({pct}% {size})."
        )
    if check == "stories_vs_scan" and num:
        return (
            f"{house} is listed as {_count(listed, 'story', 'stories')}, "
            f"but the scan shows {_count(seen, 'floor', 'floors')}."
        )
    if check == "beds_vs_rooms" and num:
        return f"{house} is listed with {_count(listed, 'bedroom', 'bedrooms')}, but the scan shows {seen}."
    if check == "baths_vs_rooms" and num:
        return f"{house} is listed with {_count(listed, 'bathroom', 'bathrooms')}, but the scan shows {seen}."
    if check == "pool_vs_vexcel" and isinstance(listed, bool) and isinstance(seen, bool):
        return (
            f"{house}'s listing says it has a pool, but aerial imagery shows none."
            if listed
            else f"{house}'s listing says no pool, but aerial imagery shows one."
        )
    what = str(check or "a detail").replace("_", " ")
    return f"{house}'s listing and scan disagree on {what}: listed {listed}, observed {seen}."


def with_sentences(audit: dict, listing: dict | None) -> dict:
    """The audit with a `say` sentence on each warn or conflict finding (tool output, not the SSE event)."""
    house = house_name(listing, audit.get("listing_id", "This home"))
    findings = []
    for f in audit.get("findings", []):
        say = finding_sentence(f, house)
        findings.append({**f, "say": say} if say else f)
    return {**audit, "findings": findings}
