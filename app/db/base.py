"""Declarative base, naming conventions, and shared column mixins."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Deterministic constraint names keep future Alembic migrations stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# BIGINT everywhere, but plain INTEGER on SQLite so PKs auto-increment there.
BigInt = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={getattr(self, 'id', None)}>"


class IdMixin:
    """Surrogate integer primary key (first column)."""

    id: Mapped[int] = mapped_column(BigInt, primary_key=True, sort_order=-1)


class TimestampMixin:
    """created_at / updated_at (timezone-aware; last columns)."""

    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            default=utcnow,
            server_default=func.now(),
            nullable=False,
            sort_order=9998,
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            default=utcnow,
            onupdate=utcnow,
            server_default=func.now(),
            nullable=False,
            sort_order=9999,
        )
