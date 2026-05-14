"""Declarative SQLAlchemy base.

All models inherit from :class:`Base`. We use the typed ``DeclarativeBase``
form (SQLAlchemy 2.x) so models are mypy-friendly.

Naming convention follows Alembic's recommendation to make autogenerate
migrations idempotent across DB engines.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Project-wide declarative base. Every ORM model inherits from this."""

    metadata = metadata_obj
