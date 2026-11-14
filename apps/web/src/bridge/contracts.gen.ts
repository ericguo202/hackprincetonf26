/* Generated from contracts/. Do not edit; run `pnpm gen:types`. */

export interface Asset {
  id: string;
  name: string;
  category: string;
  tags: string[];
  file: string;
  size_m: {
    w: number;
    d: number;
    h: number;
  };
  placement: "wall" | "corner" | "free" | "on_surface";
  clearance_m: {
    front?: number;
    back?: number;
    left?: number;
    right?: number;
  };
  license: string;
  source: string;
}

/**
 * @minItems 3
 * @maxItems 3
 */
export type Vec3 = [number, number, number];

export interface Listing {
  id: string;
  address: string;
  lat: number;
  lon: number;
  status: "for_sale" | "for_rent";
  price_usd: number | null;
  rent_usd_month: number | null;
  beds: number;
  baths: number;
  area_m2: number;
  stories: number;
  year_built: number | null;
  property_type: string;
  features: string[];
  site: {
    yard_area_m2: number | null;
    parking_spaces: number | null;
    street_class: string | null;
  };
  has_interior: boolean;
  interior_mesh: string | null;
  world: {
    position: Vec3;
    heading_deg: number;
    door: Vec3;
  };
  source: {
    costar_id?: string | null;
    matterport_model_id?: string | null;
    vexcel?: string | null;
  };
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

export interface Room {
  id: string;
  listing_id: string;
  label:
    | "living_room"
    | "bedroom"
    | "primary_bedroom"
    | "kitchen"
    | "dining_room"
    | "bathroom"
    | "hallway"
    | "office"
    | "closet"
    | "garage"
    | "utility"
    | "other";
  label_confidence: number;
  floor: number;
  floor_y: number;
  height_m: number;
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
