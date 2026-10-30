import secrets

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models import PackGrant, RedeemCode, User
from app.services.core import audit, cfg, lock_user, now, rate_limited, record_hit
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


def redeem(db: Session, user: User, value: str) -> PackGrant:
    key = f"code-error:{user.id}"
    if rate_limited(db, key, cfg(db, "code.max_errors_per_hour"), 3600):
        raise HTTPException(429, "Muitas tentativas erradas. Tente de novo mais tarde.")

    # O UPDATE condicional é o que garante o uso único sob concorrência.
    current = now()
    row = db.execute(
        update(RedeemCode)
        .where(
            RedeemCode.code == normalize_code(value),
            RedeemCode.redeemed_at.is_(None),  # redeemed_by zera se a conta for apagada
            or_(RedeemCode.expires_at.is_(None), RedeemCode.expires_at > current),
        )
        .values(redeemed_by=user.id, redeemed_at=current)
        .returning(RedeemCode.id, RedeemCode.pack_type_id, RedeemCode.label)
    ).first()
    if row is None:
        record_hit(db, key)
        db.commit()
        raise HTTPException(400, INVALID)

    lock_user(db, user.id)
    grant = grant_pack(db, user.id, row.pack_type_id, "code", str(row.id))
    audit(db, user.id, "code.redeem", code_id=row.id, label=row.label)
    return grant
