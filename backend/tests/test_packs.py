import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import AuditLog, Config, PackGrant, PackOpeningItem, PackType, Rarity, Sticker, UserSticker
from app.services import packs
from app.services.packs import draw_stickers, grant_daily_if_due

SP = ZoneInfo("America/Sao_Paulo")


def _pack(db, slug):
    return db.scalar(select(PackType).where(PackType.slug == slug))


def _at(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=SP)


def test_distribuicao_segue_os_pesos(db, content):
    rng = random.Random(42)
    rarities = db.scalars(select(Rarity)).all()
    stickers = db.scalars(select(Sticker)).all()
    pack = _pack(db, "comum")
    counts = Counter()
    for _ in range(4000):
        for sticker in draw_stickers(pack, rarities, stickers, rng):
            counts[sticker.rarity.name] += 1
    total = sum(counts.values())
    # A troca por figurinha inédita não muda a raridade; os pesos 80/17/3 valem direto.
    assert abs(counts["comum"] / total - 0.80) < 0.015
    assert abs(counts["rara"] / total - 0.17) < 0.015
    assert abs(counts["lendária"] / total - 0.03) < 0.01


def test_premium_garante_ao_menos_uma_rara(db, content):
    rng = random.Random(7)
    rarities = db.scalars(select(Rarity)).all()
    stickers = db.scalars(select(Sticker)).all()
    pack = _pack(db, "premium")
    pack.rarity_weights = {"1": 100, "2": 0, "3": 0}  # só a garantia pode trazer rara
    for _ in range(500):
        drawn = draw_stickers(pack, rarities, stickers, rng)
        assert len(drawn) == 7
        assert sum(s.rarity.rank >= 2 for s in drawn) == 1
    db.rollback()


def test_filtro_por_org(db, content):
    from app.models import Org

    other = Org(name="Outra")
    db.add(other)
    db.flush()
    db.add(Sticker(org_id=other.id, number=999, name="X", rarity_id=1, back_links=[], slot_layout={}))
    db.flush()
    pack = _pack(db, "comum")
    pack.org_filter = other.id
    drawn = draw_stickers(pack, db.scalars(select(Rarity)).all(), db.scalars(select(Sticker)).all())
    assert {s.org_id for s in drawn} == {other.id}
    db.rollback()


def _daily_stock(db, user):
    return db.scalar(
        select(func.count()).select_from(PackGrant).where(
            PackGrant.user_id == user.id, PackGrant.source == "daily", PackGrant.opened_at.is_(None)
        )
    )


def test_concessao_uma_vez_por_dia_e_virada_em_sao_paulo(db, make_user):
    user = make_user("Dia")
    with patch.object(packs, "now", lambda: _at(2026, 3, 10, 23)):
        assert grant_daily_if_due(db, user) == 2
        assert grant_daily_if_due(db, user) == 0
    # 23h59 em São Paulo já é dia seguinte em UTC, mas não é virada para o jogo.
    with patch.object(packs, "now", lambda: datetime(2026, 3, 11, 2, 59, tzinfo=ZoneInfo("UTC"))):
        assert grant_daily_if_due(db, user) == 0
    with patch.object(packs, "now", lambda: datetime(2026, 3, 11, 3, 1, tzinfo=ZoneInfo("UTC"))):
        assert grant_daily_if_due(db, user) == 2


def test_teto_descarta_o_excedente(db, make_user):
    user = make_user("Teto")
    for day in (1, 2, 3, 4):
        with patch.object(packs, "now", lambda d=day: _at(2026, 3, d)):
            grant_daily_if_due(db, user)
    assert _daily_stock(db, user) == 4
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "pack.daily_discarded")) == 2


def test_teto_nao_conta_pacotes_de_codigo(db, make_user):
    user = make_user("Cod")
    premium = _pack(db, "premium")
    for _ in range(5):
        packs.grant_pack(db, user.id, premium.id, "code", "x")
    db.commit()
    with patch.object(packs, "now", lambda: _at(2026, 3, 1)):
        assert grant_daily_if_due(db, user) == 2


def test_premium_em_dias_alternados(db, make_user):
    def premium_on(day: date, nickname: str) -> int:
        user = make_user(nickname)
        with patch.object(packs, "now", lambda: datetime(day.year, day.month, day.day, 12, tzinfo=SP)):
            grant_daily_if_due(db, user)
        rows = db.scalars(select(PackGrant).where(PackGrant.user_id == user.id)).all()
        return sum(row.pack_type.slug == "premium" for row in rows)

    assert sorted([premium_on(date(2026, 3, 1), "D1"), premium_on(date(2026, 3, 2), "D2")]) == [0, 1]


def test_mudanca_de_config_vale_sem_codigo(db, make_user):
    db.get(Config, "daily.pack_count").value = 3
    db.commit()
    try:
        with patch.object(packs, "now", lambda: _at(2026, 3, 1)):
            assert grant_daily_if_due(db, make_user("Cfg")) == 3
    finally:
        db.get(Config, "daily.pack_count").value = 2
        db.commit()


def test_abertura_nova_e_repetida(db, content, make_user, login):
    user = make_user("Abre")
    client = login(user)
    stock = client.get("/api/packs").json()["stock"]
    result = client.post(f"/api/packs/{stock[0]['id']}/open").json()
    assert len(result["items"]) >= 6
    ids = [item["id"] for item in result["items"]]
    assert all(item["is_new"] for item in result["items"]) == (len(ids) == len(set(ids)))
    total = db.scalar(select(func.sum(UserSticker.quantity)).where(UserSticker.user_id == user.id))
    assert total == len(ids)

    second = client.post(f"/api/packs/{stock[1]['id']}/open").json()
    for item in second["items"]:
        if item["id"] in ids:
            assert item["is_new"] is False


def test_nao_abre_pacote_de_outro_usuario(db, content, make_user, login):
    owner = login(make_user("Dono"))
    grant = owner.get("/api/packs").json()["stock"][0]["id"]
    assert login(make_user("Alheio")).post(f"/api/packs/{grant}/open").status_code == 404


def test_abertura_simultanea_abre_uma_vez_so(db, content, make_user, login):
    user = make_user("Corrida")
    client = login(user)
    grant = client.get("/api/packs").json()["stock"][0]
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: client.post(f"/api/packs/{grant['id']}/open").status_code, range(8)))
    assert sorted(codes) == [200] + [409] * 7
    assert db.scalar(
        select(func.count()).select_from(PackOpeningItem).where(PackOpeningItem.pack_grant_id == grant["id"])
    ) == grant["stickers_per_pack"]
    assert db.scalar(select(func.sum(UserSticker.quantity)).where(UserSticker.user_id == user.id)) == grant["stickers_per_pack"]
