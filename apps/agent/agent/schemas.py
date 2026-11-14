"""Structured reports the subagents return to the concierge (AGENTS.md section 10 "Returns").

These are reports, not handoffs: the work itself (room geometry, picks, the plan) travels through the
agent filesystem (/rooms, /plans), so the concierge never re-types ids or coordinates.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoomChoice(BaseModel):
    room_id: str
    label: str
    why: str = Field(description="One sentence citing the fields used")
    usable_area_m2: float = Field(description="area_m2 from get_room_geometry, copied, not computed")
    constraints: list[str] = Field(description="e.g. 'longest free wall 3.88 m', 'window on wall 2'")


class PickReport(BaseModel):
    asset_id: str
    count: int
    role: str
    priority: int


class Picks(BaseModel):
    picks_path: str = Field(description="Where save_picks stored them, /plans/<room_id>.picks.json")
    items: list[PickReport]


class PlanReport(BaseModel):
    plan_id: str
    room_id: str
    committed: bool
    placed: list[str] = Field(description="'<instance_id> <name>' for each piece in the committed plan")
    dropped: list[str] = Field(description="Pieces left out and why, e.g. 'armchair: no pose fits'")
    notes: str


class NeedFit(BaseModel):
    need: str
    met: bool | None = Field(description="null when the data does not say")
    evidence: str = Field(description="The field and value, e.g. 'graph: r9 is 1 door from primary r7'")


class Audit(BaseModel):
    listing_id: str
    fit: list[NeedFit]
    contradictions: list[str] = Field(
        description="Audit findings with severity warn or conflict, with evidence"
    )
    verdict: str = Field(description="One or two sentences")
