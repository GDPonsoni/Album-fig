import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, UserSession
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
