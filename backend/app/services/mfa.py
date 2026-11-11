"""Verificação em duas etapas do superadmin: TOTP (RFC 6238) com códigos de recuperação.

O segundo fator é um reforço por sessão: depois de confirmado, vale por
`MFA_TTL` naquela sessão, e as ações sensíveis (aprovar, publicar, conceder
papéis) exigem que ele esteja em dia. O login em si não muda.
"""
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

import pyotp
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import RecoveryCode, User, UserSession
from app.services.core import audit, now, rate_limited, record_hit

ISSUER = "UniAlbum"
MFA_TTL = timedelta(minutes=15)
RECOVERY_CODES = 10
MAX_FAILURES = 5  # por janela, somando TOTP e códigos de recuperação
FAILURE_WINDOW = 15 * 60
STEP = 30
SETUP_REQUIRED = "Ative a verificação em duas etapas na página Conta para fazer isto."
VERIFY_REQUIRED = "Confirme o código de duas etapas para continuar."
BAD_CODE = "Código incorreto."


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _normalize_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _failure_key(user: User) -> str:
    return f"mfa-fail:{user.id}"


def is_enabled(user: User) -> bool:
    return user.totp_secret is not None and user.totp_enabled_at is not None


def is_fresh(session: UserSession | None) -> bool:
    return session is not None and session.mfa_at is not None and session.mfa_at > now() - MFA_TTL


def begin_setup(db: Session, user: User) -> dict:
    """Gera um segredo novo, ainda não ativo. Só passa a valer depois de `enable` com um código certo."""
    if is_enabled(user):
        raise HTTPException(409, "A verificação em duas etapas já está ativa.")
    user.totp_secret = pyotp.random_base32()
    user.totp_enabled_at = None
    user.totp_last_step = None
    audit(db, user.id, "mfa.setup_start")
    uri = pyotp.TOTP(user.totp_secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return {"secret": user.totp_secret, "otpauth_uri": uri}


def _check_totp(user: User, code: str) -> bool:
    """Aceita o passo atual e os vizinhos (relógio do celular), e nunca o mesmo passo duas vezes."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6 or user.totp_secret is None:
        return False
    totp = pyotp.TOTP(user.totp_secret)
    current = int(time.time()) // STEP
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.at(step * STEP), code):
            if user.totp_last_step is not None and step <= user.totp_last_step:
                return False  # código já usado
            user.totp_last_step = step
            return True
    return False


def _guard(db: Session, user: User) -> None:
    if rate_limited(db, _failure_key(user), MAX_FAILURES, FAILURE_WINDOW):
        raise HTTPException(429, "Muitas tentativas erradas. Tente de novo em alguns minutos.")


def _fail(db: Session, user: User, action: str) -> None:
    db.rollback()  # nada do que a tentativa mexeu fica
    record_hit(db, _failure_key(user))
    audit(db, user.id, action)
    db.commit()
    raise HTTPException(400, BAD_CODE)


def enable(db: Session, user: User, session: UserSession, code: str) -> list[str]:
    """Confirma o segredo com um código do app e devolve os códigos de recuperação, uma única vez."""
    if is_enabled(user):
        raise HTTPException(409, "A verificação em duas etapas já está ativa.")
    if user.totp_secret is None:
        raise HTTPException(409, "Comece a configuração antes de confirmar.")
    _guard(db, user)
    if not _check_totp(user, code):
        _fail(db, user, "mfa.enable_fail")
    user.totp_enabled_at = now()
    session.mfa_at = now()
    codes = _new_recovery_codes(db, user)
    audit(db, user.id, "mfa.enable")
    return codes


def _new_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    db.add_all(RecoveryCode(user_id=user.id, code_hash=_hash(_normalize_recovery(code))) for code in codes)
    return codes


def verify(db: Session, user: User, session: UserSession, code: str) -> str:
    """Confirma o segundo fator nesta sessão. Aceita o código do app ou um de recuperação."""
    if not is_enabled(user):
        raise HTTPException(403, SETUP_REQUIRED)
    _guard(db, user)
    if _check_totp(user, code):
        method = "totp"
    else:
        # O UPDATE condicional garante que cada código de recuperação vale uma vez só.
        used = db.execute(
            update(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == _hash(_normalize_recovery(code)),
                RecoveryCode.used_at.is_(None),
            )
            .values(used_at=now())
            .returning(RecoveryCode.id)
        ).first()
        if used is None:
            _fail(db, user, "mfa.verify_fail")
        method = "recovery"
    session.mfa_at = now()
    audit(db, user.id, "mfa.verify", method=method)
    return method


def disable(db: Session, user: User) -> None:
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    db.execute(update(UserSession).where(UserSession.user_id == user.id).values(mfa_at=None))
    audit(db, user.id, "mfa.disable")


def recovery_left(db: Session, user: User) -> int:
    return len(db.scalars(select(RecoveryCode.id).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))).all())
