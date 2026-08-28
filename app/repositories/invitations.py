"""邀请码仓库：原子消费（见 docs/DATABASE.md §10.1）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from .models import InvitationCode


def _now() -> datetime:
    return utc_now()


class InvitationRepository:
    def atomic_consume(self, session: Session, invitation_id: int) -> bool:
        """剩余次数原子 +1；影响 1 行才表示消费成功。"""
        result = session.execute(
            update(InvitationCode)
            .where(
                InvitationCode.id == invitation_id,
                InvitationCode.deleted_at.is_(None),
                InvitationCode.revoked_at.is_(None),
                (InvitationCode.expires_at.is_(None)) | (InvitationCode.expires_at > _now()),
                InvitationCode.used_count < InvitationCode.max_uses,
            )
            .values(used_count=InvitationCode.used_count + 1, updated_at=_now())
        )
        return result.rowcount == 1
