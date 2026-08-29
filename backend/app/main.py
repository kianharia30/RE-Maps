"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import get_settings
from .core.errors import AppError
from .db import close_async_pool, fetch_all, fetch_one, open_async_pool
from .models.enums import DataStatus

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("remaps")

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_async_pool()
    # Warm the provider registry so a cold first request is not slower.
    from .providers import registry

    providers = registry.all_providers()
    log.info("providers registered: %s", [p.country_iso2 for p in providers])
    if not settings.epc_enabled:
        log.info(
            "EPC enrichment disabled (no EPC_API_EMAIL/EPC_API_KEY): UK floor "
            "area and room counts will be unavailable. Nothing is substituted."
        )
    yield
    await close_async_pool()


app = FastAPI(
    title="RE-Maps API",
    description=(
        "Residential property prices on a world map, from official open data. "
        "Every price carries its type, precision, confidence and source. Where "
        "no reliable data exists the API says so rather than estimating."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# --- error handling ---------------------------------------------------------
#
# The critical distinction (§36): an application-level "no data here" answer is
# a 200 with a NO_DATA status, while a genuine failure is a 5xx with a
# PROVIDER_ERROR status. The frontend renders completely different messages, so
# a crashed provider is never reported to the user as "data not available".


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        log.warning("%s on %s: %s", type(exc).__name__, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "status": DataStatus.PROVIDER_ERROR.value,
            "message": "Too many requests. Please slow down.",
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "status": DataStatus.PROVIDER_ERROR.value,
            "message": (
                "We couldn't retrieve property data right now. Please try again."
            ),
        },
    )


# --- routes ----------------------------------------------------------------

# Imported here rather than at the top of the file: the route modules import
# `app.providers`, which imports the provider implementations, which import
# this module's settings. Deferring the import until the app object exists
# breaks that cycle.
from .api.routes import coverage, location, property, search  # noqa: E402
from .api.routes import map as map_routes  # noqa: E402

app.include_router(search.router)
app.include_router(coverage.router)
app.include_router(map_routes.router)
app.include_router(property.router)
app.include_router(location.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict:
    """Liveness plus a summary of what is actually loaded.

    Row counts come from the planner's `reltuples` estimate rather than
    `count(*)`: an exact count is a full scan, which on a multi-million-row
    transactions table turns a liveness probe into a slow query (and, under
    concurrent bulk load, one that trips the statement timeout). Small
    reference tables are counted exactly because it is free and callers rely
    on those being precise.
    """
    try:
        rows = await fetch_all(
            """
            SELECT relname,
                   GREATEST(0, reltuples)::bigint AS estimate
            FROM pg_class
            WHERE relname IN ('transactions','properties','postcodes',
                              'market_indices','area_stats')
              AND relkind = 'r'
            """
        )
        exact = await fetch_one(
            """
            SELECT (SELECT count(*) FROM countries)         AS countries,
                   (SELECT count(*) FROM provider_coverage) AS coverage_rows,
                   (SELECT count(*) FROM data_sources)      AS data_sources
            """
        )
    except Exception:
        log.exception("health check: database unreachable")
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"status": "degraded", "database": "unreachable"},
        )

    estimates = {r["relname"]: int(r["estimate"]) for r in rows}
    return {
        "status": "ok",
        "database": "ok",
        "epc_enrichment": settings.epc_enabled,
        "row_estimates": estimates,
        "counts": {k: int(v) for k, v in (exact or {}).items()},
    }
