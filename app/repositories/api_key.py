from datetime import datetime

from sqlalchemy import select

from app.core.security import generate_api_key, hash_api_key
from app.db.base import utcnow
from app.models import ApiKey
from app.repositories.base import BaseRepository


class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    def create_new(self, name: str, *, expires_at: datetime | None = None) -> tuple[ApiKey, str]:
        """Generate a key, store only its hash, and return ``(row, plaintext_secret)``.

        The plaintext is returned exactly once and is not recoverable afterwards.
        """
        generated = generate_api_key()
        key = self.add(
            ApiKey(name=name, key_prefix=generated.key_prefix, key_hash=generated.key_hash, expires_at=expires_at)
        )
        return key, generated.secret

    def get_by_prefix(self, prefix: str) -> ApiKey | None:
        return self.session.scalars(select(ApiKey).where(ApiKey.key_prefix == prefix)).first()

    def get_by_secret(self, secret: str) -> ApiKey | None:
        return self.session.scalars(select(ApiKey).where(ApiKey.key_hash == hash_api_key(secret))).first()

    def get_usable_by_secret(self, secret: str, *, now: datetime | None = None) -> ApiKey | None:
        """The key for this secret if it is active, not revoked and not expired; else None."""
        key = self.get_by_secret(secret)
        return key if key is not None and key.is_usable(now) else None

    def list_keys(self, *, active_only: bool = False, limit: int = 100, offset: int = 0) -> list[ApiKey]:
        stmt = select(ApiKey)
        if active_only:
            stmt = stmt.where(ApiKey.is_active.is_(True), ApiKey.revoked_at.is_(None))
        return list(self.session.scalars(stmt.order_by(ApiKey.id).limit(limit).offset(offset)))

    def revoke(self, key: ApiKey, when: datetime | None = None) -> ApiKey:
        return self.update(key, is_active=False, revoked_at=when or utcnow())

    def touch_last_used(self, key: ApiKey, when: datetime | None = None) -> ApiKey:
        return self.update(key, last_used_at=when or utcnow())
