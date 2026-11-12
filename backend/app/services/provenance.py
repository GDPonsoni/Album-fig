"""Rastreio de origem: de que pacote saiu cada figurinha e por quem ela passou.

`user_sticker` continua sendo o inventário do jogo (quantidade por pessoa). Aqui cada exemplar
tem identidade (`sticker_copy`) e uma cadeia de eventos que só cresce (`sticker_copy_event`):
sorteio, trocas e colagem. Tolerante de propósito: se o inventário tem mais unidades do que há
exemplares (dados anteriores ao rastreio), os que faltam nascem como 'legacy', sem origem.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PackGrant, RedeemCode, Sticker, StickerCopy, StickerCopyEvent, Trade, User


def _event(db: Session, copy: StickerCopy, event: str, **fields) -> None:
    db.add(StickerCopyEvent(copy_id=copy.id, event=event, **fields))


def record_draw(db: Session, user_id: int, sticker_id: int, grant_id: int, position: int) -> StickerCopy:
    copy = StickerCopy(sticker_id=sticker_id, owner_id=user_id, origin_grant_id=grant_id, origin_position=position)
    db.add(copy)
    db.flush()
    _event(db, copy, "drawn", to_user_id=user_id, pack_grant_id=grant_id)
    return copy


def _owned(db: Session, user_id: int, sticker_id: int, quantity: int) -> list[StickerCopy]:
    """Exemplares da pessoa, do mais antigo ao mais novo, completando com 'legacy' até bater com o inventário."""
    copies = list(
        db.scalars(
            select(StickerCopy).where(StickerCopy.owner_id == user_id, StickerCopy.sticker_id == sticker_id).order_by(StickerCopy.id)
        )
    )
    missing = quantity - len(copies)
    if missing > 0:
        extra = [StickerCopy(sticker_id=sticker_id, owner_id=user_id) for _ in range(missing)]
        db.add_all(extra)
        db.flush()
        for copy in extra:
            _event(db, copy, "legacy", to_user_id=user_id)
        copies = sorted(copies + extra, key=lambda c: c.id)
    return copies


def record_trade(db: Session, trade: Trade, giver: int, receiver: int, sticker_id: int, quantity: int, giver_had: int) -> None:
    """Passa `quantity` exemplares de quem deu para quem recebeu. Sai o mais novo primeiro: o mais antigo
    é o que fica colado no álbum (ver `record_paste`)."""
    copies = _owned(db, giver, sticker_id, giver_had)
    for copy in reversed(copies[-quantity:]):
        copy.owner_id = receiver
        _event(db, copy, "traded", from_user_id=giver, to_user_id=receiver, trade_id=trade.id)


def record_paste(db: Session, user_id: int, sticker_id: int, quantity: int) -> None:
    copies = _owned(db, user_id, sticker_id, quantity)
    if copies:
        _event(db, copies[0], "pasted", to_user_id=user_id)


# ---- consulta (superadmin)


def _name(db: Session, cache: dict[int, str | None], user_id: int | None) -> str | None:
    if user_id is None:
        return None
    if user_id not in cache:
        user = db.get(User, user_id)
        cache[user_id] = user.nickname if user else None
    return cache[user_id]


def grant_origin(db: Session, grant: PackGrant, cache: dict[int, str | None]) -> dict:
    """De onde veio o pacote: gaveta, código (com quem o gerou) ou concessão do admin."""
    code = None
    if grant.source == "code" and grant.source_ref and grant.source_ref.isdigit():
        row = db.get(RedeemCode, int(grant.source_ref))
        if row is not None:
            code = {
                "id": row.id, "code": row.code, "label": row.label, "created_at": row.created_at,
                "created_by": _name(db, cache, row.created_by), "redeemed_at": row.redeemed_at,
            }
    return {
        "grant_id": grant.id,
        "pack": grant.pack_type.name,
        "source": grant.source,
        "source_ref": grant.source_ref,
        "generated_by": _name(db, cache, grant.origin_user_id),
        "owner": _name(db, cache, grant.user_id),
        "granted_at": grant.granted_at,
        "opened_at": grant.opened_at,
        "code": code,
    }


def copy_chain(db: Session, copy: StickerCopy, cache: dict[int, str | None]) -> dict:
    sticker = db.get(Sticker, copy.sticker_id)
    grant = db.get(PackGrant, copy.origin_grant_id) if copy.origin_grant_id else None
    events = db.scalars(select(StickerCopyEvent).where(StickerCopyEvent.copy_id == copy.id).order_by(StickerCopyEvent.id))
    return {
        "copy_id": copy.id,
        "sticker": {"id": sticker.id, "number": sticker.number, "name": sticker.name},
        "owner": _name(db, cache, copy.owner_id),
        "origin": {**grant_origin(db, grant, cache), "position": copy.origin_position} if grant else None,
        "events": [
            {
                "event": e.event, "at": e.created_at, "from": _name(db, cache, e.from_user_id),
                "to": _name(db, cache, e.to_user_id), "trade_id": e.trade_id, "pack_grant_id": e.pack_grant_id,
            }
            for e in events
        ],
    }


def count_copies(db: Session, sticker_id: int) -> int:
    return db.scalar(select(func.count()).select_from(StickerCopy).where(StickerCopy.sticker_id == sticker_id)) or 0
