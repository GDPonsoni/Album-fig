"""Contexto de álbum: qual álbum vale para um jogador agora, quem participa e o fechamento.

O estado de um álbum sai das datas, com o relógio do servidor; só `draft|ready`
é gravado. Não há cron: quem precisa saber se o álbum acabou pergunta aqui.
"""
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    Album,
    AlbumChapter,
    AlbumConfig,
    AlbumParticipation,
    PackGrant,
    PackOpeningItem,
    PackType,
    Rarity,
    Sticker,
    Trade,
    University,
    User,
    UserSticker,
)
from app.services.core import audit, now

NO_ALBUM = "Nenhum álbum em andamento."
ENDED = "Álbum encerrado."
PAUSED = "Álbum pausado. Volta em breve."
LIVE_TRADES = ("open", "active")


def album_status(album: Album, moment: datetime | None = None) -> str:
    """draft | scheduled | paused | active | ended. O fim é exclusivo: em `ends_at` o álbum já acabou."""
    if album.state != "ready":
        return "draft"
    moment = moment or now()
    if moment >= album.ends_at:
        return "ended"
    if album.paused_at is not None:
        return "paused"
    if moment < album.starts_at:
        return "scheduled"
    return "active"


def album_view(album: Album) -> dict:
    return {
        "id": album.id,
        "title": album.title,
        "cover_url": album.cover_url,
        "starts_at": album.starts_at,
        "ends_at": album.ends_at,
        "status": album_status(album),
    }


def default_university(db: Session) -> University:
    """Por enquanto há uma universidade só; toda conta nova entra nela."""
    university = db.scalar(select(University).where(University.active.is_(True)).order_by(University.id).limit(1))
    if university is None:
        raise RuntimeError("Nenhuma universidade cadastrada.")
    return university


def current_album(db: Session, user: User) -> Album | None:
    """O álbum em andamento da universidade do usuário. A exclusão no banco garante que é um só."""
    moment = now()
    return db.scalar(
        select(Album).where(
            Album.university_id == user.university_id,
            Album.state == "ready",
            Album.paused_at.is_(None),
            Album.starts_at <= moment,
            Album.ends_at > moment,
        )
    )


def require_live(db: Session, user: User) -> Album:
    """Toda escrita de jogo passa por aqui: sem álbum em andamento, nada muda."""
    album = current_album(db, user)
    if album is not None:
        return album
    ended = db.scalar(
        select(Album.id)
        .where(Album.university_id == user.university_id, Album.state == "ready", Album.ends_at <= now())
        .limit(1)
    )
    paused = db.scalar(
        select(Album.id).where(Album.university_id == user.university_id, Album.state == "ready", Album.paused_at.is_not(None), Album.ends_at > now()).limit(1)
    )
    if paused is not None:
        raise HTTPException(409, PAUSED)
    raise HTTPException(409, ENDED if ended is not None else NO_ALBUM)


def require_same_album(album: Album, album_id: int) -> None:
    """O objeto (pacote, figurinha, troca) precisa ser do álbum em andamento."""
    if album.id != album_id:
        raise HTTPException(409, ENDED)


def get_or_create_participation(db: Session, user_id: int, album_id: int) -> AlbumParticipation:
    """Chamar com o usuário já travado (`lock_user`), para ler o timer sem corrida."""
    db.execute(
        insert(AlbumParticipation)
        .values(user_id=user_id, album_id=album_id, joined_at=now())
        .on_conflict_do_nothing(index_elements=[AlbumParticipation.user_id, AlbumParticipation.album_id])
    )
    return db.execute(
        select(AlbumParticipation)
        .where(AlbumParticipation.user_id == user_id, AlbumParticipation.album_id == album_id)
        .execution_options(populate_existing=True)
    ).scalar_one()


def participation(db: Session, user_id: int, album_id: int) -> AlbumParticipation | None:
    return db.get(AlbumParticipation, (user_id, album_id))


def require_visible(db: Session, user: User, album_id: int) -> Album:
    """Álbum que o usuário pode consultar: o em andamento da própria universidade ou um
    de que participou. Para todo o resto a resposta é a mesma 404, sem revelar que existe."""
    album = db.get(Album, album_id)
    if album is not None and album.state == "ready" and album.university_id == user.university_id:
        status = album_status(album)
        if status == "active" or (status == "ended" and participation(db, user.id, album.id) is not None):
            return album
    raise HTTPException(404, "Álbum não encontrado.")


# ---- Fechamento


def _stats_for(db: Session, album: Album, user_ids: list[int]) -> dict[int, dict[str, Any]]:
    total = db.scalar(
        select(func.count()).select_from(Sticker).where(Sticker.album_id == album.id, Sticker.active.is_(True))
    ) or 0
    inventory = {
        row.user_id: row
        for row in db.execute(
            select(
                UserSticker.user_id,
                func.count(case((UserSticker.pasted.is_(True), 1))).label("pasted"),
                func.coalesce(func.sum(case((UserSticker.quantity > 1, UserSticker.quantity - 1), else_=0)), 0).label("duplicates"),
                func.max(case((UserSticker.pasted.is_(True), UserSticker.pasted_at))).label("last_pasted_at"),
            )
            .join(Sticker, Sticker.id == UserSticker.sticker_id)
            .where(Sticker.album_id == album.id, Sticker.active.is_(True), UserSticker.user_id.in_(user_ids))
            .group_by(UserSticker.user_id)
        )
    }
    received = dict(
        db.execute(
            select(PackGrant.user_id, func.count())
            .join(PackOpeningItem, PackOpeningItem.pack_grant_id == PackGrant.id)
            .where(PackGrant.album_id == album.id, PackGrant.user_id.in_(user_ids))
            .group_by(PackGrant.user_id)
        ).all()
    )
    trades: dict[int, int] = {}
    for creator_id, partner_id in db.execute(
        select(Trade.creator_id, Trade.partner_id).where(
            Trade.album_id == album.id,
            Trade.state == "done",
            or_(Trade.creator_id.in_(user_ids), Trade.partner_id.in_(user_ids)),
        )
    ):
        for user_id in (creator_id, partner_id):
            trades[user_id] = trades.get(user_id, 0) + 1

    stats = {}
    for user_id in user_ids:
        row = inventory.get(user_id)
        pasted = row.pasted if row else 0
        data: dict[str, Any] = {
            "pasted": pasted,
            "total_stickers": total,
            "received_total": received.get(user_id, 0),
            "duplicates": int(row.duplicates) if row else 0,
            "trades_done": trades.get(user_id, 0),
        }
        if total and pasted >= total and row is not None and row.last_pasted_at is not None:
            data["completed_at"] = row.last_pasted_at.isoformat()
        stats[user_id] = data
    return stats


def live_stats(db: Session, album: Album, user_id: int) -> dict[str, Any]:
    """Os mesmos números do resumo final, calculados na hora (álbum em andamento)."""
    return _stats_for(db, album, [user_id])[user_id]


def close_if_due(db: Session, album: Album) -> list[int]:
    """Fechamento preguiçoso e idempotente de um álbum que passou do fim: expira as
    trocas abertas e grava o resumo final de quem ainda não tem. Devolve os ids das
    trocas expiradas, para avisar quem estiver conectado. Não faz commit."""
    if album_status(album) != "ended":
        return []
    moment = now()
    expired = list(
        db.scalars(
            update(Trade)
            .where(Trade.album_id == album.id, Trade.state.in_(LIVE_TRADES))
            .values(state="expired", finished_at=moment)
            .returning(Trade.id)
        )
    )
    for trade_id in expired:
        audit(db, None, "trade.expired", trade_id=trade_id, reason="album_ended")

    pending = list(
        db.scalars(
            select(AlbumParticipation.user_id).where(
                AlbumParticipation.album_id == album.id, AlbumParticipation.final_stats.is_(None)
            )
        )
    )
    if pending:
        for user_id, data in _stats_for(db, album, pending).items():
            # O `IS NULL` faz o resumo ser gravado uma única vez, mesmo com dois fechamentos simultâneos.
            db.execute(
                update(AlbumParticipation)
                .where(
                    AlbumParticipation.album_id == album.id,
                    AlbumParticipation.user_id == user_id,
                    AlbumParticipation.final_stats.is_(None),
                )
                .values(final_stats=data)
            )
        audit(db, None, "album.closed", album_id=album.id, participants=len(pending))
    return expired


def close_due_albums(db: Session, university_id: int | None = None) -> list[int]:
    """Fecha todo álbum encerrado que ainda tem pendência (troca aberta ou resumo faltando)."""
    moment = now()
    pending_stats = (
        select(AlbumParticipation.album_id)
        .where(AlbumParticipation.album_id == Album.id, AlbumParticipation.final_stats.is_(None))
        .exists()
    )
    open_trades = select(Trade.id).where(Trade.album_id == Album.id, Trade.state.in_(LIVE_TRADES)).exists()
    query = select(Album).where(Album.state == "ready", Album.ends_at <= moment, or_(pending_stats, open_trades))
    if university_id is not None:
        query = query.where(Album.university_id == university_id)
    expired: list[int] = []
    for album in db.scalars(query.with_for_update(skip_locked=True, of=Album)).all():
        expired += close_if_due(db, album)
    return expired


# ---- Criação e cópia


def explicit_weights(db: Session, weights: dict[str, float]) -> dict[str, float]:
    """Pesos completos, um por raridade: o álbum não depende mais de `rarity.weight`."""
    return {str(r.id): float(weights.get(str(r.id), r.weight)) for r in db.scalars(select(Rarity).order_by(Rarity.rank))}


def copy_pack_types(db: Session, album: Album, model_album_id: int | None = None) -> list[PackType]:
    """Copia os tipos de pacote do álbum-modelo (ou do modelo global) para o álbum novo."""
    query = select(PackType).order_by(PackType.id)
    query = query.where(PackType.album_id == model_album_id if model_album_id is not None else PackType.album_id.is_(None))
    copies = []
    for source in db.scalars(query).all():
        copy = PackType(
            album_id=album.id,
            slug=source.slug,
            name=source.name,
            stickers_per_pack=source.stickers_per_pack,
            rarity_weights=explicit_weights(db, source.rarity_weights),
            guaranteed_min_rarity=source.guaranteed_min_rarity,
            org_filter=source.org_filter if model_album_id is not None else None,
            config=dict(source.config or {}),
        )
        db.add(copy)
        copies.append(copy)
    db.flush()
    return copies


def create_album(
    db: Session,
    university_id: int,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    cover_url: str | None = None,
    state: str = "draft",
) -> Album:
    album = Album(university_id=university_id, title=title, cover_url=cover_url, starts_at=starts_at, ends_at=ends_at, state=state)
    db.add(album)
    db.flush()
    copy_pack_types(db, album)
    return album


def duplicate_album(db: Session, source: Album, title: str, starts_at: datetime, ends_at: datetime) -> Album:
    """Cópia de conteúdo: capítulos, figurinhas, tipos de pacote e parâmetros. Nada de
    jogador vem junto (inventários, pacotes, códigos, trocas). Nasce como rascunho."""
    album = Album(
        university_id=source.university_id, title=title, cover_url=source.cover_url, starts_at=starts_at, ends_at=ends_at, state="draft"
    )
    db.add(album)
    db.flush()
    for chapter in db.scalars(select(AlbumChapter).where(AlbumChapter.album_id == source.id)):
        db.add(
            AlbumChapter(
                album_id=album.id,
                org_id=chapter.org_id,
                sort_order=chapter.sort_order,
                mosaic_image_url=chapter.mosaic_image_url,
                mosaic_cols=chapter.mosaic_cols,
                mosaic_rows=chapter.mosaic_rows,
            )
        )
    for sticker in db.scalars(select(Sticker).where(Sticker.album_id == source.id).order_by(Sticker.number)):
        db.add(
            Sticker(
                album_id=album.id,
                org_id=sticker.org_id,
                number=sticker.number,
                name=sticker.name,
                image_url=sticker.image_url,
                size=sticker.size,
                rarity_id=sticker.rarity_id,
                tag=sticker.tag,
                hint=sticker.hint,
                back_text=sticker.back_text,
                back_links=list(sticker.back_links or []),
                page_no=sticker.page_no,
                slot_layout=dict(sticker.slot_layout or {}),
                is_mosaic_piece=sticker.is_mosaic_piece,
                mosaic_index=sticker.mosaic_index,
                active=sticker.active,
            )
        )
    copy_pack_types(db, album, source.id)
    for row in db.scalars(select(AlbumConfig).where(AlbumConfig.album_id == source.id)):
        db.add(AlbumConfig(album_id=album.id, key=row.key, value=row.value))
    db.flush()
    return album


# ---- "Meus álbuns"


def summary(db: Session, user: User, album: Album) -> dict:
    """Progresso e números de um álbum para o jogador: ao vivo se em andamento, congelado se encerrado."""
    status = album_status(album)
    part = participation(db, user.id, album.id)
    if status == "ended" and part is not None and part.final_stats is not None:
        stats = part.final_stats
    else:
        stats = live_stats(db, album, user.id)
    return {**album_view(album), "joined_at": part.joined_at if part else None, "stats": stats, "server_now": now()}


def my_albums(db: Session, user: User) -> dict:
    close_due_albums(db, user.university_id)
    db.commit()
    active = current_album(db, user)
    ended = db.scalars(
        select(Album)
        .join(AlbumParticipation, AlbumParticipation.album_id == Album.id)
        .where(AlbumParticipation.user_id == user.id, Album.state == "ready", Album.ends_at <= now())
        .order_by(Album.ends_at.desc())
    ).all()
    # O próximo só é anunciado quando não há álbum em andamento.
    upcoming = None
    if active is None:
        upcoming = db.scalar(
            select(Album)
            .where(Album.university_id == user.university_id, Album.state == "ready", Album.starts_at > now())
            .order_by(Album.starts_at)
            .limit(1)
        )
    return {
        "active": summary(db, user, active) if active else None,
        "ended": [summary(db, user, album) for album in ended],
        "upcoming": {"title": upcoming.title, "starts_at": upcoming.starts_at} if upcoming else None,
        "server_now": now(),
    }
