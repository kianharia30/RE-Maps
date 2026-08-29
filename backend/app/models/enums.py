"""Shared enumerations for the normalised property data model.

These names are mirrored one-for-one in the TypeScript types consumed by the
frontend (see `frontend/src/types/api.ts`), so a change here is a breaking
API change.
"""
from __future__ import annotations

from enum import Enum


class PriceType(str, Enum):
    """The four (plus one) categories of price the product must never conflate."""

    TRANSACTION = "TRANSACTION"                    # a genuine recorded sale
    HISTORICAL_ESTIMATE = "HISTORICAL_ESTIMATE"    # modelled value at a past date
    CURRENT_ESTIMATE = "CURRENT_ESTIMATE"          # modelled value today
    FORECAST = "FORECAST"                          # statistical projection
    REGIONAL_STATISTIC = "REGIONAL_STATISTIC"      # an area aggregate, not a dwelling


class PrecisionLevel(str, Enum):
    """How precisely a figure describes an individual dwelling (§6)."""

    EXACT_TRANSACTION = "EXACT_TRANSACTION"   # Level A
    PROPERTY_ESTIMATE = "PROPERTY_ESTIMATE"   # Level B
    STREET_POSTCODE = "STREET_POSTCODE"       # Level C
    NEIGHBOURHOOD = "NEIGHBOURHOOD"           # Level D
    CITY_REGIONAL = "CITY_REGIONAL"           # Level E
    NONE = "NONE"                             # Level F


class CoordinatePrecision(str, Enum):
    """How precisely a coordinate locates the dwelling (§33).

    ``POSTCODE`` explicitly means "a postcode centroid, not this building".
    """

    PROPERTY = "PROPERTY"
    PARCEL = "PARCEL"
    ADDRESS = "ADDRESS"
    POSTCODE = "POSTCODE"
    NEIGHBOURHOOD = "NEIGHBOURHOOD"
    REGION = "REGION"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"


class PropertyType(str, Enum):
    # Jurisdictions differ in how finely they classify dwellings. France's DVF
    # records only "Maison" vs "Appartement", so a French house is `house` --
    # calling it `detached` would be inventing a fact the source does not state.
    HOUSE = "house"
    DETACHED = "detached"
    SEMI_DETACHED = "semi_detached"
    TERRACED = "terraced"
    FLAT = "flat"
    OTHER = "other"


class Tenure(str, Enum):
    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    UNKNOWN = "unknown"


class DataStatus(str, Enum):
    """Distinguishes genuinely-absent data from failures (§36).

    The frontend renders a different message for each of these, so that a
    crashed provider is never reported to the user as "no data exists".
    """

    OK = "OK"
    NO_DATA = "NO_DATA"                          # no provider covers this place
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # provider exists, evidence too thin
    UNSUPPORTED_LOCATION = "UNSUPPORTED_LOCATION"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    OUT_OF_RANGE = "OUT_OF_RANGE"                # year outside the data's coverage


class MapTier(str, Enum):
    """Zoom-dependent aggregation tiers (§9)."""

    WORLD = "WORLD"
    COUNTRY = "COUNTRY"
    REGION = "REGION"
    CITY = "CITY"
    NEIGHBOURHOOD = "NEIGHBOURHOOD"
    POSTCODE = "POSTCODE"
    STREET = "STREET"
    PROPERTY = "PROPERTY"
