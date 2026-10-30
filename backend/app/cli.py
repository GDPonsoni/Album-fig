"""Uso: python -m app.cli <comando>"""
import argparse
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.api.auth import hasher
from app.db import SessionLocal
from app.models import AuditLog, RateLimitEvent, Trade, User
from app.seed import seed_demo
from app.settings import settings


def create_admin(email: str, nickname: str) -> None:
    password = getpass.getpass("Senha do admin: ")
    if len(password) < 8:
        sys.exit("A senha precisa de pelo menos 8 caracteres.")
    with SessionLocal() as db:
        db.add(
            User(
                email=email.lower(),
                nickname=nickname,
                password_hash=hasher.hash(password),
                is_admin=True,
                email_verified_at=datetime.now(UTC),
            )
        )
        db.commit()
    print("Admin criado.")


def purge_campaign() -> None:
    """Fim da campanha (RNF-08): apaga contas de jogadores e tudo que depende delas."""
    with SessionLocal() as db:
        players = db.scalar(select(User.id).where(User.is_admin.is_(False)).limit(1))
        if players is None:
            print("Nenhuma conta de jogador para apagar.")
            return
        db.execute(delete(Trade))
        db.execute(delete(AuditLog))
        db.execute(delete(RateLimitEvent))
        deleted = db.execute(delete(User).where(User.is_admin.is_(False))).rowcount
        db.commit()
    print(f"{deleted} contas apagadas, junto com inventários, pacotes, trocas e auditoria.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="conteúdo fictício e contas de teste (recusado em produção)")
    admin = commands.add_parser("create-admin")
    admin.add_argument("email")
    admin.add_argument("nickname")
    purge = commands.add_parser("purge-campaign", help="apaga todas as contas de jogadores (irreversível)")
    purge.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()

    if args.command == "seed-demo":
        if settings.is_prod:
            sys.exit("seed-demo não roda em produção.")
        seed_demo()
    elif args.command == "create-admin":
        create_admin(args.email, args.nickname)
    elif args.command == "purge-campaign":
        purge_campaign()


if __name__ == "__main__":
    main()
