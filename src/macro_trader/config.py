"""Layered configuration.

Resolution order (later wins):
    1. ``config/base.yaml``
    2. ``config/<env>.yaml``      (env taken from ``APP_ENV``, default ``dev``)
    3. Environment variables / ``.env``

YAML provides defaults and structure. Environment variables override
sensitive values (DB passwords, JWT secret, API keys). The combined dict is
validated by Pydantic models defined here, so accessing an undefined field
raises at import time, not at the point of first use.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


# ----------------------------------------------------------------------
# Sectional schemas
# ----------------------------------------------------------------------
class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    name: str = "macro_trader"
    user: str = "macro"
    password: str = ""
    test_name: str = "macro_trader_test"
    url: str = ""
    pool_size: int = 5
    max_overflow: int = 10
    echo_sql: bool = False

    @model_validator(mode="after")
    def _materialize_url(self) -> DatabaseSettings:
        if not self.url:
            self.url = (
                f"postgresql+psycopg://{self.user}:{self.password}"
                f"@{self.host}:{self.port}/{self.name}"
            )
        return self


class APISettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


class AuthSettings(BaseModel):
    jwt_secret_key: str = "change_me_in_production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30
    admin_email: str = "admin@example.com"
    admin_password: str = "change_me"


class DagsterSettings(BaseModel):
    home: str = "./dagster_home"
    port: int = 3000


class FrontendSettings(BaseModel):
    dev_server_url: str = "http://localhost:5173"


class ClaudeAPISettings(BaseModel):
    api_key: str = ""
    model: str = "claude-opus-4-7"


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "console"  # or "json"
    file_path: str | None = None


class DataSourcesSettings(BaseModel):
    """Free-tier external data sources."""

    fred_api_key: str = ""
    eia_api_key: str = ""
    usda_api_key: str = ""
    noaa_api_key: str = ""
    alpha_vantage_api_key: str = ""
    quandl_api_key: str = ""


class MethodsSettings(BaseModel):
    """Cross-cutting methods-framework settings."""

    default_promotion: dict[str, Any] = Field(
        default_factory=lambda: {
            "min_shadow_period_days": 180,
            "min_comparison_runs": 12,
            "improvement_threshold": 0.05,
        }
    )
    comparison_schedule: dict[str, str] = Field(
        default_factory=lambda: {"default_cron": "0 6 * * *"}
    )


class SignalsSettings(BaseModel):
    """Signal-layer cross-cutting settings.

    Most signal-method parameters live in the methods themselves
    (constructor kwargs threaded through the registry). This block
    captures cross-cutting policy that the API and runners need.

    ``designated_per_component`` lets operators override the registry's
    PRODUCTION/BASELINE resolution when a component intentionally has
    multiple BASELINE methods (e.g. trend has three SMAs + one
    ensemble; the dashboard wants to feature the ensemble). Resolution
    order in :func:`macro_trader.signals.designated.resolve`:
    config override -> PRODUCTION -> BASELINE -> first-registered.
    """

    model_config = {"extra": "ignore"}

    designated_per_component: dict[str, str] = Field(default_factory=dict)


# ----------------------------------------------------------------------
# Top-level Settings
# ----------------------------------------------------------------------
class Settings(BaseSettings):
    """Top-level merged settings."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    log_format: str = Field(default="console", alias="APP_LOG_FORMAT")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    api: APISettings = Field(default_factory=APISettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    dagster: DagsterSettings = Field(default_factory=DagsterSettings)
    frontend: FrontendSettings = Field(default_factory=FrontendSettings)
    claude_api: ClaudeAPISettings = Field(default_factory=ClaudeAPISettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    data_sources: DataSourcesSettings = Field(default_factory=DataSourcesSettings)
    methods: MethodsSettings = Field(default_factory=MethodsSettings)
    signals: SignalsSettings = Field(default_factory=SignalsSettings)


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------
def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} must contain a top-level mapping")
    return loaded


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_env(env_var: str = "APP_ENV", default: str = "dev") -> str:
    import os

    return os.environ.get(env_var, default).lower()


def _env_overrides() -> dict[str, Any]:
    """Pull a small set of well-known env vars and graft them into the merged
    YAML dict before Pydantic validation. Anything missing falls back to YAML."""
    import os

    def opt(name: str) -> str | None:
        v = os.environ.get(name)
        return v if v not in (None, "") else None

    overrides: dict[str, Any] = {
        "database": {},
        "auth": {},
        "api": {},
        "dagster": {},
        "claude_api": {},
        "data_sources": {},
        "frontend": {},
        "logging": {},
    }

    # Database
    db = overrides["database"]
    for src, dest in [
        ("POSTGRES_HOST", "host"),
        ("POSTGRES_PORT", "port"),
        ("POSTGRES_DB", "name"),
        ("POSTGRES_USER", "user"),
        ("POSTGRES_PASSWORD", "password"),
        ("POSTGRES_TEST_DB", "test_name"),
        ("DATABASE_URL", "url"),
    ]:
        v = opt(src)
        if v is not None:
            db[dest] = int(v) if dest == "port" else v

    # API
    api = overrides["api"]
    if (v := opt("API_HOST")) is not None:
        api["host"] = v
    if (v := opt("API_PORT")) is not None:
        api["port"] = int(v)
    if (v := opt("API_RELOAD")) is not None:
        api["reload"] = v.lower() in ("1", "true", "yes")
    if (v := opt("API_CORS_ORIGINS")) is not None:
        api["cors_origins"] = [s.strip() for s in v.split(",") if s.strip()]

    # Auth
    auth = overrides["auth"]
    if (v := opt("JWT_SECRET_KEY")) is not None:
        auth["jwt_secret_key"] = v
    if (v := opt("JWT_ALGORITHM")) is not None:
        auth["jwt_algorithm"] = v
    if (v := opt("JWT_ACCESS_TOKEN_EXPIRE_MINUTES")) is not None:
        auth["access_token_expire_minutes"] = int(v)
    if (v := opt("JWT_REFRESH_TOKEN_EXPIRE_DAYS")) is not None:
        auth["refresh_token_expire_days"] = int(v)
    if (v := opt("ADMIN_EMAIL")) is not None:
        auth["admin_email"] = v
    if (v := opt("ADMIN_PASSWORD")) is not None:
        auth["admin_password"] = v

    # Dagster
    dag = overrides["dagster"]
    if (v := opt("DAGSTER_HOME")) is not None:
        dag["home"] = v
    if (v := opt("DAGSTER_PORT")) is not None:
        dag["port"] = int(v)

    # Claude
    cl = overrides["claude_api"]
    if (v := opt("ANTHROPIC_API_KEY")) is not None:
        cl["api_key"] = v
    if (v := opt("CLAUDE_MODEL")) is not None:
        cl["model"] = v

    # Data sources
    ds = overrides["data_sources"]
    if (v := opt("FRED_API_KEY")) is not None:
        ds["fred_api_key"] = v
    if (v := opt("EIA_API_KEY")) is not None:
        ds["eia_api_key"] = v
    if (v := opt("USDA_API_KEY")) is not None:
        ds["usda_api_key"] = v
    if (v := opt("NOAA_API_KEY")) is not None:
        ds["noaa_api_key"] = v
    if (v := opt("ALPHA_VANTAGE_API_KEY")) is not None:
        ds["alpha_vantage_api_key"] = v
    if (v := opt("QUANDL_API_KEY")) is not None:
        ds["quandl_api_key"] = v

    # Frontend
    fe = overrides["frontend"]
    if (v := opt("VITE_API_URL")) is not None:
        fe["dev_server_url"] = v.replace("/api/v1", "").rstrip("/") or "http://localhost:5173"

    # Logging
    lg = overrides["logging"]
    if (v := opt("APP_LOG_LEVEL")) is not None:
        lg["level"] = v
    if (v := opt("APP_LOG_FORMAT")) is not None:
        lg["format"] = v

    return overrides


def _load_merged_config(env: str | None = None) -> dict[str, Any]:
    env = env or _resolve_env()
    base = _read_yaml(CONFIG_DIR / "base.yaml")
    layer = _read_yaml(CONFIG_DIR / f"{env}.yaml")
    merged = _deep_merge(base, layer)
    merged = _deep_merge(merged, _env_overrides())
    merged.setdefault("env", env)
    return merged


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings. Tests can call :func:`reset_settings_cache`.

    In ``APP_ENV=test`` we deliberately skip ``.env`` loading so a developer's
    real local secrets / API keys / DB URLs never bleed into the test harness.
    Tests rely on ``conftest.py`` to set every var they need explicitly.
    """
    import os

    if os.environ.get("APP_ENV", "dev").lower() != "test":
        # Make sure .env is loaded into os.environ for pydantic-settings discovery.
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env", override=False)

    merged = _load_merged_config(os.environ.get("APP_ENV", "dev").lower())
    return Settings.model_validate(merged)


def reset_settings_cache() -> None:
    """Test hook: drop the cached settings."""
    get_settings.cache_clear()
