import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AlbumChapter, RoleGrant, User, UserSession
from app.services import mfa
from app.services.core import now
from app.settings import settings

SESSION_COOKIE = "album_session"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, response: Response, user: User) -> None:
    token = secrets.token_urlsafe(32)
    expires = now() + timedelta(days=settings.session_days)
    db.add(UserSession(token_hash=hash_token(token), user_id=user.id, expires_at=expires))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
    )


def user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    return db.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > now())
    )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "Entre na sua conta para continuar.")
    return user


def verified_user(user: User = Depends(current_user)) -> User:
    if user.email_verified_at is None:
        raise HTTPException(403, "Confirme seu e-mail para continuar.")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Acesso restrito.")
    return user


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    token = request.cookies.get(SESSION_COOKIE)
    session = db.get(UserSession, hash_token(token)) if token else None
    if session is None or session.expires_at <= now():
        raise HTTPException(401, "Entre na sua conta para continuar.")
    return session


def admin_mfa(user: User = Depends(admin_user), session: UserSession = Depends(current_session)) -> User:
    """Superadmin com o segundo fator ativo e confirmado há pouco nesta sessão. O cabeçalho
    `X-MFA` diz ao front se falta ativar (`setup`) ou só confirmar o código (`verify`)."""
    if not mfa.is_enabled(user):
        raise HTTPException(403, mfa.SETUP_REQUIRED, headers={"X-MFA": "setup"})
    if not mfa.is_fresh(session):
        raise HTTPException(403, mfa.VERIFY_REQUIRED, headers={"X-MFA": "verify"})
    return user


def active_grant(db: Session, user_id: int, chapter_id: int, role: str | None = "editor") -> RoleGrant | None:
    """Papel vigente da pessoa no capítulo; `role=None` aceita qualquer um (usado para ver imagens privadas)."""
    return db.scalar(
        select(RoleGrant).where(
            RoleGrant.user_id == user_id,
            RoleGrant.chapter_id == chapter_id,
            RoleGrant.role == role if role is not None else RoleGrant.role.is_not(None),
            RoleGrant.revoked_at.is_(None),
            (RoleGrant.expires_at.is_(None)) | (RoleGrant.expires_at > now()),
        )
    )


def require_chapter_editor(
    chapter_id: int, user: User = Depends(verified_user), db: Session = Depends(get_db)
) -> AlbumChapter:
    """Editor do capítulo. Consulta o banco a cada requisição (nada fica guardado na sessão),
    então revogar ou expirar vale na requisição seguinte. Quem não tem o papel recebe a mesma
    404 de capítulo inexistente: não dá para descobrir quais capítulos existem."""
    chapter = db.get(AlbumChapter, chapter_id)
    if chapter is None or active_grant(db, user.id, chapter_id) is None:
        raise HTTPException(404, "Capítulo não encontrado.")
    return chapter


def can_review(db: Session, user: User, chapter_id: int) -> bool:
    return user.is_admin or active_grant(db, user.id, chapter_id, "reviewer") is not None


def reviewer_chapter_ids(db: Session, user: User) -> list[int]:
    moment = now()
    return list(
        db.scalars(
            select(RoleGrant.chapter_id).where(
                RoleGrant.user_id == user.id, RoleGrant.role == "reviewer", RoleGrant.revoked_at.is_(None),
                (RoleGrant.expires_at.is_(None)) | (RoleGrant.expires_at > moment),
            )
        )
    )


def promoter_grant(db: Session, user_id: int) -> RoleGrant | None:
    return db.scalar(
        select(RoleGrant).where(
            RoleGrant.user_id == user_id, RoleGrant.role == "promoter", RoleGrant.revoked_at.is_(None),
            (RoleGrant.expires_at.is_(None)) | (RoleGrant.expires_at > now()),
        )
    )


def promoter_user(user: User = Depends(verified_user), db: Session = Depends(get_db)) -> User:
    """Promotor vigente (conferido no banco a cada requisição). Quem não é recebe 404, como nas outras áreas."""
    if promoter_grant(db, user.id) is None:
        raise HTTPException(404, "Não encontrado.")
    return user


def staff_user(user: User = Depends(verified_user), db: Session = Depends(get_db)) -> User:
    """Superadmin ou revisor de algum capítulo: quem pode ter o segundo fator e usar a fila de revisão."""
    if not user.is_admin and not reviewer_chapter_ids(db, user):
        raise HTTPException(403, "Acesso restrito.")
    return user


def staff_mfa(user: User = Depends(staff_user), session: UserSession = Depends(current_session)) -> User:
    """Como `admin_mfa`, para revisores também: segundo fator ativo e confirmado há pouco."""
    if not mfa.is_enabled(user):
        raise HTTPException(403, mfa.SETUP_REQUIRED, headers={"X-MFA": "setup"})
    if not mfa.is_fresh(session):
        raise HTTPException(403, mfa.VERIFY_REQUIRED, headers={"X-MFA": "verify"})
    return user
