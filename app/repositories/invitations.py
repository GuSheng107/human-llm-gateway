"""邀请码仓库：原子消费（见 docs/DATABASE.md §10.1）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..core.time import utc_now
from .models import InvitationCode


def _now() -> datetime:
    return utc_now()


class InvitationRepository:
    def get(self, session: Session, invitation_id: int) -> InvitationCode | None:
        return session.execute(
            select(InvitationCode).where(
                InvitationCode.id == invitation_id,
                InvitationCode.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def list_page(
        self,
        session: Session,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[InvitationCode], int]:
        filters = [InvitationCode.deleted_at.is_(None)]
        if search:
            term = search.strip()
            filters.append(
                or_(
                    InvitationCode.code_prefix.ilike(f"%{term}%"),
                    InvitationCode.note.ilike(f"%{term}%"),
                )
            )
        total = (
            session.scalar(select(func.count()).select_from(InvitationCode).where(*filters)) or 0
        )
        rows = list(
            session.scalars(
                select(InvitationCode)
                .where(*filters)
                .order_by(InvitationCode.created_at.desc(), InvitationCode.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def find_candidates(self, session: Session, code_prefix: str) -> list[InvitationCode]:
        return list(
            session.scalars(
                select(InvitationCode).where(
                    InvitationCode.code_prefix == code_prefix,
                    InvitationCode.deleted_at.is_(None),
                )
            )
        )

    def add(self, session: Session, invitation: InvitationCode) -> InvitationCode:
        session.add(invitation)
        return invitation

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

    def revoke(self, session: Session, invitation_id: int) -> bool:
        result = session.execute(
            update(InvitationCode)
            .where(
                InvitationCode.id == invitation_id,
                InvitationCode.deleted_at.is_(None),
                InvitationCode.revoked_at.is_(None),
            )
            .values(revoked_at=_now(), updated_at=_now())
        )
        return result.rowcount == 1

    def soft_delete(self, session: Session, invitation_id: int) -> bool:
        result = session.execute(
            update(InvitationCode)
            .where(
                InvitationCode.id == invitation_id,
                InvitationCode.revoked_at.is_not(None),
                InvitationCode.deleted_at.is_(None),
            )
            .values(deleted_at=_now(), updated_at=_now())
        )
        return result.rowcount == 1
