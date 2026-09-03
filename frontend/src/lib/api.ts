/**
 * Typed API client.
 *
 * Two behaviours matter for correctness rather than convenience:
 *
 * 1. **Request cancellation.** Panning the map fires a request per viewport
 *    change. Each new call aborts the previous one for the same key, so a slow
 *    earlier response can never overwrite a newer one (§31).
 * 2. **Status is not an error.** A NO_DATA / UNSUPPORTED_LOCATION response is a
 *    successful answer that the UI renders as a message. Only transport and
 *    5xx failures throw, so "the server broke" is never shown as
 *    "no data exists here" (§36).
 */
import type {
  Comparable,
  CoverageEntry,
  CoverageResponse,
  MapResponse,
  PriceHistory,
  PropertyDetail,
  SearchResult,
  SourceRef,
  ValuationResult,
} from "@/types/api";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly isNetwork = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Thrown when a request was superseded by a newer one. Callers ignore it. */
export class AbortedError extends Error {
  constructor() {
    super("aborted");
    this.name = "AbortedError";
  }
}

const controllers = new Map<string, AbortController>();

async function request<T>(
  path: string,
  params: Record<string, string | number | undefined> = {},
  abortKey?: string,
): Promise<T> {
  const url = new URL(API_BASE + path);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }

  let signal: AbortSignal | undefined;
  if (abortKey) {
    controllers.get(abortKey)?.abort();
    const controller = new AbortController();
    controllers.set(abortKey, controller);
    signal = controller.signal;
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), { signal, headers: { Accept: "application/json" } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw new AbortedError();
    throw new ApiError(
      "We couldn't reach the property data service. Check your connection and try again.",
      0,
      true,
    );
  } finally {
    if (abortKey && controllers.get(abortKey)?.signal === signal) controllers.delete(abortKey);
  }

  if (!response.ok) {
    let message = "We couldn't retrieve property data right now. Please try again.";
    try {
      const body = await response.json();
      if (typeof body?.message === "string") message = body.message;
    } catch {
      /* keep the default */
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; counts: Record<string, number> }>("/api/health"),

  search: (q: string, limit = 8) =>
    request<SearchResult[]>("/api/search", { q, limit }, "search"),

  coverageAt: (lat: number, lon: number) =>
    request<CoverageResponse>("/api/coverage/at", { lat, lon }, "coverage"),

  coverageIndex: () =>
    request<{
      supported: CoverageEntry[];
      /** Places registered to record that no open data exists, with the reason. */
      known_absences: CoverageEntry[];
      generated_at: string | null;
    }>("/api/coverage"),

  sources: () => request<SourceRef[]>("/api/sources"),

  mapPrices: (bbox: string, zoom: number, year: number, segment = "all") =>
    request<MapResponse>("/api/map/prices", { bbox, zoom, year, segment }, "map"),

  property: (id: number, year: number) =>
    request<PropertyDetail>(`/api/property/${id}`, { year }, `property-${id}`),

  history: (id: number) => request<PriceHistory>(`/api/property/${id}/history`, {}, `history-${id}`),

  comparables: (id: number, year: number, limit = 12) =>
    request<Comparable[]>(`/api/property/${id}/comparables`, { year, limit }, `comps-${id}`),

  valuation: (id: number, year: number) =>
    request<ValuationResult>(`/api/property/${id}/valuation`, { year }, `val-${id}`),

  marketHistory: (lat: number, lon: number, segment = "all") =>
    request<{
      status: string;
      area_name: string | null;
      points: {
        period: string;
        average_price: number | null;
        index_value: number | null;
        pct_change_12m: number | null;
      }[];
    }>("/api/location/market-history", { lat, lon, segment }, "market-history"),
};
