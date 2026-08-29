"""Geocoder abstraction (§10) so the provider can be swapped without touching
the search route or the frontend."""
from __future__ import annotations

import abc

from ..models.geo import SearchResult


class Geocoder(abc.ABC):
    key: str
    source_key: str

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        ...

    @abc.abstractmethod
    async def reverse(self, lat: float, lon: float) -> SearchResult | None:
        ...


# How far to zoom for each class of place. Chosen so that a city search lands
# at the aggregation tier where that city's neighbourhoods are visible, and an
# address search lands at the property tier.
ZOOM_BY_KIND: dict[str, float] = {
    "country": 5.0,
    "state": 7.0,
    "region": 7.5,
    "county": 9.0,
    "city": 11.5,
    "town": 13.0,
    "village": 13.5,
    "suburb": 14.0,
    "neighbourhood": 14.5,
    "postcode": 15.5,
    "street": 16.5,
    "address": 17.5,
    "building": 17.5,
    "place": 13.0,
}
DEFAULT_ZOOM = 13.0
