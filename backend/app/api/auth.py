import secrets
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, current_user, hash_token, start_session
from app.db import get_db
from app.models import EmailToken, User, UserSession
from app.services.albums import default_university
from app.services.core import audit, enforce_rate, now
from app.services.mail import send_mail
from app.services.packs import refill_drawer
from app.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])
hasher = PasswordHasher()
# Mantém o tempo de resposta igual quando o e-mail não existe.
DUMMY_HASH = hasher.hash("album-dummy-password")

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
GENERIC_SIGNUP = "Se os dados estiverem certos, enviamos um link de confirmação para o seu e-mail."


def normalize_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError("E-mail inválido.") from exc


class RegisterIn(BaseModel):
    email: str
    password: Password
    nickname: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\w.\- ]{3,20}$")]

    _email = field_validator("email")(normalize_email)


class LoginIn(BaseModel):
    email: str
    password: str = Field(max_length=128)


class EmailIn(BaseModel):
    email: str

    _email = field_validator("email")(normalize_email)


class TokenIn(BaseModel):
    token: str = Field(max_length=100)


class ResetIn(TokenIn):
    password: Password


def _issue_token(db: Session, user: User, kind: str, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        EmailToken(token_hash=hash_token(token), user_id=user.id, kind=kind, expires_at=now() + timedelta(hours=hours))
    )
    return token


def _send_confirmation(db: Session, tasks: BackgroundTasks, user: User) -> None:
    token = _issue_token(db, user, "confirm", 48)
    link = f"{settings.base_url}/confirmar?token={token}"
    tasks.add_task(
        send_mail,
        user.email,
        "Confirme seu e-mail no Álbum da Calourada",
        f"Oi, {user.nickname}!\n\nConfirme seu e-mail para abrir seus pacotes:\n{link}\n\n"
        "Se não foi você, é só ignorar esta mensagem.",
    )


def _consume_token(db: Session, token: str, kind: str) -> EmailToken:
    row = db.scalar(
        select(EmailToken)
        .where(EmailToken.token_hash == hash_token(token), EmailToken.kind == kind)
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= now():
        raise HTTPException(400, "Este link é inválido ou já expirou.")
    row.used_at = now()
    return row


def user_view(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "verified": user.email_verified_at is not None,
        "is_admin": user.is_admin,
    }


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    enforce_rate(db, f"register:{request.client.host}", "rate.register_per_hour")
    db.commit()

    if db.scalar(select(User.id).where(User.email == body.email)) is not None:
        # Mesma resposta de um cadastro novo: não revela quem tem conta.
        tasks.add_task(
            send_mail,
            body.email,
            "Você já tem conta no Álbum da Calourada",
            f"Alguém tentou criar uma conta com este e-mail. Se foi você, entre em {settings.base_url}/entrar "
            "ou use a recuperação de senha.",
        )
        return {"message": GENERIC_SIGNUP}

    if db.scalar(select(User.id).where(func.lower(User.nickname) == body.nickname.lower())) is not None:
        raise HTTPException(409, "Este apelido já está em uso.")

    user = User(
        email=body.email,
        password_hash=hasher.hash(body.password),
        nickname=body.nickname,
        university_id=default_university(db).id,  # uma universidade por enquanto; sem escolha na tela
    )
    db.add(user)
    db.flush()
    audit(db, user.id, "user.register")
    _send_confirmation(db, tasks, user)
    db.commit()
    return {"message": GENERIC_SIGNUP}


@router.post("/confirm")
def confirm(body: TokenIn, db: Session = Depends(get_db)):
    row = _consume_token(db, body.token, "confirm")
    db.execute(update(User).where(User.id == row.user_id).values(email_verified_at=now()))
    audit(db, row.user_id, "user.confirm")
    db.commit()
    return {"message": "E-mail confirmado."}


@router.post("/resend")
def resend(tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.email_verified_at is not None:
        return {"message": "Seu e-mail já está confirmado."}
    enforce_rate(db, f"resend:{user.id}", "rate.email_per_hour")
    _send_confirmation(db, tasks, user)
    db.commit()
    return {"message": "Enviamos um novo link."}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    enforce_rate(db, f"login:{request.client.host}:{email}", "rate.login_per_hour")
    db.commit()

    user = db.scalar(select(User).where(User.email == email))
    try:
        hasher.verify(user.password_hash if user else DUMMY_HASH, body.password)
    except (VerificationError, InvalidHashError):
        user = None
    if user is None:
        raise HTTPException(401, "E-mail ou senha incorretos.")

    start_session(db, response, user)
    audit(db, user.id, "user.login")
    db.commit()
    return user_view(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/forgot")
def forgot(body: EmailIn, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    enforce_rate(db, f"forgot:{body.email}", "rate.email_per_hour")
    user = db.scalar(select(User).where(User.email == body.email))
    if user is not None:
        token = _issue_token(db, user, "reset", 2)
        tasks.add_task(
            send_mail,
            user.email,
            "Recuperação de senha do Álbum da Calourada",
            f"Para criar uma nova senha, abra:\n{settings.base_url}/redefinir?token={token}\n\n"
            "O link vale por 2 horas. Se não foi você, ignore esta mensagem.",
        )
    db.commit()
    return {"message": "Se houver uma conta com este e-mail, enviamos um link."}


@router.post("/reset")
def reset(body: ResetIn, db: Session = Depends(get_db)):
    row = _consume_token(db, body.token, "reset")
    db.execute(update(User).where(User.id == row.user_id).values(password_hash=hasher.hash(body.password)))
    # Derruba as sessões abertas: quem pediu a troca pode estar reagindo a um acesso indevido.
    db.execute(delete(UserSession).where(UserSession.user_id == row.user_id))
    audit(db, row.user_id, "user.password_reset")
    db.commit()
    return {"message": "Senha alterada. Entre com a nova senha."}


me_router = APIRouter(tags=["auth"])


@me_router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    refill_drawer(db, user)
    return user_view(user)
