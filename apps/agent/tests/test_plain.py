"""agent/plain.py: audit findings as buyer-facing sentences, and the SSE event keeps the contract shape."""

from agent import events, plain

LISTING = {"id": "hou_237901269", "address": "6915 Navidad Road, Houston, TX 77083"}


def f(check, listed, observed, severity="warn"):
    return {"check": check, "severity": severity, "listed": listed, "observed": observed, "evidence": "x"}


def test_area_warning_reads_in_square_feet():
    s = plain.finding_sentence(f("area_vs_scan", 122.4, 103.9), "6915 Navidad Road")
    assert s == (
        "6915 Navidad Road's listing says 1,318 sq ft, but the scan measures about 1,118 sq ft (15% smaller)."
    )


def test_counts_pool_and_info():
    house = "7014 Navidad Road"
    assert plain.finding_sentence(f("stories_vs_scan", 1, 2, "conflict"), house) == (
        "7014 Navidad Road is listed as 1 story, but the scan shows 2 floors."
    )
    assert "scan shows 2" in plain.finding_sentence(f("beds_vs_rooms", 3, 2, "conflict"), house)
    assert "aerial imagery shows none" in plain.finding_sentence(f("pool_vs_vexcel", True, False), house)
    assert plain.finding_sentence(f("beds_vs_rooms", 3, 3, "info"), house) is None


def test_with_sentences_only_marks_warn_and_conflict():
    audit = {"listing_id": LISTING["id"], "findings": [f("area_vs_scan", 122.4, 103.9), f("x", 1, 1, "info")]}
    out = plain.with_sentences(audit, LISTING)
    assert out["findings"][0]["say"].startswith("6915 Navidad Road's listing")
    assert "say" not in out["findings"][1]
    assert plain.house_name(None, "hou_1") == "hou_1"


def test_audit_event_drops_say():
    audit = plain.with_sentences({"listing_id": "h", "findings": [f("area_vs_scan", 100, 80)]}, None)
    assert all("say" not in x for x in events.strip_say(audit)["findings"])


def test_search_matches_carry_contradiction_sentences(monkeypatch):
    from agent import tools
    from data import packs

    audit = {
        "listing_id": "h",
        "findings": [f("area_vs_scan", 122.4, 103.9), f("beds_vs_rooms", 3, 2, "conflict")],
    }
    monkeypatch.setattr(packs, "get_audit", lambda lid: audit)
    monkeypatch.setattr(packs, "get_listing", lambda lid: LISTING)
    said = tools._contradictions("h")
    assert said[0] == "6915 Navidad Road is listed with 3 bedrooms, but the scan shows 2."  # conflict first
    assert said[1].endswith("(15% smaller).")
    monkeypatch.setattr(packs, "get_audit", lambda lid: (_ for _ in ()).throw(KeyError(lid)))
    assert tools._contradictions("none") == []
