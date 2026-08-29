"""Nominatim geocoder.

Compliance with the OSM Foundation's Nominatim usage policy is built in, not
optional:

  * every request carries an identifying User-Agent (and `email=` when set),
  * a process-wide lock enforces a minimum interval between requests,
  * every response is cached in Postgres, so repeated searches for the same
    place cost nothing upstream.

If you expect real traffic, run your own Nominatim or Photon instance and point
NOMINATIM_URL at it — see docs/DATA_SOURCES.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from ..config import get_settings
from ..core.errors import ProviderError
from ..db import execute, fetch_one
from ..models.geo import BoundingBox, SearchResult
from .base import DEFAULT_ZOOM, ZOOM_BY_KIND, Geocoder

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")

# Nominatim `type`/`class` values mapped onto our coarse place kinds.
_KIND_MAP = {
    "country": "country", "state": "state", "region": "region",
    "province": "region", "county": "county", "state_district": "county",
    "city": "city", "municipality": "city", "borough": "city",
    "town": "town", "village": "village", "hamlet": "village",
    "suburb": "suburb", "quarter": "suburb", "neighbourhood": "neighbourhood",
    "city_district": "suburb", "district": "suburb",
    "postcode": "postcode", "postal_code": "postcode",
    "residential": "street", "road": "street", "pedestrian": "street",
    "house": "address", "building": "building", "house_number": "address",
    "yes": "building", "apartments": "building", "detached": "building",
    "terrace": "building", "semidetached_house": "building",
}


def _normalise(query: str) -> str:
    return _WS.sub(" ", query.strip().lower())


class _Throttle:
    """Process-wide minimum interval between upstream calls."""

    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        wait = self._min - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)

    async def __aexit__(self, *exc: object) -> None:
        self._last = time.monotonic()
        self._lock.release()


class NominatimGeocoder(Geocoder):
    key = "nominatim"
    source_key = "osm_nominatim"

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.nominatim_url.rstrip("/")
        self._email = s.nominatim_email
        self._headers = {"User-Agent": s.geocoder_user_agent, "Accept": "application/json"}
        self._throttle = _Throttle(s.geocoder_min_interval_s)
        self._cache_days = s.geocode_cache_days

    # --- cache --------------------------------------------------------------

    async def _cached(self, kind: str, query_norm: str) -> list | dict | None:
        row = await fetch_one(
            """
            SELECT payload FROM geocode_cache
            WHERE provider = %s AND kind = %s AND query_norm = %s
              AND created_at > now() - (%s || ' days')::interval
            """,
            (self.key, kind, query_norm, str(self._cache_days)),
        )
        return row["payload"] if row else None

    async def _store(self, kind: str, query_norm: str, payload: list | dict) -> None:
        await execute(
            """
            INSERT INTO geocode_cache (provider, kind, query_norm, payload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (provider, kind, query_norm)
            DO UPDATE SET payload = EXCLUDED.payload, created_at = now()
            """,
            (self.key, kind, query_norm, json.dumps(payload)),
        )

    # --- upstream -----------------------------------------------------------

    async def _get(self, path: str, params: dict) -> list | dict:
        if self._email:
            params["email"] = self._email
        async with self._throttle:
            try:
                async with httpx.AsyncClient(timeout=12.0, headers=self._headers) as client:
                    resp = await client.get(f"{self._base}{path}", params=params)
            except httpx.HTTPError as exc:
                log.warning("nominatim transport error: %s", exc)
                raise ProviderError("The location search service is unavailable.") from exc
        if resp.status_code == 429:
            raise ProviderError(
                "The location search service is rate-limiting us. Please retry shortly."
            )
        if resp.status_code >= 400:
            log.warning("nominatim %s: %s", resp.status_code, resp.text[:200])
            raise ProviderError("The location search service returned an error.")
        return resp.json()

    # --- interface ----------------------------------------------------------

    async def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        q = _normalise(query)
        if not q:
            return []
        cache_key = f"{q}|{limit}"
        payload = await self._cached("search", cache_key)
        if payload is None:
            payload = await self._get(
                "/search",
                {
                    "q": query,
                    "format": "jsonv2",
                    "limit": str(limit),
                    "addressdetails": "1",
                    "extratags": "0",
                },
            )
            await self._store("search", cache_key, payload)
        return [r for r in (self._to_result(item) for item in payload) if r]

    async def reverse(self, lat: float, lon: float) -> SearchResult | None:
        cache_key = f"{lat:.5f},{lon:.5f}"
        payload = await self._cached("reverse", cache_key)
        if payload is None:
            payload = await self._get(
                "/reverse",
                {"lat": str(lat), "lon": str(lon), "format": "jsonv2", "addressdetails": "1"},
            )
            await self._store("reverse", cache_key, payload)
        if not isinstance(payload, dict) or "lat" not in payload:
            return None
        return self._to_result(payload)

    # --- mapping ------------------------------------------------------------

    @staticmethod
    def _to_result(item: dict) -> SearchResult | None:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            return None

        addr = item.get("address") or {}
        raw_type = (item.get("type") or "").lower()
        addr_type = (item.get("addresstype") or "").lower()
        kind = _KIND_MAP.get(addr_type) or _KIND_MAP.get(raw_type) or "place"

        bbox = None
        bb = item.get("boundingbox")
        if bb and len(bb) == 4:
            try:
                south, north, west, east = (float(v) for v in bb)
                if west != east and south <= north:
                    bbox = BoundingBox(
                        west=west, south=south, east=east, north=north
                    ).clamped()
            except (TypeError, ValueError):
                bbox = None

        iso2 = (addr.get("country_code") or "").upper() or None
        display = item.get("display_name") or item.get("name") or "Unknown"
        name = item.get("name") or display.split(",")[0]

        return SearchResult(
            id=f"{item.get('osm_type','n')}/{item.get('osm_id', item.get('place_id',''))}",
            display_name=display,
            name=name,
            latitude=lat,
            longitude=lon,
            kind=kind,
            country_iso2=iso2,
            country_name=addr.get("country"),
            bbox=bbox,
            suggested_zoom=ZOOM_BY_KIND.get(kind, DEFAULT_ZOOM),
        )


def get_geocoder() -> Geocoder:
    """Factory honouring the GEOCODER setting."""
    s = get_settings()
    if s.geocoder == "nominatim":
        return NominatimGeocoder()
    raise ValueError(f"Unknown geocoder: {s.geocoder!r}")
