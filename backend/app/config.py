"""Application configuration. All secrets come from the environment (§42)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql://localhost:5432/remaps",
        alias="DATABASE_URL",
    )
    db_pool_min: int = Field(default=2, alias="DB_POOL_MIN")
    db_pool_max: int = Field(default=10, alias="DB_POOL_MAX")
    db_statement_timeout_ms: int = Field(default=15000, alias="DB_STATEMENT_TIMEOUT_MS")

    # --- server -------------------------------------------------------------
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    rate_limit: str = Field(default="120/minute", alias="RATE_LIMIT")

    # --- geocoding (§10) ----------------------------------------------------
    # Nominatim's usage policy requires an identifying User-Agent with contact
    # details and caps automated use at ~1 request/second. We honour both.
    geocoder: str = Field(default="nominatim", alias="GEOCODER")
    nominatim_url: str = Field(
        default="https://nominatim.openstreetmap.org", alias="NOMINATIM_URL"
    )
    nominatim_email: str = Field(default="", alias="NOMINATIM_EMAIL")
    geocoder_user_agent: str = Field(
        default="RE-Maps/0.1 (self-hosted property price map)",
        alias="GEOCODER_USER_AGENT",
    )
    geocoder_min_interval_s: float = Field(default=1.0, alias="GEOCODER_MIN_INTERVAL_S")
    geocode_cache_days: int = Field(default=30, alias="GEOCODE_CACHE_DAYS")

    # --- optional: EPC property characteristics (§47) -----------------------
    # Free but requires a GOV.UK One Login account. Without it, UK properties
    # simply have no floor area / habitable-room data; nothing is invented.
    epc_api_base: str = Field(
        default="https://epc.opendatacommunities.org/api/v1", alias="EPC_API_BASE"
    )
    epc_api_email: str = Field(default="", alias="EPC_API_EMAIL")
    epc_api_key: str = Field(default="", alias="EPC_API_KEY")

    # --- modelling ----------------------------------------------------------
    avm_model_version: str = Field(default="cs-avm-1.2.0", alias="AVM_MODEL_VERSION")
    forecast_model_version: str = Field(
        default="shrunk-drift-1.2.0", alias="FORECAST_MODEL_VERSION"
    )
    forecast_max_horizon_years: int = Field(default=10, alias="FORECAST_MAX_HORIZON_YEARS")

    # --- data paths ---------------------------------------------------------
    data_dir: Path = Field(default=REPO_ROOT / "data", alias="DATA_DIR")

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolutise_data_dir(cls, v: Path) -> Path:
        """Resolve a relative DATA_DIR against the repository root, so the
        ingest CLI and the API agree no matter which directory they run from."""
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def epc_enabled(self) -> bool:
        return bool(self.epc_api_email and self.epc_api_key)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
