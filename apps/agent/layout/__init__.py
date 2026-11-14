"""Lane B layout planner: room geometry, pose proposals and plan validation (pure, no I/O).

The agent's tool wrappers (get_room_geometry, propose_poses, validate_layout) call these.
"""

from layout.geometry import RoomGeometry, room_geometry
from layout.propose import DEFAULT_RULE, RULES, propose_poses
from layout.validate import validate_layout

__all__ = ["DEFAULT_RULE", "RULES", "RoomGeometry", "propose_poses", "room_geometry", "validate_layout"]
