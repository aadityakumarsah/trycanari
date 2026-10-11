"""Declarative base and the column helpers every table shares."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit names, so a migration can refer to any constraint.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def created_now() -> Mapped[datetime]:
    """A timezone-aware timestamp set by the database when the row is inserted."""
    return mapped_column(DateTime(timezone=True), server_default=func.now())
