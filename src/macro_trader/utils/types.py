"""Shared structural type aliases."""

from __future__ import annotations

from typing import Any

# Recursive JSON type — Pydantic / FastAPI / SQLAlchemy JSONB all converge here.
JSONValue = None | bool | int | float | str | list[Any] | dict[str, Any]
JSONDict = dict[str, JSONValue]
JSONList = list[JSONValue]
