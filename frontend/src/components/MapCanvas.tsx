"use client";

import maplibregl, { type LngLatBoundsLike, type Map as MlMap } from "maplibre-gl";
import { useEffect, useRef } from "react";

import { formatCompact } from "@/lib/currency";
import type { AreaStat, MapResponse, PropertySummary } from "@/types/api";

const MAP_STYLE =
  process.env.NEXT_PUBLIC_MAP_STYLE ?? "https://tiles.openfreemap.org/styles/liberty";

/** Teardrop pin, matching the reference: solid dark body, hollow centre. */
const PIN_SVG = `
<svg class="rm-marker__pin" width="26" height="34" viewBox="0 0 26 34" fill="none"
     xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <path d="M13 33.2C13 33.2 24.6 20.6 24.6 12.9C24.6 6.3 19.4 1 13 1C6.6 1 1.4 6.3 1.4 12.9C1.4 20.6 13 33.2 13 33.2Z"
        fill="#0F172A" stroke="#fff" stroke-width="1.6"/>
  <circle cx="13" cy="12.7" r="4.6" fill="none" stroke="#fff" stroke-width="2.1"/>
</svg>`;

export interface MapView {
  centre: [number, number];
  zoom: number;
}

interface Props {
  data: MapResponse | null;
  year: number;
  mode: "markers" | "heatmap";
  heatmapMetric: "median_price" | "median_price_per_sqm" | "growth_1y_pct";
  selectedPropertyId: number | null;
  flyTo: { lat: number; lon: number; zoom: number; bbox?: LngLatBoundsLike } | null;
  onViewChange: (bbox: string, zoom: number, centre: [number, number]) => void;
  onSelectProperty: (id: number) => void;
  onZoomToArea: (area: AreaStat) => void;
  onMapReady: () => void;
}

export default function MapCanvas({
  data,
  year,
  mode,
  heatmapMetric,
  selectedPropertyId,
  flyTo,
  onViewChange,
  onSelectProperty,
  onZoomToArea,
  onMapReady,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
  const readyRef = useRef(false);

  // Handlers are read through a ref so the map is created exactly once and
  // never torn down when a parent re-render produces new closures.
  const handlers = useRef({ onViewChange, onSelectProperty, onZoomToArea, onMapReady });
  handlers.current = { onViewChange, onSelectProperty, onZoomToArea, onMapReady };

  /* --- create the map once ------------------------------------------------ */
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [-0.7594, 52.0407], // Milton Keynes — inside the covered market
      zoom: 15.2,
      minZoom: 1.4,
      maxZoom: 19,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchZoomRotate: true,
      fadeDuration: 120,
    });
    mapRef.current = map;
    map.touchZoomRotate.disableRotation();

    // Surface basemap failures instead of silently showing an empty map. A
    // missing sprite icon is cosmetic; a failing tile source is not, and
    // without this handler MapLibre swallows both.
    map.on("error", (e) => {
      const message = (e as unknown as { error?: Error }).error?.message ?? String(e);
      if (/could not be loaded|styleimagemissing/i.test(message)) return;
      console.error("[map]", message, e);
    });

    if (process.env.NODE_ENV !== "production") {
      (window as unknown as { __rmMap?: MlMap }).__rmMap = map;
    }

    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }),
      "bottom-right",
    );
    map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), "bottom-right");

    const emit = () => {
      const b = map.getBounds();
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
        .map((v) => v.toFixed(6))
        .join(",");
      const c = map.getCenter();
      handlers.current.onViewChange(bbox, map.getZoom(), [c.lng, c.lat]);
    };

    map.on("load", () => {
      readyRef.current = true;
      // Heatmap source is created empty and fed later, so toggling modes never
      // has to add/remove layers mid-interaction.
      map.addSource("rm-heat", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      map.addLayer({
        id: "rm-heat-layer",
        type: "heatmap",
        source: "rm-heat",
        paint: {
          "heatmap-weight": ["coalesce", ["get", "w"], 0.4],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 4, 0.7, 14, 2.1],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 4, 18, 10, 34, 16, 62],
          "heatmap-opacity": 0.72,
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(30,64,175,0)",
            0.15, "rgba(37,99,235,0.55)",
            0.35, "rgba(14,165,233,0.68)",
            0.55, "rgba(250,204,21,0.75)",
            0.75, "rgba(249,115,22,0.82)",
            1, "rgba(190,18,60,0.9)",
          ],
        },
        layout: { visibility: "none" },
      });
      map.resize();
      emit();
      handlers.current.onMapReady();

      // Replay a camera move that arrived before the style was ready.
      const queued = pendingFlyTo.current;
      if (queued) {
        pendingFlyTo.current = null;
        if (queued.bbox) {
          map.fitBounds(queued.bbox, {
            padding: { top: 140, bottom: 90, left: 90, right: 90 },
            maxZoom: queued.zoom,
            duration: 0,
          });
        } else {
          map.jumpTo({ center: [queued.lon, queued.lat], zoom: queued.zoom });
        }
        emit();
      }
    });

    map.on("moveend", emit);
    map.on("zoomend", emit);
    // `resize` changes the visible bounds but fires NO move/zoom event, so
    // without this the app would keep requesting the viewport the map had
    // before it was sized — which on first load is a tiny sliver of the screen
    // and returns almost nothing.
    map.on("resize", emit);

    // MapLibre measures its container synchronously in the constructor. If the
    // element has not been laid out yet (or the stylesheet lands a tick later)
    // it locks in a wrong drawing-buffer size and renders into a corner of the
    // viewport. Observing the container and calling resize() makes the map
    // correct on first paint and on every subsequent layout change — window
    // resize, device rotation, and the panel opening on mobile.
    const observer = new ResizeObserver(() => map.resize());
    observer.observe(containerRef.current);
    // One resize on the next frame covers the initial-layout case even in
    // browsers that fire no observer callback for the first measurement.
    requestAnimationFrame(() => map.resize());

    return () => {
      observer.disconnect();
      markersRef.current.forEach((m) => m.remove());
      markersRef.current.clear();
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, []);

  /* --- imperative camera moves from search ------------------------------- */
  //
  // A camera move issued before the style has finished loading is discarded by
  // MapLibre, which is exactly when the initial move from a shared URL arrives.
  // Pending moves are therefore queued and replayed on load.
  const pendingFlyTo = useRef<Props["flyTo"]>(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    if (!readyRef.current) {
      pendingFlyTo.current = flyTo;
      return;
    }
    if (flyTo.bbox) {
      map.fitBounds(flyTo.bbox, {
        padding: { top: 140, bottom: 90, left: 90, right: 90 },
        maxZoom: flyTo.zoom,
        duration: 1100,
      });
    } else {
      map.flyTo({ center: [flyTo.lon, flyTo.lat], zoom: flyTo.zoom, duration: 1100, essential: true });
    }
  }, [flyTo]);

  /* --- markers ------------------------------------------------------------ */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    const existing = markersRef.current;
    const wanted = new Set<string>();

    if (mode === "markers" && data?.status === "OK") {
      for (const p of data.properties as PropertySummary[]) {
        const key = `p:${p.id}`;
        wanted.add(key);
        const selected = selectedPropertyId === p.id;
        const label = formatCompact(p.price.value, p.price.currency);
        const existingMarker = existing.get(key);
        if (existingMarker) {
          updatePropertyMarker(existingMarker, label, selected, p.price.price_type);
          existingMarker.setLngLat([p.longitude, p.latitude]);
        } else {
          const marker = buildPropertyMarker(p, label, selected, () =>
            handlers.current.onSelectProperty(p.id),
          );
          marker.addTo(map);
          existing.set(key, marker);
        }
      }
      for (const a of data.areas as AreaStat[]) {
        const key = `a:${a.area_level}:${a.area_code}`;
        wanted.add(key);
        if (!existing.has(key)) {
          const marker = buildAreaMarker(a, () => handlers.current.onZoomToArea(a));
          marker.addTo(map);
          existing.set(key, marker);
        } else {
          existing.get(key)!.setLngLat([a.longitude, a.latitude]);
        }
      }
    }

    // Remove only what is no longer wanted, so panning does not flash markers
    // that stay in view.
    for (const [key, marker] of existing) {
      if (!wanted.has(key)) {
        marker.remove();
        existing.delete(key);
      }
    }
  }, [data, mode, selectedPropertyId, year]);

  /* --- heatmap ------------------------------------------------------------ */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const source = map.getSource("rm-heat") as maplibregl.GeoJSONSource | undefined;
    if (!source) return;

    if (mode !== "heatmap" || data?.status !== "OK") {
      map.setLayoutProperty("rm-heat-layer", "visibility", "none");
      source.setData({ type: "FeatureCollection", features: [] });
      return;
    }

    const points: { lon: number; lat: number; value: number | null }[] = [
      ...data.areas.map((a) => ({
        lon: a.longitude,
        lat: a.latitude,
        value:
          heatmapMetric === "median_price"
            ? a.median_price
            : heatmapMetric === "median_price_per_sqm"
              ? a.median_price_per_sqm
              : a.growth_1y_pct,
      })),
      ...data.properties.map((p) => ({
        lon: p.longitude,
        lat: p.latitude,
        value: heatmapMetric === "growth_1y_pct" ? null : p.price.value,
      })),
    ].filter((p) => p.value != null);

    // Normalise within the current viewport so the ramp always uses its full
    // range, whatever the local price level.
    const values = points.map((p) => p.value as number).sort((a, b) => a - b);
    const lo = values[Math.floor(values.length * 0.05)] ?? 0;
    const hi = values[Math.floor(values.length * 0.95)] ?? 1;
    const span = hi - lo || 1;

    source.setData({
      type: "FeatureCollection",
      features: points.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { w: Math.min(1, Math.max(0.05, ((p.value as number) - lo) / span)) },
      })),
    });
    map.setLayoutProperty("rm-heat-layer", "visibility", "visible");
  }, [data, mode, heatmapMetric]);

  return <div ref={containerRef} className="rm-map-root" aria-label="Property price map" />;
}

/* --- marker construction -------------------------------------------------- */

const TYPE_TINT: Record<string, string> = {
  TRANSACTION: "var(--color-sold)",
  CURRENT_ESTIMATE: "var(--color-estimate)",
  HISTORICAL_ESTIMATE: "var(--color-historical)",
  FORECAST: "var(--color-forecast)",
  REGIONAL_STATISTIC: "var(--color-regional)",
};

function buildPropertyMarker(
  p: PropertySummary,
  label: string,
  selected: boolean,
  onClick: () => void,
): maplibregl.Marker {
  const el = document.createElement("div");
  el.className = `rm-marker${selected ? " rm-marker--selected" : ""}`;
  el.innerHTML = `<div class="rm-marker__pill"></div>${PIN_SVG}`;
  const pill = el.firstElementChild as HTMLElement;
  pill.textContent = label;
  applyTint(pill, p.price.price_type, selected);

  el.setAttribute("role", "button");
  el.setAttribute("tabindex", "0");
  el.setAttribute(
    "aria-label",
    `${p.address_short ?? "Property"} — ${label} (${p.price.price_type.replace(/_/g, " ").toLowerCase()})`,
  );
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick();
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  });

  return new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([
    p.longitude,
    p.latitude,
  ]);
}

function updatePropertyMarker(
  marker: maplibregl.Marker,
  label: string,
  selected: boolean,
  priceType: string,
) {
  const el = marker.getElement();
  el.className = `rm-marker${selected ? " rm-marker--selected" : ""}`;
  const pill = el.querySelector(".rm-marker__pill") as HTMLElement | null;
  if (pill) {
    pill.textContent = label;
    applyTint(pill, priceType, selected);
  }
}

/**
 * A thin left border in the price-type colour. Deliberately subtle: the pill
 * must stay legible against a busy basemap, but a forecast must never look
 * identical to a recorded sale (§2).
 */
function applyTint(pill: HTMLElement, priceType: string, selected: boolean) {
  const tint = TYPE_TINT[priceType] ?? "var(--color-sold)";
  pill.style.boxShadow = selected
    ? `var(--shadow-marker), inset 3px 0 0 0 #fff`
    : `var(--shadow-marker), inset 3px 0 0 0 ${tint}`;
}

function buildAreaMarker(a: AreaStat, onClick: () => void): maplibregl.Marker {
  const el = document.createElement("div");
  el.className = "rm-marker rm-marker--area";
  const pill = document.createElement("div");
  pill.className = "rm-marker__pill";

  const value = document.createElement("span");
  value.textContent = formatCompact(a.median_price, a.currency);
  pill.appendChild(value);

  const sub = document.createElement("span");
  sub.className = "rm-marker__sub";
  sub.textContent = a.is_forecast ? `${a.area_name} · forecast` : a.area_name;
  pill.appendChild(sub);

  applyTint(pill, a.is_forecast ? "FORECAST" : "REGIONAL_STATISTIC", false);
  el.appendChild(pill);

  el.setAttribute("role", "button");
  el.setAttribute("tabindex", "0");
  el.setAttribute(
    "aria-label",
    `${a.area_name}: median ${formatCompact(a.median_price, a.currency)} from ${a.transaction_count} sales in ${a.year}. Zoom in.`,
  );
  const activate = (e: Event) => {
    e.stopPropagation();
    onClick();
  };
  el.addEventListener("click", activate);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") activate(e);
  });

  return new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([
    a.longitude,
    a.latitude,
  ]);
}
