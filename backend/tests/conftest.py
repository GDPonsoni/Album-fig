import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://album:album@localhost:5433/album_test"
os.environ["SMTP_HOST"] = ""

from datetime import timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.auth import hasher  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Album, AlbumChapter, Org, PackType, Rarity, Sticker, User, UserSticker  # noqa: E402
from app.services import albums  # noqa: E402
from app.services.core import now  # noqa: E402
from app.services.mail import dev_outbox  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "senha-de-teste"
PASSWORD_HASH = hasher.hash(PASSWORD)


@pytest.fixture(scope="session", autouse=True)
def schema():
    alembic_cfg = AlembicConfig(BACKEND / "alembic.ini")
    alembic_cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(autouse=True)
def clean():
    # DELETE e não TRUNCATE ... CASCADE, que levaria junto a universidade e os dados da migration.
    # A ordem respeita as chaves RESTRICT: primeiro o que é de jogador, depois o conteúdo, por fim os álbuns.
    with engine.begin() as conn:
        for table in (
            'trade', 'pack_grant', 'redeem_code', 'audit_log', 'rate_limit_event', '"user"',
            'sticker', 'album', 'org',
        ):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("DELETE FROM pack_type WHERE album_id IS NOT NULL"))
        conn.execute(text("DELETE FROM university WHERE slug <> 'padrao'"))
    dev_outbox.clear()


@pytest.fixture(autouse=True)
def album(clean):
    """Todo teste começa com um álbum em andamento na universidade padrão (começou ontem, dura 90 dias)."""
    with SessionLocal() as session:
        university = albums.default_university(session)
        row = albums.create_album(
            session, university.id, "Calourada", now() - timedelta(days=1), now() + timedelta(days=90), state="ready"
        )
        session.commit()
        return row


@pytest.fixture
def make_album(db):
    """Outro álbum da mesma universidade, em qualquer período (offsets em dias a partir de agora)."""

    def factory(title: str, start: float, end: float, state: str = "ready") -> Album:
        row = albums.create_album(
            db, albums.default_university(db).id, title, now() + timedelta(days=start), now() + timedelta(days=end), state=state
        )
        db.commit()
        return row

    return factory


def set_period(db, album: Album, start: float, end: float) -> None:
    """Move o período do álbum (offsets em dias a partir de agora): -10, -1 já acabou; 1, 30 ainda não começou."""
    row = db.get(Album, album.id)
    row.starts_at, row.ends_at = now() + timedelta(days=start), now() + timedelta(days=end)
    db.commit()
    db.expire_all()


def pack_of(db, album: Album, slug: str) -> PackType:
    from sqlalchemy import select

    return db.scalar(select(PackType).where(PackType.album_id == album.id, PackType.slug == slug))


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_user(db):
    def factory(nickname: str, verified: bool = True, admin: bool = False) -> User:
        user = User(
            email=f"{nickname.lower()}@example.com",
            nickname=nickname,
            password_hash=PASSWORD_HASH,
            is_admin=admin,
            email_verified_at=now() if verified else None,
            university_id=albums.default_university(db).id,
        )
        db.add(user)
        db.commit()
        return user

    return factory


@pytest.fixture
def login():
    def factory(user: User) -> TestClient:
        client = TestClient(app, headers={"X-Requested-With": "album"})
        response = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return factory


def add_content(db, album: Album, org_name: str = "Org Teste") -> list[Sticker]:
    """Uma org com 10 comuns, 5 raras e 2 lendárias, com capítulo no álbum dado."""
    org = Org(name=org_name, theme_color="#112233", university_id=album.university_id)
    db.add(org)
    db.flush()
    db.add(AlbumChapter(album_id=album.id, org_id=org.id))
    rarities = {r.name: r for r in db.query(Rarity)}
    stickers = []
    for number, name in enumerate(["comum"] * 10 + ["rara"] * 5 + ["lendária"] * 2, start=1):
        sticker = Sticker(
            album_id=album.id,
            org_id=org.id,
            number=number,
            name=f"Figurinha {number}",
            image_url=f"/media/x/{number}.webp",
            rarity_id=rarities[name].id,
            back_text=f"Verso {number}",
            back_links=[],
            slot_layout={"x": 1, "y": 1, "w": 10, "h": 10},
        )
        db.add(sticker)
        stickers.append(sticker)
    db.commit()
    return stickers


@pytest.fixture
def content(db, album):
    return add_content(db, album)


@pytest.fixture
def give(db):
    def factory(user: User, sticker: Sticker, quantity: int, pasted: bool = False) -> None:
        db.add(UserSticker(user_id=user.id, sticker_id=sticker.id, quantity=quantity, pasted=pasted))
        db.commit()

    return factory


def quantity_of(db, user: User, sticker: Sticker) -> int:
    db.expire_all()
    row = db.get(UserSticker, (user.id, sticker.id))
    return row.quantity if row else 0
