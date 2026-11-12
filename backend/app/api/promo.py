"""Área do promotor: gera códigos de pacote extra (com QR) dentro de uma cota diária.
Escreve só `redeem_code` e a auditoria; não mexe em figurinha, pacote de ninguém nem configuração."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import promoter_user
from app.db import get_db
from app.models import PackType, RedeemCode, User
from app.services.albums import current_album
from app.services.codes import generate_code
from app.services.core import audit, cfg, lock_user, now

router = APIRouter(prefix="/promo", tags=["promo"])


def _used(db: Session, user: User) -> int:
    since = now() - timedelta(hours=24)
    return db.scalar(
        select(func.count()).select_from(RedeemCode).where(RedeemCode.created_by == user.id, RedeemCode.created_at >= since)
    ) or 0


class CodesIn(BaseModel):
    pack_type_id: int
    count: int = Field(ge=1, le=200)
    label: str = Field(default="", max_length=80)
    expires_at: AwareDatetime | None = None


@router.get("/overview")
def overview(user: User = Depends(promoter_user), db: Session = Depends(get_db)):
    album = current_album(db, user)
    limit = int(cfg(db, "promo.codes_per_day"))
    packs = (
        db.scalars(select(PackType).where(PackType.album_id == album.id).order_by(PackType.id)).all() if album else []
    )
    rows = db.execute(
        select(RedeemCode, User.nickname)
        .outerjoin(User, User.id == RedeemCode.redeemed_by)
        .where(RedeemCode.created_by == user.id)
        .order_by(RedeemCode.id.desc())
        .limit(300)
    ).all()
    return {
        "album": {"id": album.id, "title": album.title} if album else None,
        "limit": limit,
        "left": max(0, limit - _used(db, user)),
        "pack_types": [{"id": p.id, "slug": p.slug, "name": p.name, "stickers_per_pack": p.stickers_per_pack} for p in packs],
        "codes": [
            {
                "id": c.id, "code": c.code, "label": c.label, "pack_type": c.pack_type.name, "created_at": c.created_at,
                "expires_at": c.expires_at, "redeemed_by": nickname, "redeemed_at": c.redeemed_at,
            }
            for c, nickname in rows
        ],
    }


@router.post("/codes", status_code=201)
def create_codes(body: CodesIn, user: User = Depends(promoter_user), db: Session = Depends(get_db)):
    album = current_album(db, user)
    if album is None:
        raise HTTPException(409, "Não há álbum em andamento para gerar códigos.")
    pack = db.get(PackType, body.pack_type_id)
    if pack is None or pack.album_id != album.id:
        raise HTTPException(404, "Tipo de pacote não encontrado.")
    if body.expires_at is not None and body.expires_at <= now():
        raise HTTPException(400, "A validade precisa estar no futuro.")
    lock_user(db, user.id)  # duas gerações ao mesmo tempo não furam a cota
    limit = int(cfg(db, "promo.codes_per_day"))
    left = limit - _used(db, user)
    if body.count > left:
        raise HTTPException(429, f"Cota diária: você ainda pode gerar {max(left, 0)} de {limit} códigos nas últimas 24 horas.")
    codes = [generate_code() for _ in range(body.count)]
    db.add_all(
        RedeemCode(
            code=code, album_id=album.id, pack_type_id=pack.id, label=body.label, expires_at=body.expires_at, created_by=user.id
        )
        for code in codes
    )
    audit(db, user.id, "promo.codes_create", count=body.count, label=body.label, pack_type_id=pack.id, album_id=album.id)
    db.commit()
    return {"codes": codes, "left": left - body.count}
