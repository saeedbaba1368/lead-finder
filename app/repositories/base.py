"""Generic repository. Repositories flush but never commit; the caller owns the transaction."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.interfaces import LoaderOption

from app.db.base import Base
from app.repositories.errors import NotFoundError

_PROTECTED = {"id", "created_at", "updated_at"}


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- reads ---------------------------------------------------------
    def _one(self, stmt: Select[Any], options: Sequence[LoaderOption] = ()) -> ModelT | None:
        """Run a select; when eager-load options are given, also fill already-loaded instances."""
        if options:
            stmt = stmt.options(*options).execution_options(populate_existing=True)
        return self.session.scalars(stmt).unique().first()

    def get(self, id: int, *, options: Sequence[LoaderOption] = ()) -> ModelT | None:
        return self._one(select(self.model).where(self.model.id == id), options)  # type: ignore[attr-defined]

    def require(self, id: int, *, options: Sequence[LoaderOption] = ()) -> ModelT:
        obj = self.get(id, options=options)
        if obj is None:
            raise NotFoundError(self.model.__name__, id)
        return obj

    def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).order_by(self.model.id).limit(limit).offset(offset)  # type: ignore[attr-defined]
        return list(self.session.scalars(stmt))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model)) or 0

    # -- writes --------------------------------------------------------
    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        self.session.flush()
        return obj

    def update(self, obj: ModelT, **values: Any) -> ModelT:
        """Set mapped column attributes and flush. Unknown/protected fields raise ValueError."""
        allowed = {c.key for c in inspect(self.model).column_attrs} - _PROTECTED
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"cannot update {self.model.__name__} fields: {sorted(unknown)}")
        for key, value in values.items():
            setattr(obj, key, value)
        self.session.flush()
        return obj

    def delete(self, obj: ModelT) -> None:
        self.session.delete(obj)
        self.session.flush()

    def delete_by_id(self, id: int) -> bool:
        obj = self.get(id)
        if obj is None:
            return False
        self.delete(obj)
        return True
