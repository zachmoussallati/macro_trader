"""Tests for the layered configuration loader."""

from __future__ import annotations

import os

import pytest

from macro_trader.config import (
    _deep_merge,
    _load_merged_config,
    get_settings,
    reset_settings_cache,
)


@pytest.mark.unit
def test_deep_merge_overrides_leaf_values() -> None:
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}}
    assert _deep_merge(base, override) == {"a": 1, "b": {"c": 99, "d": 3}}


@pytest.mark.unit
def test_deep_merge_keeps_unrelated_keys() -> None:
    base = {"keep": "this", "nested": {"x": 1}}
    override = {"nested": {"y": 2}}
    assert _deep_merge(base, override) == {
        "keep": "this",
        "nested": {"x": 1, "y": 2},
    }


@pytest.mark.unit
def test_load_merged_config_dev_env() -> None:
    """`dev.yaml` should set log level to DEBUG."""
    merged = _load_merged_config(env="dev")
    assert merged["logging"]["level"] == "DEBUG"


@pytest.mark.unit
def test_load_merged_config_test_env() -> None:
    merged = _load_merged_config(env="test")
    assert merged["database"]["name"] == "macro_trader_test"
    assert merged["methods"]["default_promotion"]["min_shadow_period_days"] == 0


@pytest.mark.unit
def test_get_settings_returns_valid_pydantic_object() -> None:
    reset_settings_cache()
    s = get_settings()
    assert s.env in {"dev", "test", "prod"}
    assert s.api.port > 0
    assert isinstance(s.api.cors_origins, list)
    assert s.auth.jwt_algorithm in {"HS256", "RS256"}
    assert s.methods.default_promotion["improvement_threshold"] >= 0


@pytest.mark.unit
def test_database_url_is_assembled_when_not_explicitly_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "macro_trader_test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_settings_cache()
    s = get_settings()
    assert s.database.url == ("postgresql+psycopg://u:p@db.example.com:5433/macro_trader_test")

    # Restore so other tests don't see the override.
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    os.environ.setdefault("POSTGRES_DB", "macro_trader_test")
    reset_settings_cache()
