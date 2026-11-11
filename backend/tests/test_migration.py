"""A migration 0004 sobre dados de uma campanha já em andamento, num banco à parte:
nada some, nada fica sem álbum, e o caminho de volta (downgrade) funciona."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text

from app.db import engine as main_engine

BACKEND = Path(__file__).resolve().parent.parent
DATABASE = "album_migtest"
TABLES = ('"user"', "org", "sticker", "pack_grant", "pack_opening_item", "user_sticker", "trade", "trade_offer", "redeem_code")
ALBUM_TABLES = ("sticker", "pack_grant", "redeem_code", "trade")

# Uma campanha no esquema 0003: 2 orgs, 5 figurinhas, 3 usuários (um sem gaveta), pacotes, inventário, troca e códigos.
OLD_DATA = """
INSERT INTO org (id, name, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows) VALUES
  (1, 'Tubarões', 2, '/media/a.webp', 2, 3), (2, 'Raposas', 1, NULL, 4, 4);
INSERT INTO sticker (id, org_id, number, name, size, rarity_id, back_links, slot_layout, is_mosaic_piece) VALUES
  (1, 1, 1, 'Um', 'P', 1, '[]', '{}', false), (2, 1, 2, 'Dois', 'P', 2, '[]', '{}', false),
  (3, 2, 3, 'Três', 'G', 3, '[]', '{}', false), (4, 2, 4, 'Quatro', 'P', 1, '[]', '{}', true),
  (5, 2, 5, 'Cinco', 'P', 1, '[]', '{}', false);
INSERT INTO "user" (id, email, password_hash, nickname, is_admin, email_verified_at, last_daily_grant_on, next_pack_at) VALUES
  (1, 'ana@example.com', 'x', 'Ana', false, now(), '2026-03-10', '2026-03-11 09:00+00'),
  (2, 'bia@example.com', 'x', 'Bia', false, now(), '2026-03-09', '2026-03-10 21:30+00'),
  (3, 'nova@example.com', 'x', 'Nova', false, NULL, NULL, NULL);
INSERT INTO pack_grant (id, user_id, pack_type_id, source, opened_at) VALUES
  (1, 1, (SELECT id FROM pack_type WHERE slug = 'comum'), 'daily', now()),
  (2, 1, (SELECT id FROM pack_type WHERE slug = 'premium'), 'code', NULL),
  (3, 2, (SELECT id FROM pack_type WHERE slug = 'comum'), 'daily', NULL);
INSERT INTO pack_opening_item (pack_grant_id, position, sticker_id, is_new) VALUES (1, 0, 1, true), (1, 1, 1, false), (1, 2, 3, true);
INSERT INTO user_sticker (user_id, sticker_id, quantity, pasted) VALUES (1, 1, 2, true), (1, 3, 1, false), (2, 2, 3, false);
INSERT INTO trade (id, token, creator_id, partner_id, state, expires_at) VALUES (1, 'tok-1', 1, 2, 'done', now()), (2, 'tok-2', 2, NULL, 'open', now());
INSERT INTO trade_offer (trade_id, user_id, sticker_id, quantity) VALUES (1, 1, 1, 1), (1, 2, 2, 1);
INSERT INTO redeem_code (id, code, pack_type_id, label, redeemed_by) VALUES
  (1, 'AAAA-BBBB-CCCC-DDDD', (SELECT id FROM pack_type WHERE slug = 'premium'), 'biblioteca', 1),
  (2, 'EEEE-FFFF-GGGG-HHHH', (SELECT id FROM pack_type WHERE slug = 'comum'), '', NULL);
"""


@pytest.fixture
def scratch():
    """Um banco vazio só para este teste, apagado no fim."""
    url = main_engine.url.set(database=DATABASE)
    admin = create_engine(main_engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {DATABASE}"))
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        admin.dispose()


def _migrate(engine, action, revision: str) -> None:
    config = AlembicConfig(BACKEND / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        action(config, revision)


def _counts(conn) -> dict[str, int]:
    return {table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() for table in TABLES}


def _snapshot(conn, sql: str) -> list[tuple]:
    return [tuple(row) for row in conn.execute(text(sql))]


def test_0004_sobre_campanha_existente_sobe_e_desce(scratch):
    _migrate(scratch, command.upgrade, "0003")
    with scratch.begin() as conn:
        conn.execute(text(OLD_DATA))
    with scratch.connect() as conn:
        before = _counts(conn)
        timers = _snapshot(conn, 'SELECT id, next_pack_at, last_daily_grant_on FROM "user" ORDER BY id')
        mosaics = _snapshot(conn, "SELECT id, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows FROM org ORDER BY id")
        inventory = _snapshot(conn, "SELECT user_id, sticker_id, quantity, pasted FROM user_sticker ORDER BY 1, 2")
        old_types = _snapshot(conn, "SELECT id, slug FROM pack_type ORDER BY id")

    _migrate(scratch, command.upgrade, "head")
    with scratch.connect() as conn:
        assert _counts(conn) == before  # nenhuma linha de jogo ou de conteúdo sumiu
        assert _snapshot(conn, "SELECT user_id, sticker_id, quantity, pasted FROM user_sticker ORDER BY 1, 2") == inventory
        assert _snapshot(conn, "SELECT name, slug, active FROM university") == [("Universidade Padrão", "padrao", True)]
        albums = _snapshot(conn, "SELECT id, title, state, starts_at <= now(), (ends_at - starts_at) = interval '90 days' FROM album")
        assert len(albums) == 1 and albums[0][1:] == ("Calourada", "ready", True, True)
        album_id = albums[0][0]
        for table in ALBUM_TABLES:
            assert _snapshot(conn, f"SELECT DISTINCT album_id FROM {table}") == [(album_id,)], table
        assert conn.execute(text('SELECT count(*) FROM "user" WHERE university_id IS NULL')).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM org WHERE university_id IS NULL")).scalar() == 0
        # Um capítulo por org, com a ordem e o mosaico que estavam na org.
        assert _snapshot(conn, "SELECT org_id, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows FROM album_chapter ORDER BY org_id") == mosaics
        # Participação para cada usuário, com o timer da gaveta copiado.
        assert _snapshot(conn, "SELECT user_id, next_pack_at, last_daily_grant_on FROM album_participation ORDER BY user_id") == timers
        assert conn.execute(text("SELECT count(*) FROM album_participation WHERE final_stats IS NOT NULL")).scalar() == 0
        # Os tipos de pacote antigos são do álbum (pacotes e códigos continuam apontando para eles),
        # com pesos completos; e há uma cópia como modelo global.
        assert _snapshot(conn, "SELECT id, slug FROM pack_type WHERE album_id IS NOT NULL ORDER BY id") == old_types
        assert _snapshot(conn, "SELECT slug, rarity_weights FROM pack_type WHERE album_id IS NULL ORDER BY slug") == [
            ("comum", {"1": 80, "2": 17, "3": 3}), ("premium", {"1": 60, "2": 32, "3": 8}),
        ]
        assert _snapshot(conn, "SELECT slug, rarity_weights FROM pack_type WHERE album_id IS NOT NULL ORDER BY slug") == [
            ("comum", {"1": 80, "2": 17, "3": 3}), ("premium", {"1": 60, "2": 32, "3": 8}),
        ]
        # As colunas antigas continuam lá até a 0006... que já rodou no head: sumiram só no fim.
        constraints = {row[0] for row in conn.execute(text("SELECT conname FROM pg_constraint"))}
        assert {"ex_album_ready_overlap", "ck_album_dates", "uq_sticker_album_number"} <= constraints

    # Caminho de volta, uma migration por vez, até o esquema de campanha única.
    _migrate(scratch, command.downgrade, "0003")
    with scratch.connect() as conn:
        assert _counts(conn) == before
        assert _snapshot(conn, 'SELECT id, next_pack_at, last_daily_grant_on FROM "user" ORDER BY id') == timers
        assert _snapshot(conn, "SELECT id, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows FROM org ORDER BY id") == mosaics
        assert _snapshot(conn, "SELECT id, slug FROM pack_type ORDER BY id") == old_types
        leftovers = _snapshot(
            conn,
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('album_id', 'university_id')",
        )
        assert leftovers == []
        tables = {row[0] for row in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))}
        assert not tables & {"album", "university", "album_chapter", "album_participation", "album_config"}

    _migrate(scratch, command.upgrade, "head")
    with scratch.connect() as conn:
        assert _counts(conn) == before
        assert conn.execute(text("SELECT count(*) FROM album")).scalar() == 1


def test_0004_em_banco_vazio_nao_inventa_album(scratch):
    _migrate(scratch, command.upgrade, "head")
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM university")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM album")).scalar() == 0
        # Sem campanha para herdar, os tipos de pacote ficam só como modelo, já com pesos completos.
        assert _snapshot(conn, "SELECT album_id, slug, rarity_weights FROM pack_type ORDER BY slug") == [
            (None, "comum", {"1": 80, "2": 17, "3": 3}), (None, "premium", {"1": 60, "2": 32, "3": 8}),
        ]
    _migrate(scratch, command.downgrade, "0003")
    _migrate(scratch, command.upgrade, "head")


def test_cada_migration_desce_e_sobe(scratch):
    _migrate(scratch, command.upgrade, "0003")
    with scratch.begin() as conn:
        conn.execute(text(OLD_DATA))
    _migrate(scratch, command.upgrade, "head")
    with scratch.connect() as conn:
        head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    steps = int(head) - 3
    for _ in range(steps):
        _migrate(scratch, command.downgrade, "-1")
        _migrate(scratch, command.upgrade, "+1")
        _migrate(scratch, command.downgrade, "-1")
    with scratch.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0003"
    _migrate(scratch, command.upgrade, "head")


def test_downgrade_recusa_quando_ha_mais_de_um_album(scratch):
    _migrate(scratch, command.upgrade, "0005")
    with scratch.begin() as conn:
        conn.execute(text(
            "INSERT INTO album (university_id, title, starts_at, ends_at) "
            "SELECT id, t, now(), now() + interval '1 day' FROM university, (VALUES ('A'), ('B')) v(t)"
        ))
    with pytest.raises(RuntimeError, match="mais de um álbum"):
        _migrate(scratch, command.downgrade, "0003")
