"""Rastreio para o superadmin: de onde veio um pacote, quem o gerou e abriu, de qual pacote saiu cada
figurinha, por quem ela passou, e quem escreveu e aprovou o que foi publicado."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import normalize_email
from app.api.deps import admin_user
from app.db import get_db
from app.models import ChapterRevision, PackGrant, PackOpeningItem, RedeemCode, Sticker, StickerCopy, User
from app.services import provenance
from app.services.codes import normalize_code

router = APIRouter(prefix="/admin/trace", tags=["admin"], dependencies=[Depends(admin_user)])


def _pack(db: Session, grant: PackGrant, cache: dict) -> dict:
    items = db.scalars(
        select(PackOpeningItem).where(PackOpeningItem.pack_grant_id == grant.id).order_by(PackOpeningItem.position)
    ).all()
    copies = {
        c.origin_position: c
        for c in db.scalars(select(StickerCopy).where(StickerCopy.origin_grant_id == grant.id))
    }
    return {
        **provenance.grant_origin(db, grant, cache),
        "items": [
            {
                "position": i.position, "is_new": i.is_new,
                "copy": provenance.copy_chain(db, copies[i.position], cache) if i.position in copies else None,
                "sticker_id": i.sticker_id,
            }
            for i in items
        ],
    }


@router.get("/pack/{grant_id}")
def pack(grant_id: int, db: Session = Depends(get_db)):
    grant = db.get(PackGrant, grant_id)
    if grant is None:
        raise HTTPException(404, "Registro não encontrado.")
    return _pack(db, grant, {})


@router.get("/code/{code}")
def code(code: str, db: Session = Depends(get_db)):
    row = db.scalar(select(RedeemCode).where(RedeemCode.code == normalize_code(code)))
    if row is None:
        raise HTTPException(404, "Código não encontrado.")
    cache: dict = {}
    grant = db.scalar(select(PackGrant).where(PackGrant.source == "code", PackGrant.source_ref == str(row.id)))
    name = lambda uid: provenance._name(db, cache, uid)  # noqa: E731
    return {
        "code": row.code, "label": row.label, "pack_type": row.pack_type.name, "created_by": name(row.created_by),
        "created_at": row.created_at, "expires_at": row.expires_at, "redeemed_by": name(row.redeemed_by),
        "redeemed_at": row.redeemed_at, "pack": _pack(db, grant, cache) if grant else None,
    }


@router.get("/sticker/{sticker_id}")
def sticker(sticker_id: int, db: Session = Depends(get_db)):
    """Uma figurinha: quem a criou/editou/aprovou/publicou e os exemplares que existem, com a cadeia de cada um."""
    row = db.get(Sticker, sticker_id)
    if row is None:
        raise HTTPException(404, "Registro não encontrado.")
    cache: dict = {}
    name = lambda uid: provenance._name(db, cache, uid)  # noqa: E731
    revisions = []
    if row.content_key:
        for revision in db.scalars(
            select(ChapterRevision).where(ChapterRevision.status.in_(("published", "superseded"))).order_by(ChapterRevision.id)
        ):
            if any(s.get("key") == row.content_key for s in revision.content.get("stickers", [])):
                revisions.append(
                    {
                        "revision_id": revision.id, "number": revision.number, "chapter_id": revision.chapter_id,
                        "author": name(revision.author_id), "contributors": [name(u) for u in revision.contributors or []],
                        "reviewed_by": name(revision.reviewed_by), "published_at": revision.published_at,
                    }
                )
    copies = db.scalars(select(StickerCopy).where(StickerCopy.sticker_id == sticker_id).order_by(StickerCopy.id).limit(200)).all()
    return {
        "sticker": {"id": row.id, "number": row.number, "name": row.name},
        "revisions": revisions,
        "copy_total": provenance.count_copies(db, sticker_id),
        "copies": [provenance.copy_chain(db, c, cache) for c in copies],
    }


@router.get("/user")
def user_trace(email: str, db: Session = Depends(get_db)):
    """Pacotes de uma pessoa (recebidos, de onde vieram, quando abriu) e os exemplares que ela tem hoje."""
    try:
        normalized = normalize_email(email)
    except ValueError as exc:
        raise HTTPException(400, "E-mail inválido.") from exc
    user = db.scalar(select(User).where(User.email == normalized))
    if user is None:
        raise HTTPException(404, "Registro não encontrado.")
    cache: dict = {}
    grants = db.scalars(select(PackGrant).where(PackGrant.user_id == user.id).order_by(PackGrant.id.desc()).limit(100)).all()
    generated = db.scalars(select(PackGrant).where(PackGrant.origin_user_id == user.id).order_by(PackGrant.id.desc()).limit(100)).all()
    return {
        "user": {"id": user.id, "nickname": user.nickname},
        "packs": [provenance.grant_origin(db, g, cache) for g in grants],
        "packs_generated_by_user": [provenance.grant_origin(db, g, cache) for g in generated],
    }
