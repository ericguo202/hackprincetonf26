// Lane C: Rapier collision groups, so queries can tell zone collision from
// everything else. Rapier's test for a query or contact:
//   (a.memberships & b.filter) !== 0 && (b.memberships & a.filter) !== 0
//
// Every collider built from the zone's collision mesh (collision.glb, or the
// placeholder that stands in for it, or the yard under a house) is a member of
// ZONE only. Furniture is a member of FURNITURE only. Both still collide with
// everything (filter = all), so physics is unchanged; only queries that filter
// on ZONE (the camera arm) can tell them apart. Render meshes never have
// colliders at all.

import { interactionGroups } from "@react-three/rapier";

export const GROUP = { zone: 0, furniture: 1, player: 2, camera: 3 } as const;

export const ZONE_COLLIDER = interactionGroups(GROUP.zone);
export const FURNITURE_COLLIDER = interactionGroups(GROUP.furniture);

/** Camera arm raycast: hits zone collision only, never furniture or players. */
export const CAMERA_QUERY = interactionGroups(GROUP.camera, [GROUP.zone]);
