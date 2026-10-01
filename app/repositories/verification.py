from datetime import datetime

from sqlalchemy import select

from app.db.base import utcnow
from app.models import EmailVerification, VerificationMethod, VerificationStatus
from app.repositories.base import BaseRepository


class EmailVerificationRepository(BaseRepository[EmailVerification]):
    """Append-only history of verification attempts (update/delete still available)."""

    model = EmailVerification

    def record(
        self,
        email_id: int,
        method: VerificationMethod,
        status: VerificationStatus = VerificationStatus.UNKNOWN,
        *,
        reason: str | None = None,
        mx_host: str | None = None,
        smtp_code: int | None = None,
        is_catch_all: bool | None = None,
        provider: str | None = None,
        checked_at: datetime | None = None,
    ) -> EmailVerification:
        return self.add(
            EmailVerification(
                email_id=email_id,
                method=method,
                status=status,
                reason=reason,
                mx_host=mx_host,
                smtp_code=smtp_code,
                is_catch_all=is_catch_all,
                provider=provider,
                checked_at=checked_at or utcnow(),
            )
        )

    def list_for_email(self, email_id: int, *, limit: int = 100) -> list[EmailVerification]:
        """Newest first."""
        stmt = (
            select(EmailVerification)
            .where(EmailVerification.email_id == email_id)
            .order_by(EmailVerification.checked_at.desc(), EmailVerification.id.desc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def latest_for_email(self, email_id: int) -> EmailVerification | None:
        rows = self.list_for_email(email_id, limit=1)
        return rows[0] if rows else None
