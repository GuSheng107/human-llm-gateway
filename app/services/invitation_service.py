"""邀请码用例：创建、治理、明文匹配与原子消费。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..core.security import generate_invitation_code, verify_invitation_code
from ..core.time import utc_now
from ..domain.enums import AuditAction
from ..domain.errors import DomainError, DomainErrorCode
from ..repositories.invitations import InvitationRepository
from ..repositories.models import InvitationCode
from ..repositories.system import AuditRepository

MAX_INVITATION_USES = 1000
_MAX_EXPIRY_YEARS = 5


def _validate_expiry(expires_at: datetime | None) -> None:
    if expires_at is None:
        return
    now = utc_now()
    if expires_at <= now:
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED, "过期时间必须晚于当前时间", status_code=400
        )
    if expires_at > now + timedelta(days=365 * _MAX_EXPIRY_YEARS):
        raise DomainError(
            DomainErrorCode.VALIDATION_FAILED,
            f"过期时间不能晚于 {_MAX_EXPIRY_YEARS} 年后",
            status_code=400,
        )


class InvitationService:
    def __init__(self) -> None:
        self.invitations = InvitationRepository()
        self.audit = AuditRepository()

    def create(
        self,
        session: Session,
        *,
        actor_user_id: int,
        note: str | None,
        expires_at: datetime | None,
        max_uses: int,
    ) -> tuple[InvitationCode, str]:
        if max_uses <= 0:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED, "最大使用次数必须大于 0", status_code=400
            )
        if max_uses > MAX_INVITATION_USES:
            raise DomainError(
                DomainErrorCode.VALIDATION_FAILED,
                f"最大使用次数不能超过 {MAX_INVITATION_USES}",
                status_code=400,
            )
        _validate_expiry(expires_at)
        code, prefix, code_hash = generate_invitation_code()
        row = InvitationCode(
            created_by_user_id=actor_user_id,
            code_hash=code_hash,
            code_prefix=prefix,
            note=note.strip() or None if note is not None else None,
            expires_at=expires_at,
            max_uses=max_uses,
        )
        self.invitations.add(session, row)
        session.flush()
        self.audit.add(
            session,
            action=AuditAction.INVITATION_CREATED,
            resource_type="invitation",
            resource_id=str(row.id),
            actor_user_id=actor_user_id,
            metadata={"fields": ["note", "expires_at", "max_uses"]},
        )
        return row, code

    def get(self, session: Session, invitation_id: int) -> InvitationCode:
        row = self.invitations.get(session, invitation_id)
        if row is None:
            raise DomainError(DomainErrorCode.NOT_FOUND, "邀请码不存在", status_code=404)
        return row

    def update(
        self,
        session: Session,
        row: InvitationCode,
        *,
        actor_user_id: int,
        fields: dict,
    ) -> InvitationCode:
        changed: list[str] = []
        if "note" in fields:
            note = fields["note"]
            row.note = note.strip() or None if note is not None else None
            changed.append("note")
        if "expires_at" in fields:
            _validate_expiry(fields["expires_at"])
            row.expires_at = fields["expires_at"]
            changed.append("expires_at")
        if "max_uses" in fields:
            max_uses = fields["max_uses"]
            if max_uses <= 0 or max_uses > MAX_INVITATION_USES or max_uses < row.used_count:
                raise DomainError(
                    DomainErrorCode.VALIDATION_FAILED,
                    f"最大使用次数需在 1 到 {MAX_INVITATION_USES} 之间且不小于已使用次数",
                    status_code=400,
                )
            row.max_uses = max_uses
            changed.append("max_uses")
        if changed:
            self.audit.add(
                session,
                action=AuditAction.INVITATION_UPDATED,
                resource_type="invitation",
                resource_id=str(row.id),
                actor_user_id=actor_user_id,
                metadata={"fields": changed},
            )
        session.flush()
        return row

    def revoke(
        self, session: Session, row: InvitationCode, *, actor_user_id: int
    ) -> InvitationCode:
        if self.invitations.revoke(session, row.id):
            self.audit.add(
                session,
                action=AuditAction.INVITATION_REVOKED,
                resource_type="invitation",
                resource_id=str(row.id),
                actor_user_id=actor_user_id,
            )
            session.flush()
            session.refresh(row)
        return row

    def delete(self, session: Session, row: InvitationCode, *, actor_user_id: int) -> None:
        if row.revoked_at is None:
            raise DomainError(
                DomainErrorCode.CONFLICT, "只有已撤销的邀请码可以删除", status_code=409
            )
        if self.invitations.soft_delete(session, row.id):
            self.audit.add(
                session,
                action=AuditAction.INVITATION_DELETED,
                resource_type="invitation",
                resource_id=str(row.id),
                actor_user_id=actor_user_id,
            )
            session.flush()

    def match_plaintext(self, session: Session, plaintext: str) -> InvitationCode:
        normalized = plaintext.upper().strip()
        if len(normalized) < 8:
            raise self._invalid()
        for row in self.invitations.find_candidates(session, normalized[:8]):
            if verify_invitation_code(normalized, row.code_hash):
                return row
        raise self._invalid()

    @staticmethod
    def _invalid() -> DomainError:
        return DomainError(DomainErrorCode.INVALID_INVITATION, "邀请码无效", status_code=400)
