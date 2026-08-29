"""Typed application errors mapped onto HTTP responses (§30, §36)."""
from __future__ import annotations

from ..models.enums import DataStatus


class AppError(Exception):
    status_code = 500
    data_status = DataStatus.PROVIDER_ERROR
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **extra: object) -> None:
        self.message = message or self.default_message
        self.extra = extra
        super().__init__(self.message)

    def payload(self) -> dict:
        return {
            "status": self.data_status.value,
            "message": self.message,
            "detail": self.extra or None,
        }


class BadRequest(AppError):
    status_code = 400
    data_status = DataStatus.PROVIDER_ERROR
    default_message = "The request was invalid."


class NotFound(AppError):
    status_code = 404
    data_status = DataStatus.NO_DATA
    default_message = "The requested resource does not exist."


class UnsupportedLocation(AppError):
    """No provider covers this jurisdiction — a legitimate, expected answer."""

    status_code = 200
    data_status = DataStatus.UNSUPPORTED_LOCATION
    default_message = (
        "Property price data is not currently available for this location."
    )


class InsufficientEvidence(AppError):
    """A provider covers this place, but the evidence is too thin to publish."""

    status_code = 200
    data_status = DataStatus.INSUFFICIENT_EVIDENCE
    default_message = (
        "There is not enough reliable local evidence to produce a value here."
    )


class ProviderError(AppError):
    """An upstream failure. MUST NOT be reported to the user as 'no data'."""

    status_code = 502
    data_status = DataStatus.PROVIDER_ERROR
    default_message = (
        "We couldn't retrieve property data right now. Please try again."
    )


class OutOfRange(AppError):
    status_code = 200
    data_status = DataStatus.OUT_OF_RANGE
    default_message = "The requested year is outside the available data range."
