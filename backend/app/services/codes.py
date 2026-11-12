import secrets

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import PackGrant, PackType, RedeemCode, User
from app.services.core import audit, cfg, lock_user, now, rate_limited, record_hit
from app.services.albums import require_live
from app.services.packs import grant_pack

# Sem 0/O/1/I/L: o código é digitado à mão a partir de cartazes.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
INVALID = "Código inválido."


def generate_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def normalize_code(value: str) -> str:
    raw = "".join(ch for ch in value.upper() if ch.isalnum())
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def _error_key(user: User) -> str:
    return f"code-error:{user.id}"


def _check_error_limit(db: Session, user: User) -> None:
    if rate_limited(db, _error_key(user), cfg(db, "code.max_errors_per_hour"), 3600):
        raise HTTPException(429, "Muitas tentativas erradas. Tente de novo mais tarde.")


def preview(db: Session, user: User, value: str) -> RedeemCode:
    """Consulta sem efeito sobre o código (é o que o link do QR abre). Divide o limite
    de erros com o resgate, para não servir de oráculo para adivinhar códigos."""
    _check_error_limit(db, user)
    album = require_live(db, user)
    current = now()
    row = db.scalar(
        select(RedeemCode).where(
            RedeemCode.code == normalize_code(value),
            RedeemCode.album_id == album.id,
            RedeemCode.redeemed_at.is_(None),
            or_(RedeemCode.expires_at.is_(None), RedeemCode.expires_at > current),
        )
    )
    if row is None:
        record_hit(db, _error_key(user))
        db.commit()
        raise HTTPException(400, INVALID)
    return row


def redeem(db: Session, user: User, value: str) -> PackGrant:
    key = _error_key(user)
    _check_error_limit(db, user)
    album = require_live(db, user)

    # O UPDATE condicional é o que garante o uso único sob concorrência.
    current = now()
    row = db.execute(
        update(RedeemCode)
        .where(
            RedeemCode.code == normalize_code(value),
            RedeemCode.album_id == album.id,  # código de outro álbum é só um código inválido
            RedeemCode.redeemed_at.is_(None),  # redeemed_by zera se a conta for apagada
            or_(RedeemCode.expires_at.is_(None), RedeemCode.expires_at > current),
        )
        .values(redeemed_by=user.id, redeemed_at=current)
        .returning(RedeemCode.id, RedeemCode.pack_type_id, RedeemCode.label, RedeemCode.created_by)
    ).first()
    if row is None:
        record_hit(db, key)
        db.commit()
        raise HTTPException(400, INVALID)

    lock_user(db, user.id)
    grant = grant_pack(db, user.id, db.get(PackType, row.pack_type_id), "code", str(row.id), origin_user_id=row.created_by)
    audit(db, user.id, "code.redeem", code_id=row.id, label=row.label, album_id=album.id, created_by=row.created_by, grant_id=grant.id)
    return grant
