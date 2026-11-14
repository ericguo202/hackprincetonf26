/* Generated from contracts/. Do not edit; run `pnpm gen:types`. */

/**
 * One entry of furniture/catalog.json (AGENTS.md section 9). Asset .glb: Y up, meters, origin at bottom center, front facing +Z, under 5 MB.
 */
export interface Asset {
  /**
   * <category>_<source>_<source id>, e.g. sofa_abo_B075X4QMX3; stable across rebuilds
   */
  id: string;
  /**
   * Short generic name for in-world labels (no brand)
   */
  name: string;
  category: string;
  /**
   * From the source listing when present; never invented
   */
  style?: string | null;
  /**
   * From the source listing when present; never invented
   */
  color?: string | null;
  /**
   * From the source listing when present; never invented
   */
  material?: string | null;
  tags: string[];
  /**
   * Relative to the pack version folder
   */
  file: string;
  /**
   * Optional small catalog image for the chat panel, relative to the pack version folder
   */
  thumb?: string;
  /**
   * Measured from the normalized mesh, never copied from listing text
   */
  size_m: {
    /**
     * Along local X
     */
    w: number;
    /**
     * Along local Z
     */
    d: number;
    h: number;
  };
  /**
   * Default placement; wall, corner, center and beside match propose_poses rules
   */
  placement: "wall" | "corner" | "center" | "beside" | "free" | "on_surface";
  clearance_m: {
    front?: number;
    back?: number;
    left?: number;
    right?: number;
  };
  /**
   * full: taller than 1.2 m; half: 0.5 to 1.2 m; none: below 0.5 m
   */
  cover: "full" | "half" | "none";
  source: {
    dataset: "ABO" | "PolyHaven" | "KayKit" | "Kenney";
    item_id?: string | null;
    /**
     * Full listing name; credits screen and chat panel only, never in-world signage
     */
    product_name?: string | null;
    brand?: string | null;
    license: string;
    credit: string;
  };
}

/**
 * houses/<listing_id>/audit.json: deterministic contradictions found between data layers.
 */
export interface Audit {
  listing_id: string;
  findings: {
    /**
     * e.g. stories_vs_scan, beds_vs_rooms, area_vs_scan, pool_vs_vexcel
     */
    check: string;
    severity: "info" | "warn" | "conflict";
    /**
     * Value from the listing (CoStar)
     */
    listed: {
      [k: string]: unknown;
    };
    /**
     * Value from the scan or imagery
     */
    observed: {
      [k: string]: unknown;
    };
    /**
     * Data fields used, e.g. listing.stories, rooms[label=staircase]
     */
    evidence: string;
  }[];
}

/**
 * houses/<listing_id>/graph.json: rooms as nodes, doors and stairs as edges.
 */
export interface HouseGraph {
  nodes: {
    id: string;
    label: string;
    area_m2: number;
    floor: number;
    has_window: boolean;
  }[];
  edges: {
    a: string;
    b: string;
    kind: "door" | "stairs" | "open";
    width_m: number | null;
  }[];
}

/**
 * @minItems 3
 * @maxItems 3
 */
export type Vec3 = [number, number, number];
/**
 * House-local door point
 *
 * @minItems 3
 * @maxItems 3
 */
export type Vec31 = [number, number, number];

/**
 * One entry of listings/index.json. Fields the CoStar folder does not have stay null; never fabricate them.
 */
export interface Listing {
  id: string;
  address: string;
  lat: number;
  lon: number;
  /**
   * The world pack this listing is placed in; null when it is in the index but on no built block
   */
  block_id: string | null;
  status: "for_sale" | "for_rent";
  /**
   * From CoStar property.propertyType: 1 Detached -> single_family, 2 Townhouse -> townhouse, 4 Condo and 256 Coop -> condo, 512 Duplex -> duplex, 16 MultiFamily, 1024 Triplex and 2048 Quadruplex -> multi_family, 8 Other and 64 MobileManufactured -> other. 0 None, 32 LotsLands and 128 Commercial are not homes and are left out of the index.
   */
  property_type: "single_family" | "townhouse" | "condo" | "duplex" | "multi_family" | "other";
  price_usd: number | null;
  rent_usd_month: number | null;
  tax_usd_year: number | null;
  beds: number | null;
  baths: number | null;
  area_m2: number | null;
  stories: number | null;
  year_built: number | null;
  features: {
    pool: boolean | null;
    parking_spaces: number | null;
    garage: boolean | null;
    fenced_yard: boolean | null;
    utilities: string[];
  };
  schools: {
    name: string;
    level: "elementary" | "middle" | "high" | "other";
    distance_m: number | null;
    rating: number | null;
  }[];
  site: {
    /**
     * CoStar property.lotSizeAcresMin converted to square meters; null when CoStar has no lot size
     */
    lot_area_m2: number | null;
  };
  has_interior: boolean;
  /**
   * Placement of the house group in the block frame; null when the listing is not on a built block
   */
  world: {
    position: Vec3;
    heading_deg: number;
    door: Vec31;
  } | null;
  source: {
    /**
     * Raw CoStar residential listing id (MLS listing id), kept for traceability
     */
    costar_id: string;
    /**
     * From costar-hackathon-kit/residential-listings.csv (ListingId -> MatterportModelId). With several models, the one on the listing's 3D-tour media item (type 43), else the first CSV row
     */
    matterport_model_id: string | null;
    vexcel: string | null;
  };
}

/**
 * @minItems 3
 * @maxItems 3
 */
export type Spawn = [number, number, number];

/**
 * DRAFT (shape not fixed by AGENTS.md; see HANDOFF.md). packs/<block_id>/v<N>/manifest.json. Block frame: origin at block center, ground level; +X east, +Y up, +Z south.
 */
export interface PackManifest {
  block_id: string;
  version: number;
  origin: {
    lat: number;
    lon: number;
  };
  block: {
    render: string;
    collision: string;
  };
  houses: {
    listing_id: string;
    interior: string;
    collision: string;
    rooms: string;
    graph: string;
    audit: string;
  }[];
  /**
   * Sidewalk spawn points in the block frame
   */
  spawns: Spawn[];
  furniture_catalog: string;
}

export interface PlacementPlan {
  plan_id: string;
  listing_id: string;
  room_id: string;
  status: "draft" | "validated" | "placed";
  objects: {
    instance_id: string;
    asset_id: string;
    x: number;
    y: number;
    z: number;
    rot_deg: number;
    /**
     * 12 words or fewer
     */
    reason: string;
  }[];
  conflicts: {
    instance_id: string;
    type: "outside_room" | "overlap" | "blocks_opening" | "clearance";
    detail: string;
  }[];
}

/**
 * @minItems 2
 * @maxItems 2
 */
export type Point2 = [number, number];

/**
 * One entry of houses/<listing_id>/rooms.json. House-local frame: Y up, meters; 2D points are [x, z].
 */
export interface Room {
  id: string;
  label:
    | "living_room"
    | "bedroom"
    | "primary_bedroom"
    | "kitchen"
    | "dining_room"
    | "bathroom"
    | "hallway"
    | "staircase"
    | "office"
    | "closet"
    | "garage"
    | "utility"
    | "other";
  label_confidence: number;
  floor: number;
  floor_y: number;
  height_m: number;
  /**
   * Computed from the polygon
   */
  area_m2: number;
  /**
   * @minItems 3
   */
  polygon: [Point2, Point2, Point2, ...Point2[]];
  openings: {
    type: "door" | "window";
    a: Point2;
    b: Point2;
    to_room: string | null;
  }[];
}
