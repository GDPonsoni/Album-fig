import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "postgresql+psycopg://album:album@localhost:5433/album_test"
os.environ["SMTP_HOST"] = ""

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.api.auth import hasher  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Org, Rarity, Sticker, User, UserSticker  # noqa: E402
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
    # DELETE e não TRUNCATE ... CASCADE, que levaria junto pack_type e os dados da migration.
    with engine.begin() as conn:
        for table in ('sticker', 'trade', 'redeem_code', 'audit_log', 'rate_limit_event', '"user"', 'org'):
            conn.execute(text(f"DELETE FROM {table}"))
    dev_outbox.clear()


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


@pytest.fixture
def content(db):
    """Uma org com 10 comuns, 5 raras e 2 lendárias."""
    org = Org(name="Org Teste", theme_color="#112233")
    db.add(org)
    db.flush()
    rarities = {r.name: r for r in db.query(Rarity)}
    stickers = []
    for number, name in enumerate(["comum"] * 10 + ["rara"] * 5 + ["lendária"] * 2, start=1):
        sticker = Sticker(
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
def give(db):
    def factory(user: User, sticker: Sticker, quantity: int, pasted: bool = False) -> None:
        db.add(UserSticker(user_id=user.id, sticker_id=sticker.id, quantity=quantity, pasted=pasted))
        db.commit()

    return factory


def quantity_of(db, user: User, sticker: Sticker) -> int:
    db.expire_all()
    row = db.get(UserSticker, (user.id, sticker.id))
    return row.quantity if row else 0
