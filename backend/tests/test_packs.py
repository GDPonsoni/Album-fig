import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import Config, PackGrant, PackOpeningItem, PackType, Rarity, Sticker, UserSticker
from app.services import packs
from app.services.packs import daily_kind, draw_stickers, next_pack_kind, refill_drawer

SP = ZoneInfo("America/Sao_Paulo")
REFILL = timedelta(hours=12)


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


def _clock(moment):
    return patch.object(packs, "now", lambda: moment)


def test_conta_nova_recebe_boas_vindas_e_o_timer_comeca(db, make_user):
    user = make_user("Nova")
    start = _at(2026, 3, 10)
    with _clock(start):
        assert refill_drawer(db, user) == 2
        assert refill_drawer(db, user) == 0
    assert user.next_pack_at == start + REFILL


def test_conta_nao_confirmada_nao_recebe(db, make_user):
    user = make_user("Sem", verified=False)
    assert refill_drawer(db, user) == 0
    assert user.next_pack_at is None


def test_entrega_quando_o_timer_acaba_e_o_ciclo_segue_com_a_gaveta_cheia(db, make_user):
    user = make_user("Timer")
    start = _at(2026, 3, 10)
    with _clock(start):
        refill_drawer(db, user)
    with _clock(start + REFILL - timedelta(seconds=1)):
        assert refill_drawer(db, user) == 0
    with _clock(start + REFILL):
        assert refill_drawer(db, user) == 1
    assert _daily_stock(db, user) == 3
    assert user.next_pack_at == start + 2 * REFILL


def _open_daily(db, user):
    grant = db.scalar(
        select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.source == "daily", PackGrant.opened_at.is_(None))
    )
    packs.open_pack(db, user, grant.id)
    db.commit()


def test_abrir_pacote_nao_mexe_no_timer(db, content, make_user):
    user = make_user("Mantem")
    start = _at(2026, 3, 10)
    with _clock(start):
        refill_drawer(db, user)
    with _clock(start + REFILL):
        refill_drawer(db, user)
    # Gaveta cheia: o ciclo continua, então abrir no meio dele aproveita o tempo já corrido.
    with _clock(start + REFILL + timedelta(hours=11)):
        _open_daily(db, user)
    assert user.next_pack_at == start + 2 * REFILL
    with _clock(start + 2 * REFILL):
        assert refill_drawer(db, user) == 1


def test_ciclo_com_a_gaveta_cheia_nao_entrega_e_mantem_o_ritmo(db, make_user):
    user = make_user("Ritmo")
    start = _at(2026, 3, 10)
    with _clock(start):
        refill_drawer(db, user)
    with _clock(start + 5 * REFILL + timedelta(hours=1)):
        assert refill_drawer(db, user) == 1
    assert _daily_stock(db, user) == 3
    assert user.next_pack_at == start + 6 * REFILL


def test_com_mais_de_um_espaco_o_timer_emenda(db, content, make_user):
    user = make_user("Emenda")
    start = _at(2026, 3, 10)
    with _clock(start):
        refill_drawer(db, user)
        _open_daily(db, user)
        _open_daily(db, user)
    assert _daily_stock(db, user) == 0
    with _clock(start + REFILL):
        assert refill_drawer(db, user) == 1
    assert user.next_pack_at == start + 2 * REFILL


def test_tempo_fora_do_app_conta_ate_encher(db, content, make_user):
    user = make_user("Fora")
    start = _at(2026, 3, 10)
    with _clock(start):
        refill_drawer(db, user)
        _open_daily(db, user)
        _open_daily(db, user)
    with _clock(start + timedelta(days=30)):
        assert refill_drawer(db, user) == 3
    assert _daily_stock(db, user) == 3
    assert user.next_pack_at > start + timedelta(days=30)


def test_gaveta_nao_conta_pacotes_de_codigo(db, make_user):
    user = make_user("Cod")
    premium = _pack(db, "premium")
    for _ in range(5):
        packs.grant_pack(db, user.id, premium.id, "code", "x")
    db.commit()
    with _clock(_at(2026, 3, 1)):
        assert refill_drawer(db, user) == 2


def test_premium_em_dias_alternados(db, make_user):
    def premium_on(day: date, nickname: str) -> int:
        user = make_user(nickname)
        with _clock(datetime(day.year, day.month, day.day, 12, tzinfo=SP)):
            refill_drawer(db, user)
        rows = db.scalars(select(PackGrant).where(PackGrant.user_id == user.id)).all()
        return sum(row.pack_type.slug == "premium" for row in rows)

    assert sorted([premium_on(date(2026, 3, 1), "D1"), premium_on(date(2026, 3, 2), "D2")]) == [0, 1]


def test_mudanca_de_config_vale_sem_codigo(db, make_user):
    db.get(Config, "daily.welcome_packs").value = 3
    db.commit()
    try:
        with _clock(_at(2026, 3, 1)):
            assert refill_drawer(db, make_user("Cfg")) == 3
    finally:
        db.get(Config, "daily.welcome_packs").value = 2
        db.commit()


def test_rota_informa_o_timer(db, make_user, login):
    body = login(make_user("Rota")).get("/api/packs").json()
    assert body["daily_cap"] == 3
    assert body["daily_in_stock"] == 2
    assert body["next_pack_at"] > body["server_now"]


def test_atalhos_de_teste(db, make_user, login):
    user = make_user("Dev")
    client = login(user)
    assert client.post("/api/dev/packs", json={"slug": "premium"}).status_code == 201
    code = client.post("/api/dev/codes", json={"slug": "comum"}).json()["code"]
    assert client.post("/api/codes/redeem", json={"code": code}).status_code == 200
    body = client.get("/api/packs").json()
    assert body["dev_tools"] is True
    assert sorted(g["source"] for g in body["stock"]) == ["admin", "code", "daily", "daily"]
    assert client.post("/api/dev/packs", json={"slug": "nao-existe"}).status_code == 404


def test_atalhos_de_teste_exigem_conta_confirmada(db, make_user, login):
    client = login(make_user("Pendente", verified=False))
    assert client.post("/api/dev/packs", json={"slug": "comum"}).status_code == 403


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


def test_historico_lista_os_ultimos_pacotes_abertos(db, content, make_user, login):
    user = make_user("Hist")
    client = login(user)
    assert client.get("/api/packs/history").json() == []
    stock = client.get("/api/packs").json()["stock"]
    first = client.post(f"/api/packs/{stock[0]['id']}/open").json()
    second = client.post(f"/api/packs/{stock[1]['id']}/open").json()

    history = client.get("/api/packs/history").json()
    assert [h["pack"]["id"] for h in history] == [stock[1]["id"], stock[0]["id"]]
    assert history[0]["opened_at"] is not None
    for entry, opened in zip(history, [second, first]):
        assert [(i["id"], i["is_new"]) for i in entry["items"]] == [(i["id"], i["is_new"]) for i in opened["items"]]
    # Pacote fechado não aparece; o limite é respeitado e validado.
    assert len(client.get("/api/packs/history?limit=1").json()) == 1
    assert client.get("/api/packs/history?limit=0").status_code == 422
    assert client.get("/api/packs/history?limit=500").status_code == 422


def test_historico_e_so_do_proprio_usuario(db, content, make_user, login):
    owner = login(make_user("Dona"))
    grant = owner.get("/api/packs").json()["stock"][0]["id"]
    owner.post(f"/api/packs/{grant}/open")
    assert login(make_user("Curioso")).get("/api/packs/history").json() == []


# ---- previsão do próximo pacote (mesma regra da entrega)

PREMIUM_DAY = date(2026, 3, 3)  # toordinal par: com every=2 é dia de premium
COMMON_DAY = date(2026, 3, 2)


def test_regra_de_escolha_do_pacote():
    assert PREMIUM_DAY.toordinal() % 2 == 0 and COMMON_DAY.toordinal() % 2 == 1
    assert daily_kind(PREMIUM_DAY, None, 2, True) == "premium"
    assert daily_kind(PREMIUM_DAY, COMMON_DAY, 2, True) == "premium"
    assert daily_kind(PREMIUM_DAY, PREMIUM_DAY, 2, True) == "common"  # já entregou hoje
    assert daily_kind(COMMON_DAY, None, 2, True) == "common"
    assert daily_kind(PREMIUM_DAY, None, 0, True) == "common"
    assert daily_kind(PREMIUM_DAY, None, -3, True) == "common"
    assert daily_kind(PREMIUM_DAY, None, 2, False) == "common"  # premium inexistente
    assert daily_kind(COMMON_DAY, None, 1, True) == "premium"  # todo dia


def _prediction(db, user, next_at, last_on):
    user.next_pack_at, user.last_daily_grant_on = next_at, last_on
    return next_pack_kind(db, user)


def test_previsao_na_virada_da_meia_noite(db, make_user):
    user = make_user("Virada")
    before = datetime(2026, 3, 2, 23, 59, tzinfo=SP)  # ainda dia comum
    after = datetime(2026, 3, 3, 0, 1, tzinfo=SP)  # já é dia de premium
    assert _prediction(db, user, before, COMMON_DAY)["is_premium"] is False
    kind = _prediction(db, user, after, COMMON_DAY)
    assert kind == {"slug": "premium", "name": kind["name"], "is_premium": True}
    # A data é a local (America/Sao_Paulo), não a UTC: 02:30 UTC do dia 3 ainda é dia 2 em SP.
    utc_next_day = datetime(2026, 3, 3, 2, 30, tzinfo=ZoneInfo("UTC"))
    assert _prediction(db, user, utc_next_day, COMMON_DAY)["is_premium"] is False
    db.rollback()


def test_previsao_quando_ja_recebeu_hoje(db, make_user):
    user = make_user("Hoje")
    assert _prediction(db, user, datetime(2026, 3, 3, 18, tzinfo=SP), PREMIUM_DAY)["is_premium"] is False
    db.rollback()


def test_previsao_sem_timer_e_com_premium_desligado_ou_inexistente(db, make_user):
    user = make_user("Sem2")
    assert next_pack_kind(db, user) is None
    at = datetime(2026, 3, 3, 12, tzinfo=SP)
    every = db.get(Config, "daily.premium_every_n_days")
    slug = db.get(Config, "daily.premium_pack_slug")
    old_every, old_slug = every.value, slug.value
    try:
        for value in (0, -1):
            every.value = value
            db.flush()
            assert _prediction(db, user, at, None) == {"slug": "comum", "name": _pack(db, "comum").name, "is_premium": False}
        every.value = old_every
        slug.value = "nao-existe"
        db.flush()
        assert _prediction(db, user, at, None)["slug"] == "comum"
    finally:
        db.rollback()
        assert db.get(Config, "daily.premium_every_n_days").value == old_every
        assert db.get(Config, "daily.premium_pack_slug").value == old_slug


def test_previsao_bate_com_a_entrega_real(db, content, make_user):
    """Para vários inícios, o que a previsão anuncia é o que a gaveta entrega no instante do timer."""
    for offset in range(6):
        user = make_user(f"Bate{offset}")
        start = datetime(2026, 3, 1, 3, tzinfo=SP) + timedelta(hours=7 * offset)
        with _clock(start):
            refill_drawer(db, user)
            _open_daily(db, user)
        for _ in range(4):
            predicted = next_pack_kind(db, user)
            before = set(db.scalars(select(PackGrant.id).where(PackGrant.user_id == user.id)))
            with _clock(user.next_pack_at):
                assert refill_drawer(db, user) == 1
                new = db.scalars(select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.id.not_in(before))).one()
                assert new.pack_type.slug == predicted["slug"]
                _open_daily(db, user)


def test_acumulo_offline_entrega_um_premium_so(db, content, make_user):
    user = make_user("Acumulo")
    with _clock(datetime(2026, 3, 2, 12, tzinfo=SP)):
        refill_drawer(db, user)
        _open_daily(db, user)
        _open_daily(db, user)
    # Volta dias depois, num dia de premium: três pacotes com a mesma data, só o primeiro é premium.
    with _clock(datetime(2026, 3, 7, 12, tzinfo=SP)):
        assert refill_drawer(db, user) == 3
    slugs = [g.pack_type.slug for g in db.scalars(
        select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.opened_at.is_(None)).order_by(PackGrant.id)
    )]
    assert slugs == ["premium", "comum", "comum"]


def test_rota_informa_o_tipo_do_proximo_pacote(db, make_user, login):
    body = login(make_user("Tipo")).get("/api/packs").json()
    assert set(body["next_pack_kind"]) == {"slug", "name", "is_premium"}
