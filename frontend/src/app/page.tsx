"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";

import Attribution from "@/components/Attribution";
import MapControls, {
  type Segment,
} from "@/components/MapControls";
import PropertyPanel from "@/components/PropertyPanel";
import SearchBar from "@/components/SearchBar";
import StatusBanner from "@/components/StatusBanner";
import { AbortedError, ApiError, api } from "@/lib/api";
import type {
  AreaStat,
  CoverageResponse,
  DataStatus,
  MapResponse,
  SearchResult,
} from "@/types/api";

// MapLibre touches `window` at import time, so it must not be server-rendered.
const MapCanvas = dynamic(() => import("@/components/MapCanvas"), {
  ssr: false,
  loading: () => <div className="absolute inset-0 bg-[#eef1f5]" />,
});

const THIS_YEAR = new Date().getFullYear();

interface View {
  bbox: string;
  zoom: number;
  centre: [number, number];
}

export default function Home() {
  const [view, setView] = useState<View | null>(null);
  const [year, setYear] = useState<number>(THIS_YEAR);
  const [mapData, setMapData] = useState<MapResponse | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{ status: DataStatus; message: string } | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [flyTo, setFlyTo] = useState<
    { lat: number; lon: number; zoom: number; bbox?: [number, number, number, number] } | null
  >(null);
  const [segment, setSegment] = useState<Segment>("all");

  const urlHydrated = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reloadRef = useRef<() => void>(() => {});

  /* --- hydrate from the URL once (§55: shareable map links) --------------- */
  useEffect(() => {
    if (urlHydrated.current) return;
    urlHydrated.current = true;
    const p = new URLSearchParams(window.location.search);
    const y = Number(p.get("year"));
    if (Number.isFinite(y) && y > 1900 && y < 2100) setYear(y);
    const lat = Number(p.get("lat"));
    const lon = Number(p.get("lon"));
    const z = Number(p.get("z"));
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      setFlyTo({ lat, lon, zoom: Number.isFinite(z) ? z : 15 });
    }
    const m = p.get("mode");
    const seg = p.get("type") as Segment | null;
    if (seg) setSegment(seg);
    const pid = Number(p.get("property"));
    if (Number.isFinite(pid) && pid > 0) setSelectedId(pid);
  }, []);

  /* --- keep the URL in sync so the view is shareable ---------------------- */
  useEffect(() => {
    if (!view || !urlHydrated.current) return;
    const p = new URLSearchParams();
    p.set("lat", view.centre[1].toFixed(5));
    p.set("lon", view.centre[0].toFixed(5));
    p.set("z", view.zoom.toFixed(1));
    p.set("year", String(year));
    if (segment !== "all") p.set("type", segment);
    if (selectedId) p.set("property", String(selectedId));
    window.history.replaceState(null, "", `?${p.toString()}`);
  }, [view, year, segment, selectedId]);

  /* --- coverage for the current centre ------------------------------------ */
  useEffect(() => {
    if (!view) return;
    let cancelled = false;
    (async () => {
      try {
        const c = await api.coverageAt(view.centre[1], view.centre[0]);
        if (!cancelled) setCoverage(c);
      } catch (err) {
        if (err instanceof AbortedError) return;
        // A coverage failure must not blank the map; the map request carries
        // its own status.
      }
    })();
    return () => {
      cancelled = true;
    };
    // Re-fetch only when the centre moves enough to plausibly change country.
  }, [view?.centre[0].toFixed(1), view?.centre[1].toFixed(1)]);

  /* --- the map data request, debounced ----------------------------------- */
  const load = useCallback(
    async (v: View, y: number, seg: Segment) => {
      setLoading(true);
      try {
        const data = await api.mapPrices(v.bbox, v.zoom, y, seg);
        setMapData(data);
        setFailure(null);
      } catch (err) {
        if (err instanceof AbortedError) return;
        // A transport/5xx failure is reported as a FAILURE, never as "no data".
        setFailure({
          status: "PROVIDER_ERROR",
          message:
            err instanceof ApiError
              ? err.message
              : "We couldn't retrieve property data right now. Please try again.",
        });
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!view) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    // 260 ms: long enough that a continuous pan issues one request at the end,
    // short enough to feel immediate. Older requests are aborted in api.ts.
    debounceRef.current = setTimeout(() => load(view, year, segment), 260);
    reloadRef.current = () => load(view, year, segment);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [view, year, segment, load]);

  const onViewChange = useCallback((bbox: string, zoom: number, centre: [number, number]) => {
    setView({ bbox, zoom, centre });
  }, []);

  const onSearchSelect = useCallback((result: SearchResult) => {
    setSelectedId(null);
    setFlyTo({
      lat: result.latitude,
      lon: result.longitude,
      zoom: result.suggested_zoom,
      bbox: result.bbox
        ? [result.bbox.west, result.bbox.south, result.bbox.east, result.bbox.north]
        : undefined,
    });
  }, []);

  const onZoomToArea = useCallback((area: AreaStat) => {
    setFlyTo({ lat: area.latitude, lon: area.longitude, zoom: 15.6 });
  }, []);


  /* --- timeline bounds come from real coverage, never hard-coded ---------- */
  const minYear = coverage?.min_year ?? THIS_YEAR - 6;
  const maxDataYear = coverage?.max_data_year ?? THIS_YEAR;
  const maxYear = coverage?.max_forecast_year ?? THIS_YEAR + 4;
  const currentYear = coverage?.current_year ?? THIS_YEAR;

  // Keep the selected year inside the range the current jurisdiction supports.
  useEffect(() => {
    if (!coverage) return;
    if (year < minYear) setYear(minYear);
    else if (year > maxYear) setYear(maxYear);
  }, [coverage, minYear, maxYear, year]);

  const banner = failure ?? (mapData && mapData.status !== "OK"
    ? { status: mapData.status, message: mapData.message ?? "" }
    : null);

  const resultCount = (mapData?.properties.length ?? 0) + (mapData?.areas.length ?? 0);

  return (
    <main className="rm-shell">
      <MapCanvas
        data={mapData}
        year={year}
        selectedPropertyId={selectedId}
        flyTo={flyTo}
        onViewChange={onViewChange}
        onSelectProperty={setSelectedId}
        onZoomToArea={onZoomToArea}
        onMapReady={() => {}}
      />

      {/* --- top chrome ----------------------------------------------------- */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex flex-col gap-2.5
                      p-3 md:flex-row md:items-start md:gap-4 md:p-4">
        <SearchBar onSelect={onSearchSelect} />

        <div className="flex min-w-0 flex-1 justify-center">
          <TimelineMount
            minYear={minYear}
            maxYear={maxYear}
            currentYear={currentYear}
            maxDataYear={maxDataYear}
            value={year}
            onChange={setYear}
            disabled={coverage?.status !== "OK"}
          />
        </div>

        <div className="hidden md:block">
          <MapControls
                            segment={segment}
            onSegmentChange={setSegment}
            tier={mapData?.tier ?? null}
            loading={loading}
            resultCount={resultCount}
            truncated={mapData?.truncated ?? false}
          />
        </div>
      </div>

      {/* --- status ---------------------------------------------------------- */}
      {banner && (
        <div className="pointer-events-none absolute inset-x-0 bottom-24 z-20 flex justify-center
                        px-3 md:bottom-10">
          <StatusBanner
            status={banner.status}
            message={banner.message}
            onRetry={() => reloadRef.current()}
          />
        </div>
      )}

      {/* --- property panel -------------------------------------------------- */}
      {selectedId != null && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 max-h-[72%]
                        md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-auto md:p-4">
          <PropertyPanel
            propertyId={selectedId}
            year={year}
            onClose={() => setSelectedId(null)}
            onSelectYear={setYear}
          />
        </div>
      )}

      {/* --- mobile controls ------------------------------------------------- */}
      <div className="absolute bottom-3 left-3 z-20 md:hidden">
        <MapControls
                  segment={segment}
          onSegmentChange={setSegment}
          tier={mapData?.tier ?? null}
          loading={loading}
          resultCount={resultCount}
          truncated={mapData?.truncated ?? false}
        />
      </div>

      {/* --- attribution ----------------------------------------------------- */}
      <div className="pointer-events-none absolute bottom-1.5 left-1/2 z-10 -translate-x-1/2
                      px-2 md:left-auto md:right-[92px] md:translate-x-0">
        <Attribution attributions={mapData?.attributions ?? []} />
      </div>
    </main>
  );
}

/** Wrapper so the timeline can be lazily imported without SSR mismatch. */
const Timeline = dynamic(() => import("@/components/Timeline"), { ssr: false });

function TimelineMount(props: React.ComponentProps<typeof Timeline>) {
  return <Timeline {...props} />;
}
