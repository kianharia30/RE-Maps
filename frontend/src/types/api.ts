/**
 * TypeScript mirror of the backend's normalised model.
 * Kept one-for-one with `backend/app/models/` — a change there is a breaking
 * API change and must be reflected here.
 */

/** The four (plus one) categories of price the UI must never render alike. */
export type PriceType =
  | "TRANSACTION"
  | "HISTORICAL_ESTIMATE"
  | "CURRENT_ESTIMATE"
  | "FORECAST"
  | "REGIONAL_STATISTIC";

/** How precisely a figure describes an individual dwelling. */
export type PrecisionLevel =
  | "EXACT_TRANSACTION"
  | "PROPERTY_ESTIMATE"
  | "STREET_POSTCODE"
  | "NEIGHBOURHOOD"
  | "CITY_REGIONAL"
  | "NONE";

/** How precisely a coordinate locates the dwelling. */
export type CoordinatePrecision =
  | "PROPERTY"
  | "PARCEL"
  | "ADDRESS"
  | "POSTCODE"
  | "NEIGHBOURHOOD"
  | "REGION";

export type Confidence = "HIGH" | "MEDIUM" | "LOW" | "VERY_LOW";

/** Distinguishes genuinely-absent data from failures. */
export type DataStatus =
  | "OK"
  | "NO_DATA"
  | "INSUFFICIENT_EVIDENCE"
  | "UNSUPPORTED_LOCATION"
  | "PROVIDER_ERROR"
  | "OUT_OF_RANGE";

export type MapTier =
  | "WORLD"
  | "COUNTRY"
  | "REGION"
  | "CITY"
  | "NEIGHBOURHOOD"
  | "POSTCODE"
  | "STREET"
  | "PROPERTY";

export interface SourceRef {
  key: string;
  name: string;
  owner: string;
  url: string;
  licence: string;
  licence_url: string | null;
  attribution: string;
  source_published_at: string | null;
  last_ingested_at: string | null;
}

export interface Price {
  value: number;
  currency: string;
  price_type: PriceType;
  date: string;
  lower_bound: number | null;
  upper_bound: number | null;
  confidence: Confidence | null;
  precision_level: PrecisionLevel;
  methodology: string;
  sources: SourceRef[];
  source_record_id: string | null;
  last_updated: string | null;
  evidence: Record<string, unknown>;
}

export interface Transaction {
  id: number;
  date: string;
  price: number;
  currency: string;
  property_type: string | null;
  tenure: string | null;
  new_build: boolean | null;
  floor_area_sqm: number | null;
  price_per_sqm: number | null;
  address: string | null;
  postcode: string | null;
  source_key: string;
  source_record_id: string;
}

export interface PropertySummary {
  id: number;
  latitude: number;
  longitude: number;
  coordinate_precision: CoordinatePrecision;
  position_is_approximate: boolean;
  address_short: string | null;
  postcode: string | null;
  property_type: string | null;
  price: Price;
}

export interface AreaStat {
  area_level: string;
  area_code: string;
  area_name: string;
  latitude: number;
  longitude: number;
  year: number;
  median_price: number;
  p25_price: number | null;
  p75_price: number | null;
  median_price_per_sqm: number | null;
  transaction_count: number;
  currency: string;
  precision_level: PrecisionLevel;
  /** Span of sales the median covers; wider than one year for live tiers. */
  window_from_year: number | null;
  window_to_year: number | null;
  growth_1y_pct: number | null;
  is_forecast: boolean;
  confidence: Confidence | null;
  /** For a forecast, the market whose index was projected. */
  forecast_market: string | null;
}

export interface MapResponse {
  status: DataStatus;
  message: string | null;
  tier: MapTier;
  year: number;
  is_future: boolean;
  is_historical: boolean;
  currency: string | null;
  areas: AreaStat[];
  properties: PropertySummary[];
  truncated: boolean;
  total_available: number | null;
  attributions: string[];
}

export interface BBox {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface SearchResult {
  id: string;
  display_name: string;
  name: string;
  latitude: number;
  longitude: number;
  kind: string;
  country_iso2: string | null;
  country_name: string | null;
  bbox: BBox | null;
  suggested_zoom: number;
  has_property_data: boolean;
  coverage_note: string | null;
}

export interface PropertyAddress {
  line: string | null;
  saon: string | null;
  paon: string | null;
  street: string | null;
  locality: string | null;
  town: string | null;
  district: string | null;
  county: string | null;
  postcode: string | null;
  country_iso2: string;
}

export interface PropertyCharacteristics {
  property_type: string | null;
  tenure: string | null;
  bedrooms: number | null;
  bathrooms: number | null;
  habitable_rooms: number | null;
  floor_area_sqm: number | null;
  lot_area_sqm: number | null;
  year_built: number | null;
  new_build_at_sale: boolean | null;
  source: string | null;
}

export interface PropertyDetail {
  id: number;
  address: PropertyAddress;
  latitude: number | null;
  longitude: number | null;
  coordinate_precision: CoordinatePrecision | null;
  position_is_approximate: boolean;
  characteristics: PropertyCharacteristics;
  selected_year: number;
  selected_year_price: Price | null;
  selected_year_status: DataStatus;
  selected_year_message: string | null;
  current_estimate: Price | null;
  last_transaction: Transaction | null;
  transactions: Transaction[];
  sources: SourceRef[];
  data_updated_at: string | null;
}

export interface PricePoint {
  year: number;
  date: string;
  price: Price;
}

export interface PriceHistory {
  property_id: number;
  currency: string;
  points: PricePoint[];
  status: DataStatus;
  message: string | null;
}

export interface Comparable {
  transaction_id: number;
  latitude: number | null;
  longitude: number | null;
  distance_km: number;
  distance_miles: number;
  sold_price: number;
  currency: string;
  sold_date: string;
  property_type: string | null;
  floor_area_sqm: number | null;
  bedrooms: number | null;
  address_short: string | null;
  similarity: number;
  index_adjusted_price: number | null;
}

export interface CoverageEntry {
  provider_key: string;
  country_iso2: string;
  country_name: string | null;
  region_code: string | null;
  region_name: string | null;
  transaction_level_data: boolean;
  property_characteristics: boolean;
  market_index: boolean;
  forecast_supported: boolean;
  max_precision: PrecisionLevel;
  coordinate_precision: CoordinatePrecision | null;
  historical_from: string | null;
  historical_to: string | null;
  currency_code: string | null;
  notes: string | null;
  sources: SourceRef[];
}

export interface CoverageResponse {
  status: DataStatus;
  message: string | null;
  country_iso2: string | null;
  country_name: string | null;
  entry: CoverageEntry | null;
  min_year: number | null;
  max_data_year: number | null;
  max_forecast_year: number | null;
  current_year: number | null;
}

export interface ValuationResult {
  status: DataStatus;
  message: string | null;
  estimated_price: number | null;
  low_estimate: number | null;
  high_estimate: number | null;
  currency: string | null;
  confidence: Confidence | null;
  precision_level: PrecisionLevel;
  valuation_date: string | null;
  method: string | null;
  model_version: string | null;
  comparable_count: number;
  data_sources: SourceRef[];
  evidence: Record<string, unknown>;
  computed_at: string | null;
}
