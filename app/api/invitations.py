"""管理员邀请码治理 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.time import iso_utc, to_naive_utc, utc_now
from ..repositories.invitations import InvitationRepository
from ..repositories.models import InvitationCode, User
from ..services.invitation_service import InvitationService
from .common import StrictModel
from .deps import require_admin

router = APIRouter(prefix="/api/invitations", tags=["invitations"])


class InvitationCreate(StrictModel):
    note: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None
    max_uses: int = Field(default=1, ge=1)


class InvitationUpdate(StrictModel):
    note: str | None = Field(default=None, max_length=255)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)

    @field_validator("max_uses")
    @classmethod
    def reject_null_max_uses(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("最大使用次数不能为空")
        return value


class InvitationView(BaseModel):
    id: str
    code_prefix: str
    note: str | None
    max_uses: int
    used_count: int
    status: str
    expires_at: str | None
    revoked_at: str | None
    created_at: str
    created_by_user_id: str


class InvitationCreated(InvitationView):
    code: str


class InvitationPage(BaseModel):
    items: list[InvitationView]
    page: int
    page_size: int
    total: int


def _status(row: InvitationCode) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= utc_now():
        return "expired"
    if row.used_count >= row.max_uses:
        return "exhausted"
    return "active"


def _view(row: InvitationCode) -> InvitationView:
    return InvitationView(
        id=str(row.id),
        code_prefix=row.code_prefix,
        note=row.note,
        max_uses=row.max_uses,
        used_count=row.used_count,
        status=_status(row),
        expires_at=iso_utc(row.expires_at),
        revoked_at=iso_utc(row.revoked_at),
        created_at=iso_utc(row.created_at) or "",
        created_by_user_id=str(row.created_by_user_id),
    )


@router.get("", response_model=InvitationPage)
def list_invitations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationPage:
    rows, total = InvitationRepository().list_page(
        db, page=page, page_size=page_size, search=search
    )
    return InvitationPage(
        items=[_view(row) for row in rows], page=page, page_size=page_size, total=total
    )


@router.post("", response_model=InvitationCreated, status_code=201)
def create_invitation(
    payload: InvitationCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationCreated:
    row, code = InvitationService().create(
        db,
        actor_user_id=admin.id,
        note=payload.note,
        expires_at=to_naive_utc(payload.expires_at),
        max_uses=payload.max_uses,
    )
    db.commit()
    return InvitationCreated(**_view(row).model_dump(), code=code)


@router.get("/{invitation_id}", response_model=InvitationView)
def get_invitation(
    invitation_id: int,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationView:
    return _view(InvitationService().get(db, invitation_id))


@router.patch("/{invitation_id}", response_model=InvitationView)
def update_invitation(
    invitation_id: int,
    payload: InvitationUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationView:
    fields = payload.model_dump(include=payload.model_fields_set)
    if "expires_at" in fields:
        fields["expires_at"] = to_naive_utc(fields["expires_at"])
    service = InvitationService()
    row = service.update(db, service.get(db, invitation_id), actor_user_id=admin.id, fields=fields)
    db.commit()
    return _view(row)


@router.post("/{invitation_id}/revoke", response_model=InvitationView)
def revoke_invitation(
    invitation_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> InvitationView:
    service = InvitationService()
    row = service.revoke(db, service.get(db, invitation_id), actor_user_id=admin.id)
    db.commit()
    return _view(row)


@router.delete("/{invitation_id}", status_code=204)
def delete_invitation(
    invitation_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    service = InvitationService()
    service.delete(db, service.get(db, invitation_id), actor_user_id=admin.id)
    db.commit()
    return Response(status_code=204)
