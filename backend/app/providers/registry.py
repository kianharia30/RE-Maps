"""Provider resolution: coordinates/country -> provider instance.

Registration is explicit rather than magic import scanning, so the set of
supported jurisdictions is auditable in one place. There is deliberately NO
fallback provider: an unregistered country yields None and the caller takes
the honest NO_DATA path (§20).
"""
from __future__ import annotations

from ..core.jurisdiction import Jurisdiction, resolve_bbox, resolve_point
from .base import PropertyDataProvider

_PROVIDERS: dict[str, PropertyDataProvider] = {}


def register(provider: PropertyDataProvider) -> PropertyDataProvider:
    _PROVIDERS[provider.country_iso2] = provider
    return provider


def _bootstrap() -> None:
    if _PROVIDERS:
        return
    # Imported lazily to avoid a circular import at module load.
    from .fr.provider import FrancePropertyProvider
    from .uk.provider import UKPropertyProvider

    register(UKPropertyProvider())
    register(FrancePropertyProvider())


def for_country(country_iso2: str | None) -> PropertyDataProvider | None:
    if not country_iso2:
        return None
    _bootstrap()
    return _PROVIDERS.get(country_iso2.upper())


def all_providers() -> list[PropertyDataProvider]:
    _bootstrap()
    return list(_PROVIDERS.values())


async def for_point(lon: float, lat: float) -> tuple[Jurisdiction | None, PropertyDataProvider | None]:
    j = await resolve_point(lon, lat)
    return j, for_country(j.country_iso2 if j else None)


async def for_bbox(
    west: float, south: float, east: float, north: float
) -> tuple[Jurisdiction | None, PropertyDataProvider | None]:
    j = await resolve_bbox(west, south, east, north)
    return j, for_country(j.country_iso2 if j else None)
