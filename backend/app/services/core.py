"""Pequenos utilitários compartilhados: config, auditoria, rate limit e relógio."""
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import AlbumConfig, AuditLog, Config, RateLimitEvent, User


def now() -> datetime:
    return datetime.now(UTC)


# Parâmetros que um álbum pode sobrescrever (tabela album_config). O resto é sempre global.
ALBUM_KEYS = frozenset(
    {
        "daily.timezone",
        "daily.refill_minutes",
        "daily.stock_cap",
        "daily.welcome_packs",
        "daily.premium_every_n_days",
        "daily.common_pack_slug",
        "daily.premium_pack_slug",
        "trade.link_ttl_seconds",
        "trade.session_ttl_seconds",
    }
)


def cfg(db: Session, key: str, album_id: int | None = None) -> Any:
    """Parâmetro do jogo: a sobrescrita do álbum, se houver, senão o padrão global."""
    if album_id is not None and key in ALBUM_KEYS:
        override = db.get(AlbumConfig, (album_id, key))
        if override is not None:
            return override.value
    row = db.get(Config, key)
    if row is None:
        raise RuntimeError(f"Parâmetro de configuração ausente: {key}")
    return row.value


def audit(db: Session, user_id: int | None, action: str, **payload: Any) -> None:
    db.add(AuditLog(user_id=user_id, action=action, payload=payload))


def rate_limited(db: Session, key: str, limit: int, window_seconds: int) -> bool:
    since = now() - timedelta(seconds=window_seconds)
    count = db.scalar(
        select(func.count()).select_from(RateLimitEvent).where(RateLimitEvent.key == key, RateLimitEvent.created_at >= since)
    )
    return (count or 0) >= limit


def record_hit(db: Session, key: str) -> None:
    db.add(RateLimitEvent(key=key))


def enforce_rate(db: Session, key: str, limit_key: str, window_seconds: int = 3600) -> None:
    """Conta uma tentativa e bloqueia ao passar do limite configurado."""
    if rate_limited(db, key, cfg(db, limit_key), window_seconds):
        raise HTTPException(429, "Muitas tentativas. Tente de novo mais tarde.")
    record_hit(db, key)


def purge_rate_events(db: Session, older_than_seconds: int = 86400) -> None:
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < now() - timedelta(seconds=older_than_seconds)))


def lock_user(db: Session, user_id: int) -> User:
    """Serializa as operações de inventário de um usuário: toda operação que mexe
    em inventário ou estoque passa por aqui, o que evita corridas e deadlocks."""
    return db.execute(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
