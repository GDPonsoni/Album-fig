from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Org, Sticker, User, UserSticker
from app.services.core import audit, lock_user, now


def sticker_card(sticker: Sticker) -> dict:
    return {
        "id": sticker.id,
        "number": sticker.number,
        "name": sticker.name,
        "image_url": sticker.image_url,
        "size": sticker.size,
        "tag": sticker.tag,
        "rarity": {"name": sticker.rarity.name, "rank": sticker.rarity.rank, "color": sticker.rarity.color},
        "org_id": sticker.org_id,
        "org_name": sticker.org.name,
        "org_color": sticker.org.theme_color,
        "page_no": sticker.page_no,
        "is_mosaic_piece": sticker.is_mosaic_piece,
        "mosaic_index": sticker.mosaic_index,
    }


def _inventory(db: Session, user: User) -> dict[int, UserSticker]:
    rows = db.scalars(select(UserSticker).where(UserSticker.user_id == user.id, UserSticker.quantity > 0))
    return {row.sticker_id: row for row in rows}


def album_index(db: Session, user: User) -> dict:
    orgs = db.scalars(select(Org).order_by(Org.sort_order, Org.id)).all()
    stickers = db.execute(
        select(Sticker.id, Sticker.org_id, Sticker.page_no, Sticker.is_mosaic_piece).where(Sticker.active.is_(True))
    ).all()
    inventory = _inventory(db, user)
    pasted = {sid for sid, row in inventory.items() if row.pasted}

    pages = []
    org_rows = []
    for org in orgs:
        own = [s for s in stickers if s.org_id == org.id]
        if not own:
            continue
        for page_no in sorted({s.page_no for s in own if not s.is_mosaic_piece}):
            slots = [s for s in own if not s.is_mosaic_piece and s.page_no == page_no]
            pages.append(
                {
                    "kind": "stickers",
                    "org_id": org.id,
                    "page_no": page_no,
                    "org_name": org.name,
                    "color": org.theme_color,
                    "total": len(slots),
                    "filled": sum(s.id in pasted for s in slots),
                }
            )
        pieces = [s for s in own if s.is_mosaic_piece]
        if pieces:
            pages.append(
                {
                    "kind": "mosaic",
                    "org_id": org.id,
                    "page_no": 0,
                    "org_name": org.name,
                    "color": org.theme_color,
                    "total": len(pieces),
                    "filled": sum(s.id in pasted for s in pieces),
                }
            )
        org_rows.append(
            {
                "id": org.id,
                "name": org.name,
                "color": org.theme_color,
                "logo_url": org.logo_url,
                "total": len(own),
                "filled": sum(s.id in pasted for s in own),
            }
        )

    total = len(stickers)
    active_ids = {s.id for s in stickers}
    return {
        "pages": pages,
        "orgs": org_rows,
        "total": total,
        "filled": len(pasted & active_ids),
        "tray": tray(db, user, inventory),
    }


def tray(db: Session, user: User, inventory: dict[int, UserSticker] | None = None) -> list[dict]:
    rows = inventory if inventory is not None else _inventory(db, user)
    items = [row.sticker for row in rows.values() if not row.pasted and row.sticker.active]
    return [sticker_card(s) for s in sorted(items, key=lambda s: s.number)]


def _slot(sticker: Sticker, row: UserSticker | None) -> dict:
    slot = {
        "sticker_id": sticker.id,
        "number": sticker.number,
        "size": sticker.size,
        "layout": sticker.slot_layout,
        "mosaic_index": sticker.mosaic_index,
        "hint": sticker.hint,
        "filled": False,
    }
    if row is not None and row.pasted:
        slot.update(filled=True, card=sticker_card(sticker), quantity=row.quantity)
    return slot


def page_detail(db: Session, user: User, org_id: int, page_no: int) -> dict:
    """page_no 0 é a página de mosaico da org."""
    org = db.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "Página não encontrada.")
    is_mosaic = page_no == 0
    query = select(Sticker).where(
        Sticker.org_id == org_id, Sticker.active.is_(True), Sticker.is_mosaic_piece.is_(is_mosaic)
    )
    if not is_mosaic:
        query = query.where(Sticker.page_no == page_no)
    stickers = db.scalars(query.order_by(Sticker.mosaic_index if is_mosaic else Sticker.number)).all()
    if not stickers:
        raise HTTPException(404, "Página não encontrada.")
    inventory = _inventory(db, user)
    return {
        "kind": "mosaic" if is_mosaic else "stickers",
        "org": {
            "id": org.id,
            "name": org.name,
            "description": org.description,
            "logo_url": org.logo_url,
            "color": org.theme_color,
            "mosaic_image_url": org.mosaic_image_url if is_mosaic else None,
            "mosaic_cols": org.mosaic_cols,
            "mosaic_rows": org.mosaic_rows,
        },
        "page_no": page_no,
        "slots": [_slot(s, inventory.get(s.id)) for s in stickers],
    }


def paste(db: Session, user: User, sticker_id: int) -> Sticker:
    lock_user(db, user.id)
    row = db.get(UserSticker, (user.id, sticker_id))
    if row is None or row.quantity < 1:
        raise HTTPException(404, "Você não tem esta figurinha.")
    if row.pasted:
        raise HTTPException(409, "Esta figurinha já está colada.")
    row.pasted = True
    row.pasted_at = now()
    audit(db, user.id, "sticker.paste", sticker_id=sticker_id)
    return row.sticker


def paste_all(db: Session, user: User) -> list[int]:
    """Cola tudo o que está na bandeja, com as mesmas regras de `paste`. Idempotente:
    sem nada para colar, devolve lista vazia."""
    lock_user(db, user.id)
    rows = db.scalars(
        select(UserSticker)
        .join(Sticker, Sticker.id == UserSticker.sticker_id)
        .where(
            UserSticker.user_id == user.id,
            UserSticker.quantity > 0,
            UserSticker.pasted.is_(False),
            Sticker.active.is_(True),
        )
        .order_by(Sticker.number)
    ).all()
    current = now()
    for row in rows:
        row.pasted = True
        row.pasted_at = current
    ids = [row.sticker_id for row in rows]
    if ids:
        audit(db, user.id, "sticker.paste_all", sticker_ids=ids)
    return ids


def sticker_back(db: Session, user: User, sticker_id: int) -> dict:
    """O verso só existe para quem colou a figurinha."""
    row = db.get(UserSticker, (user.id, sticker_id))
    if row is None or not row.pasted:
        raise HTTPException(403, "Cole a figurinha para ver o verso.")
    sticker = row.sticker
    return {**sticker_card(sticker), "back_text": sticker.back_text, "back_links": sticker.back_links}


def duplicates(db: Session, user: User) -> list[dict]:
    """Repetidas: tudo além do exemplar que fica reservado para o álbum."""
    rows = [row for row in _inventory(db, user).values() if row.quantity > 1 and row.sticker.active]
    rows.sort(key=lambda r: r.sticker.number)
    return [{**sticker_card(r.sticker), "available": r.quantity - 1, "pasted": r.pasted} for r in rows]
