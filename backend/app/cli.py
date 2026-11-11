"""Uso: python -m app.cli <comando>"""
import argparse
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.auth import hasher
from app.db import SessionLocal
from app.models import (
    Album,
    AlbumParticipation,
    AuditLog,
    PackGrant,
    RateLimitEvent,
    Sticker,
    Trade,
    University,
    User,
    UserSticker,
)
from app.seed import seed_demo
from app.services import albums


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
                university_id=albums.default_university(db).id,
            )
        )
        db.commit()
    print("Admin criado.")


def purge_campaign(album_id: int | None = None) -> None:
    """Irreversível. Com `album_id`, apaga o que os jogadores fizeram naquele álbum
    (inventário, pacotes, trocas, participação) e mantém contas e conteúdo. Sem ele,
    apaga todas as contas de jogadores e tudo que depende delas, em todos os álbuns."""
    with SessionLocal() as db:
        if album_id is not None:
            if db.get(Album, album_id) is None:
                sys.exit("Álbum não encontrado.")
            stickers = select(Sticker.id).where(Sticker.album_id == album_id)
            db.execute(delete(Trade).where(Trade.album_id == album_id))  # as ofertas caem junto
            db.execute(delete(PackGrant).where(PackGrant.album_id == album_id))  # e os itens de abertura
            removed = db.execute(delete(UserSticker).where(UserSticker.sticker_id.in_(stickers))).rowcount
            players = db.execute(delete(AlbumParticipation).where(AlbumParticipation.album_id == album_id)).rowcount
            db.add(AuditLog(user_id=None, action="album.purge", payload={"album_id": album_id, "participants": players}))
            db.commit()
            print(f"Álbum {album_id}: {players} participações e {removed} linhas de inventário apagadas, junto com pacotes e trocas.")
            return
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


def _moment(value: str) -> datetime:
    """Data ISO (2027-03-01 ou 2027-03-01T08:00-03:00). Sem fuso, vale UTC."""
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def create_album(title: str, starts: str, ends: str, university: str | None, model: int | None, ready: bool) -> None:
    with SessionLocal() as db:
        if university:
            row = db.scalar(select(University).where(University.slug == university))
            if row is None:
                sys.exit("Universidade não encontrada.")
        else:
            row = albums.default_university(db)
        starts_at, ends_at = _moment(starts), _moment(ends)
        if ends_at <= starts_at:
            sys.exit("O fim do álbum precisa ser depois do início.")
        try:
            if model is not None:
                source = db.get(Album, model)
                if source is None:
                    sys.exit("Álbum-modelo não encontrado.")
                album = albums.duplicate_album(db, source, title, starts_at, ends_at)
            else:
                album = albums.create_album(db, row.id, title, starts_at, ends_at)
            if ready:
                album.state = "ready"
            db.commit()
        except IntegrityError as exc:
            if "ex_album_ready_overlap" in str(exc.orig):
                sys.exit("Já existe um álbum pronto nesse período para esta universidade.")
            raise
        print(f"Álbum {album.id} criado ({album.state}): {album.title}, de {album.starts_at} a {album.ends_at}.")


def close_albums() -> None:
    """Fecha em lote os álbuns que já passaram do fim: expira trocas e grava o resumo final."""
    with SessionLocal() as db:
        pending = db.scalars(
            select(AlbumParticipation.album_id).where(AlbumParticipation.final_stats.is_(None)).distinct()
        ).all()
        expired = albums.close_due_albums(db)
        db.commit()
        closed = [
            album.id for album in db.scalars(select(Album).where(Album.id.in_(pending))) if albums.album_status(album) == "ended"
        ]
    print(f"Álbuns fechados agora: {closed or 'nenhum'}. Trocas expiradas: {len(expired)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-demo", help="álbum de exemplo e contas de teste (em produção, sem admin e com senha aleatória)")
    admin = commands.add_parser("create-admin")
    admin.add_argument("email")
    admin.add_argument("nickname")
    purge = commands.add_parser("purge-campaign", help="apaga dados de jogadores (irreversível)")
    scope = purge.add_mutually_exclusive_group(required=True)
    scope.add_argument("--album-id", type=int, help="apaga só o que foi jogado neste álbum; contas e conteúdo ficam")
    scope.add_argument("--all-accounts", action="store_true", help="apaga todas as contas de jogadores, em todos os álbuns")
    purge.add_argument("--confirm", action="store_true", required=True)
    album = commands.add_parser("create-album", help="cria um álbum (rascunho, a menos que --ready)")
    album.add_argument("title")
    album.add_argument("starts_at", help="início, em ISO (ex.: 2027-03-01T08:00-03:00)")
    album.add_argument("ends_at", help="fim, em ISO")
    album.add_argument("--university", help="slug da universidade (padrão: a única ativa)")
    album.add_argument("--from-album", type=int, help="copia capítulos, figurinhas, pacotes e parâmetros deste álbum")
    album.add_argument("--ready", action="store_true", help="já marca como pronto (entra no ar na data de início)")
    commands.add_parser("close-albums", help="fecha os álbuns que já acabaram (trocas expiram, resumo final é gravado)")
    args = parser.parse_args()

    if args.command == "seed-demo":
        seed_demo()
    elif args.command == "create-admin":
        create_admin(args.email, args.nickname)
    elif args.command == "purge-campaign":
        purge_campaign(args.album_id)
    elif args.command == "create-album":
        create_album(args.title, args.starts_at, args.ends_at, args.university, args.from_album, args.ready)
    elif args.command == "close-albums":
        close_albums()


if __name__ == "__main__":
    main()
