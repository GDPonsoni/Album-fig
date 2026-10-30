"""Atalhos temporários de teste. Só existem fora de produção (ver app/main.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import verified_user
from app.db import get_db
from app.models import PackType, RedeemCode, User
from app.services.codes import generate_code
from app.services.core import audit
from app.services.packs import grant_pack

router = APIRouter(prefix="/dev", tags=["dev"])


class PackIn(BaseModel):
    slug: str


def _pack_type(db: Session, slug: str) -> PackType:
    pack_type = db.scalar(select(PackType).where(PackType.slug == slug))
    if pack_type is None:
        raise HTTPException(404, "Tipo de pacote não encontrado.")
    return pack_type


@router.post("/codes", status_code=201)
def create_code(body: PackIn, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    pack_type = _pack_type(db, body.slug)
    code = generate_code()
    db.add(RedeemCode(code=code, pack_type_id=pack_type.id, label="dev"))
    audit(db, user.id, "dev.code_create", pack_type_id=pack_type.id)
    db.commit()
    return {"code": code}


@router.post("/packs", status_code=201)
def add_pack(body: PackIn, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    grant_pack(db, user.id, _pack_type(db, body.slug).id, "admin", "dev")
    db.commit()
    return {"ok": True}
