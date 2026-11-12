import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Album, Sticker, Trade, TradeOffer, User, UserSticker
from app.services.album import sticker_card
from app.services.albums import ENDED, album_status, current_album, require_live
from app.services import provenance
from app.services.core import audit, cfg, lock_user, now
from app.services.packs import add_to_inventory

LIVE = ("open", "active")
INVALID_LINK = "Este link de troca não é mais válido."


def _lock_trade(db: Session, token: str) -> Trade | None:
    return db.scalar(
        select(Trade).where(Trade.token == token).with_for_update(of=Trade).execution_options(populate_existing=True)
    )


def expire_if_due(db: Session, trade: Trade) -> bool:
    """A expiração é sempre decidida aqui, com o relógio do servidor."""
    if trade.state in LIVE and trade.expires_at <= now():
        trade.state = "expired"
        trade.finished_at = now()
        audit(db, None, "trade.expired", trade_id=trade.id)
        db.commit()
        return True
    return False


def current_trade(db: Session, user: User) -> Trade | None:
    moment = now()
    trades = db.scalars(
        select(Trade)
        .join(Album, Album.id == Trade.album_id)
        .where(
            Trade.state.in_(LIVE),
            or_(Trade.creator_id == user.id, Trade.partner_id == user.id),
            # Troca de álbum que acabou não conta mais como "em andamento".
            Album.state == "ready", Album.starts_at <= moment, Album.ends_at > moment,
        )
        .order_by(Trade.created_at.desc())
    ).all()
    current = None
    for trade in trades:
        if trade.expires_at > now():
            current = current or trade
    return current


def _end_with_album(db: Session, trade: Trade) -> bool:
    """Troca de álbum que já acabou expira no primeiro acesso, como as vencidas pelo relógio."""
    album = db.get(Album, trade.album_id)
    if trade.state in LIVE and album_status(album) != "active":
        trade.state = "expired"
        trade.finished_at = now()
        audit(db, None, "trade.expired", trade_id=trade.id, reason="album_ended")
        db.commit()
        return True
    return False


def create_trade(db: Session, user: User) -> Trade:
    album = require_live(db, user)
    lock_user(db, user.id)
    if current_trade(db, user) is not None:
        raise HTTPException(409, "Você já está em uma troca.")
    trade = Trade(
        token=secrets.token_urlsafe(24),
        album_id=album.id,
        creator_id=user.id,
        expires_at=now() + timedelta(seconds=cfg(db, "trade.link_ttl_seconds", album.id)),
    )
    db.add(trade)
    db.flush()
    audit(db, user.id, "trade.create", trade_id=trade.id, album_id=album.id)
    return trade


def join_trade(db: Session, user: User, token: str) -> Trade:
    # Ordem de travas: usuário e depois troca, para "uma troca ativa por vez" valer sob concorrência.
    album = require_live(db, user)
    lock_user(db, user.id)
    trade = _lock_trade(db, token)
    # Link de outro álbum (ou de álbum encerrado) é só um link inválido: não revela nada.
    if trade is None or trade.state != "open" or trade.album_id != album.id or expire_if_due(db, trade):
        raise HTTPException(410, INVALID_LINK)
    if trade.creator_id == user.id:
        raise HTTPException(409, "Envie este link para a outra pessoa.")
    if current_trade(db, user) is not None:
        raise HTTPException(409, "Você já está em uma troca.")
    trade.partner_id = user.id
    trade.state = "active"
    trade.joined_at = now()
    trade.expires_at = now() + timedelta(seconds=cfg(db, "trade.session_ttl_seconds", album.id))
    audit(db, user.id, "trade.join", trade_id=trade.id)
    return trade


def _require_participant(trade: Trade | None, user: User) -> Trade:
    if trade is None or user.id not in (trade.creator_id, trade.partner_id):
        raise HTTPException(404, INVALID_LINK)
    return trade


def _require_active(db: Session, trade: Trade) -> None:
    if _end_with_album(db, trade):
        raise HTTPException(409, ENDED)
    if expire_if_due(db, trade):
        raise HTTPException(410, "O tempo desta troca acabou.")
    if trade.state != "active":
        raise HTTPException(409, "Esta troca não está mais ativa.")


def _offers(db: Session, trade: Trade, user_id: int) -> list[TradeOffer]:
    rows = db.scalars(select(TradeOffer).where(TradeOffer.trade_id == trade.id, TradeOffer.user_id == user_id)).all()
    return sorted(rows, key=lambda o: o.sticker.number)


def _available(db: Session, album_id: int, user_id: int, sticker_ids: list[int]) -> dict[int, int]:
    """Repetidas disponíveis, só entre figurinhas do álbum da troca."""
    rows = db.scalars(
        select(UserSticker)
        .join(Sticker, Sticker.id == UserSticker.sticker_id)
        .where(UserSticker.user_id == user_id, UserSticker.sticker_id.in_(sticker_ids), Sticker.album_id == album_id)
    )
    return {row.sticker_id: row.quantity - 1 for row in rows}


def set_offer(db: Session, user: User, token: str, items: dict[int, int]) -> Trade:
    trade = _require_participant(_lock_trade(db, token), user)
    _require_active(db, trade)
    available = _available(db, trade.album_id, user.id, list(items))
    for sticker_id, quantity in items.items():
        if quantity < 1 or quantity > available.get(sticker_id, 0):
            raise HTTPException(409, "Você só pode oferecer figurinhas repetidas.")

    existing = _offers(db, trade, user.id)
    if {o.sticker_id: o.quantity for o in existing} == items:
        return trade
    for offer in existing:
        db.delete(offer)
    db.flush()
    db.add_all(TradeOffer(trade_id=trade.id, user_id=user.id, sticker_id=s, quantity=q) for s, q in items.items())
    # Nova versão: os aceites anteriores deixam de valer.
    trade.offer_version += 1
    return trade


def accept(db: Session, user: User, token: str, version: int) -> Trade:
    trade = _require_participant(_lock_trade(db, token), user)
    _require_active(db, trade)
    if version != trade.offer_version:
        raise HTTPException(409, "As ofertas mudaram. Confira antes de aceitar.")

    creator_offers = _offers(db, trade, trade.creator_id)
    partner_offers = _offers(db, trade, trade.partner_id)
    if not creator_offers or not partner_offers:
        raise HTTPException(409, "Cada lado precisa oferecer pelo menos uma figurinha.")

    if user.id == trade.creator_id:
        trade.creator_accepted_version = version
    else:
        trade.partner_accepted_version = version

    if trade.creator_accepted_version == trade.partner_accepted_version == version:
        _settle(db, trade, creator_offers, partner_offers)
    return trade


def _settle(db: Session, trade: Trade, creator_offers: list[TradeOffer], partner_offers: list[TradeOffer]) -> None:
    """Move as duas ofertas na transação em curso. Qualquer erro desfaz tudo."""
    for user_id in sorted((trade.creator_id, trade.partner_id)):
        lock_user(db, user_id)

    moves = ((trade.creator_id, trade.partner_id, creator_offers), (trade.partner_id, trade.creator_id, partner_offers))
    for giver, _, offers in moves:
        available = _available(db, trade.album_id, giver, [o.sticker_id for o in offers])
        if any(o.quantity > available.get(o.sticker_id, 0) for o in offers):
            raise HTTPException(409, "Uma das figurinhas oferecidas não está mais disponível.")

    for giver, receiver, offers in moves:
        for offer in offers:
            row = db.get(UserSticker, (giver, offer.sticker_id), populate_existing=True)
            had = row.quantity
            row.quantity -= offer.quantity
            provenance.record_trade(db, trade, giver, receiver, offer.sticker_id, offer.quantity, had)
            add_to_inventory(db, receiver, offer.sticker_id, offer.quantity)

    trade.state = "done"
    trade.finished_at = now()
    audit(
        db,
        None,
        "trade.done",
        trade_id=trade.id,
        creator_id=trade.creator_id,
        partner_id=trade.partner_id,
        creator_gave={o.sticker_id: o.quantity for o in creator_offers},
        partner_gave={o.sticker_id: o.quantity for o in partner_offers},
    )


def cancel(db: Session, user: User, token: str) -> Trade:
    trade = _require_participant(_lock_trade(db, token), user)
    if trade.state in LIVE and not expire_if_due(db, trade):
        trade.state = "cancelled"
        trade.finished_at = now()
        audit(db, user.id, "trade.cancel", trade_id=trade.id)
    return trade


def expire_due_trades(db: Session) -> list[int]:
    """Varredura periódica: encerra sessões vencidas e avisa quem está conectado."""
    due = list(
        db.scalars(
            select(Trade)
            .where(Trade.state.in_(LIVE), Trade.expires_at <= now())
            .with_for_update(skip_locked=True, of=Trade)
        )
    )
    expired = []
    for trade in due:
        trade.state = "expired"
        trade.finished_at = now()
        audit(db, None, "trade.expired", trade_id=trade.id)
        expired.append(trade.id)
    db.commit()
    return expired


def trade_view(db: Session, trade: Trade, user: User) -> dict:
    is_creator = user.id == trade.creator_id
    other_id = trade.partner_id if is_creator else trade.creator_id
    other = trade.partner if is_creator else trade.creator
    mine_accepted = trade.creator_accepted_version if is_creator else trade.partner_accepted_version
    other_accepted = trade.partner_accepted_version if is_creator else trade.creator_accepted_version

    def side(user_id: int | None) -> list[dict]:
        if user_id is None:
            return []
        return [{**sticker_card(o.sticker), "quantity": o.quantity} for o in _offers(db, trade, user_id)]

    return {
        "token": trade.token,
        "state": trade.state,
        "is_creator": is_creator,
        "other_nickname": other.nickname if other else None,
        "offer_version": trade.offer_version,
        "my_offer": side(user.id),
        "other_offer": side(other_id),
        "i_accepted": mine_accepted == trade.offer_version,
        "other_accepted": other_accepted == trade.offer_version,
        "expires_in": max(0, (trade.expires_at - now()).total_seconds()) if trade.state in LIVE else 0,
    }


def visible_trade(db: Session, user: User, token: str) -> dict:
    trade = _lock_trade(db, token)
    if trade is None:
        raise HTTPException(404, INVALID_LINK)
    _end_with_album(db, trade) or expire_if_due(db, trade)
    if user.id in (trade.creator_id, trade.partner_id):
        return trade_view(db, trade, user)
    if trade.state != "open" or trade.album_id != getattr(current_album(db, user), "id", None):
        raise HTTPException(410, INVALID_LINK)
    return {"token": token, "state": "invite", "other_nickname": trade.creator.nickname}
