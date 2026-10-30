from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, verified_user
from app.db import get_db
from app.models import PackGrant, User
from app.services import album, codes, packs
from app.services.core import cfg, now
from app.settings import settings

router = APIRouter(tags=["game"])


class RedeemIn(BaseModel):
    code: str = Field(max_length=60)


@router.get("/album")
def get_album(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return album.album_index(db, user)


@router.get("/album/orgs/{org_id}/pages/{page_no}")
def get_page(org_id: int, page_no: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return album.page_detail(db, user, org_id, page_no)


@router.post("/album/paste/{sticker_id}")
def paste(sticker_id: int, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    sticker = album.paste(db, user, sticker_id)
    card = album.sticker_card(sticker)
    db.commit()
    return card


@router.get("/stickers/{sticker_id}/back")
def back(sticker_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return album.sticker_back(db, user, sticker_id)


@router.get("/inventory/duplicates")
def duplicates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return album.duplicates(db, user)


def _grant_view(grant: PackGrant) -> dict:
    return {
        "id": grant.id,
        "source": grant.source,
        "name": grant.pack_type.name,
        "slug": grant.pack_type.slug,
        "stickers_per_pack": grant.pack_type.stickers_per_pack,
    }


@router.get("/packs")
def stock(user: User = Depends(current_user), db: Session = Depends(get_db)):
    packs.refill_drawer(db, user)
    grants = db.scalars(
        select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.opened_at.is_(None)).order_by(PackGrant.id)
    ).all()
    return {
        "stock": [_grant_view(g) for g in grants],
        "daily_cap": cfg(db, "daily.stock_cap"),
        "daily_in_stock": sum(g.source == "daily" for g in grants),
        "next_pack_at": user.next_pack_at,
        "server_now": now(),
        "refill_minutes": cfg(db, "daily.refill_minutes"),
        "dev_tools": not settings.is_prod,
    }


@router.post("/packs/{grant_id}/open")
def open_pack(grant_id: int, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    grant, result = packs.open_pack(db, user, grant_id)
    payload = {
        "pack": _grant_view(grant),
        "items": [{**album.sticker_card(sticker), "is_new": is_new} for sticker, is_new in result],
    }
    db.commit()
    return payload


@router.post("/codes/redeem")
def redeem(body: RedeemIn, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    grant = codes.redeem(db, user, body.code)
    payload = _grant_view(grant)
    db.commit()
    return payload
