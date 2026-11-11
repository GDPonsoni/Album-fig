"""Fundação multi-álbum: contexto de álbum, isolamento, fechamento e imutabilidade."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Album,
    AlbumChapter,
    AlbumConfig,
    AlbumParticipation,
    AuditLog,
    Config,
    PackGrant,
    PackType,
    Rarity,
    RedeemCode,
    Sticker,
    Trade,
    University,
    User,
    UserSticker,
)
from app.services import albums
from app.services.codes import generate_code
from app.services.core import cfg, now
from app.services.packs import grant_pack, refill_drawer
from tests.conftest import PASSWORD_HASH, add_content, pack_of, quantity_of, set_period


def _iso(days: float) -> str:
    return (now() + timedelta(days=days)).isoformat()


def _code(db, album) -> str:
    value = generate_code()
    db.add(RedeemCode(code=value, album_id=album.id, pack_type_id=pack_of(db, album, "comum").id, label="t"))
    db.commit()
    return value


# ---- estado derivado das datas


def test_estado_do_album_sai_das_datas(db, album):
    row = db.get(Album, album.id)
    assert albums.album_status(row) == "active"
    assert albums.album_status(row, row.starts_at - timedelta(seconds=1)) == "scheduled"
    assert albums.album_status(row, row.starts_at) == "active"  # o início é inclusivo
    assert albums.album_status(row, row.ends_at - timedelta(seconds=1)) == "active"
    assert albums.album_status(row, row.ends_at) == "ended"  # o fim é exclusivo
    row.state = "draft"
    assert albums.album_status(row) == "draft"
    db.rollback()


# ---- um álbum pronto por vez por universidade (imposto no banco)


def test_banco_recusa_dois_albuns_prontos_sobrepostos(db, album, make_album):
    with pytest.raises(IntegrityError) as error:
        make_album("Sobreposto", 30, 120)
    assert "ex_album_ready_overlap" in str(error.value.orig)
    db.rollback()
    # Rascunho pode sobrepor; e o seguinte pode começar no instante em que o atual acaba.
    draft = make_album("Rascunho", 30, 120, state="draft")
    current = db.get(Album, album.id)
    nxt = Album(university_id=current.university_id, title="Seguinte", starts_at=current.ends_at,
                ends_at=current.ends_at + timedelta(days=90), state="ready")
    db.add(nxt)
    db.commit()
    # Promover o rascunho sobreposto é recusado.
    db.get(Album, draft.id).state = "ready"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_banco_recusa_fim_antes_do_inicio(db, album):
    db.add(Album(university_id=album.university_id, title="Invertido", starts_at=now(), ends_at=now() - timedelta(days=1)))
    with pytest.raises(IntegrityError) as error:
        db.commit()
    assert "ck_album_dates" in str(error.value.orig)
    db.rollback()


def test_outra_universidade_pode_ter_album_no_mesmo_periodo(db, album):
    other = University(name="Outra", slug="outra")
    db.add(other)
    db.flush()
    db.add(Album(university_id=other.id, title="Paralelo", starts_at=album.starts_at, ends_at=album.ends_at, state="ready"))
    db.commit()


def test_admin_recebe_mensagem_amigavel_na_sobreposicao(db, album, make_user, login):
    admin = login(make_user("Adm", admin=True))
    body = {"title": "Outro", "starts_at": _iso(10), "ends_at": _iso(100), "state": "ready"}
    response = admin.post("/api/admin/albums", json=body)
    assert response.status_code == 409 and "Já existe um álbum pronto nesse período" in response.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Album)) == 1  # nada ficou pela metade
    assert db.scalar(select(func.count()).select_from(PackType).where(PackType.album_id.is_not(None))) == 2
    # Como rascunho passa; promover depois é recusado com a mesma mensagem.
    created = admin.post("/api/admin/albums", json={**body, "state": "draft"})
    assert created.status_code == 201 and created.json()["status"] == "draft"
    promote = admin.put(f"/api/admin/albums/{created.json()['id']}", json=body)
    assert promote.status_code == 409 and "Já existe um álbum pronto" in promote.json()["detail"]
    assert admin.post("/api/admin/albums", json={**body, "ends_at": _iso(5)}).status_code == 422


# ---- escrita de jogo só com álbum em andamento


def _player_with_stuff(db, album, content, make_user, give, login, nickname="Joga"):
    user = make_user(nickname)
    client = login(user)
    give(user, content[0], 2)
    grant = client.get("/api/packs").json()["stock"][0]["id"]
    return user, client, grant


@pytest.mark.parametrize("period,detail", [((-30, -1), "Álbum encerrado."), ((1, 30), "Nenhum álbum em andamento.")])
def test_album_encerrado_ou_futuro_recusa_toda_escrita(db, album, content, make_user, give, login, period, detail):
    user, client, grant = _player_with_stuff(db, album, content, make_user, give, login)
    code = _code(db, album)
    other = make_user("Par")
    give(other, content[1], 2)
    token = client.post("/api/trades").json()["token"]
    set_period(db, album, *period)

    for response in (
        client.post(f"/api/packs/{grant}/open"),
        client.post(f"/api/album/paste/{content[0].id}"),
        client.post("/api/album/paste-all"),
        client.post("/api/trades"),
        client.post("/api/codes/redeem", json={"code": code}),
        client.get("/api/codes/preview", params={"code": code}),
        client.post("/api/dev/packs", json={"slug": "comum"}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == detail
    assert login(other).post(f"/api/trades/{token}/join").status_code == 409

    # Nada mudou no banco.
    db.expire_all()
    assert db.get(PackGrant, grant).opened_at is None
    assert db.get(UserSticker, (user.id, content[0].id)).pasted is False
    assert db.scalar(select(RedeemCode).where(RedeemCode.code == code)).redeemed_at is None
    assert db.scalar(select(func.count()).select_from(PackGrant).where(PackGrant.user_id == user.id)) == 2

    # Leituras não quebram: sem álbum em andamento, índice e gaveta vazios.
    index = client.get("/api/album").json()
    assert index["album"] is None and index["pages"] == [] and index["tray"] == []
    packs = client.get("/api/packs").json()
    assert packs["album"] is None and packs["stock"] == [] and packs["next_pack_at"] is None
    assert client.get("/api/packs/history").json() == []
    assert client.get("/api/inventory/duplicates").json() == []
    assert client.get("/api/trades/current").json() == {"token": None}


def test_gaveta_nao_entrega_fora_do_periodo(db, album, make_user):
    user = make_user("Cedo")
    set_period(db, album, 1, 30)
    assert refill_drawer(db, user) == 0
    assert db.scalar(select(func.count()).select_from(AlbumParticipation)) == 0
    set_period(db, album, -1, 30)
    assert refill_drawer(db, user) == 2


# ---- isolamento entre álbuns


@pytest.fixture
def two_albums(db, album, content, make_user, give, login):
    """O álbum A já acabou (o jogador tem figurinhas e um pacote fechado nele); o B está em andamento."""
    user = make_user("Dois")
    client = login(user)
    old_grant = client.get("/api/packs").json()["stock"][0]["id"]
    give(user, content[0], 3)
    give(user, content[1], 1, pasted=True)
    old_code = _code(db, album)
    set_period(db, album, -100, -10)
    new_album = albums.create_album(db, album.university_id, "Inverno", now() - timedelta(days=1), now() + timedelta(days=60), state="ready")
    db.commit()
    new_content = add_content(db, new_album, "Org Nova")
    return user, client, album, content, new_album, new_content, old_grant, old_code


def test_inventario_nao_vaza_entre_albuns(db, two_albums, give):
    user, client, old, old_content, new, new_content, old_grant, old_code = two_albums
    give(user, new_content[0], 2)

    index = client.get("/api/album").json()
    assert index["album"]["id"] == new.id and index["album"]["title"] == "Inverno"
    assert index["total"] == 17 and index["filled"] == 0
    assert [t["id"] for t in index["tray"]] == [new_content[0].id]
    assert [o["name"] for o in index["orgs"]] == ["Org Nova"]
    assert [d["id"] for d in client.get("/api/inventory/duplicates").json()] == [new_content[0].id]

    # Página do capítulo antigo não existe no álbum atual.
    assert client.get(f"/api/album/orgs/{old_content[0].org_id}/pages/1").status_code == 404
    # Colar tudo só cola o álbum atual; a figurinha do álbum antigo fica como estava.
    assert client.post("/api/album/paste-all").json() == {"pasted": [new_content[0].id]}
    db.expire_all()
    assert db.get(UserSticker, (user.id, old_content[0].id)).pasted is False
    assert client.post(f"/api/album/paste/{old_content[0].id}").json()["detail"] == "Álbum encerrado."


def test_pacote_e_codigo_do_album_antigo_nao_valem_no_novo(db, two_albums):
    user, client, old, old_content, new, new_content, old_grant, old_code = two_albums
    # A gaveta é nova no álbum novo: boas-vindas de novo, e o pacote antigo não aparece.
    stock = client.get("/api/packs").json()["stock"]
    assert len(stock) == 2 and old_grant not in [g["id"] for g in stock]
    assert client.post(f"/api/packs/{old_grant}/open").json()["detail"] == "Álbum encerrado."
    # Código do álbum antigo é só um código inválido (não revela que existiu).
    assert client.post("/api/codes/redeem", json={"code": old_code}).json() == {"detail": "Código inválido."}
    assert client.get("/api/codes/preview", params={"code": old_code}).status_code == 400
    assert client.post("/api/codes/redeem", json={"code": _code(db, new)}).status_code == 200
    parts = {p.album_id for p in db.scalars(select(AlbumParticipation).where(AlbumParticipation.user_id == user.id))}
    assert parts == {old.id, new.id}


def test_sorteio_so_usa_figurinhas_e_pacotes_do_album(db, two_albums):
    user, client, old, old_content, new, new_content, old_grant, old_code = two_albums
    new_ids = {s.id for s in new_content}
    for grant in client.get("/api/packs").json()["stock"]:
        opened = client.post(f"/api/packs/{grant['id']}/open").json()
        assert {item["id"] for item in opened["items"]} <= new_ids
    grants = db.scalars(select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.album_id == new.id)).all()
    assert {g.pack_type.album_id for g in grants} == {new.id}
    # Histórico também é por álbum.
    assert len(client.get("/api/packs/history").json()) == 2


def test_troca_nao_aceita_figurinha_de_outro_album(db, two_albums, make_user, give, login):
    user, client, old, old_content, new, new_content, *_ = two_albums
    partner = make_user("Parceira")
    give(partner, new_content[1], 2)
    give(user, new_content[0], 2)
    token = client.post("/api/trades").json()["token"]
    assert login(partner).post(f"/api/trades/{token}/join").status_code == 200
    # O jogador tem 3 da figurinha antiga (2 repetidas), mas ela é de outro álbum.
    bad = client.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": old_content[0].id, "quantity": 1}]})
    assert bad.status_code == 409
    good = client.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": new_content[0].id, "quantity": 1}]})
    assert good.status_code == 200
    assert db.scalar(select(Trade.album_id).where(Trade.token == token)) == new.id


def test_numero_de_figurinha_e_unico_por_album_nao_global(db, two_albums):
    *_, old, old_content, new, new_content, _, _ = two_albums
    assert old_content[0].number == new_content[0].number == 1
    db.add(Sticker(album_id=new.id, org_id=new_content[0].org_id, number=1, name="Dup", rarity_id=1, back_links=[], slot_layout={}))
    with pytest.raises(IntegrityError) as error:
        db.commit()
    assert "uq_sticker_album_number" in str(error.value.orig)
    db.rollback()


def test_album_de_outra_universidade_nao_existe_para_o_usuario(db, album, content, make_user, login):
    other = University(name="Outra", slug="outra")
    db.add(other)
    db.flush()
    stranger = User(email="fora@example.com", nickname="Fora", password_hash=PASSWORD_HASH, email_verified_at=now(), university_id=other.id)
    db.add(stranger)
    db.commit()
    client = login(stranger)
    assert client.get("/api/album").json()["album"] is None
    assert client.get(f"/api/albums/{album.id}").status_code == 404
    assert client.get(f"/api/albums/{album.id}/summary").status_code == 404
    assert client.get(f"/api/albums/{album.id}/orgs/{content[0].org_id}/pages/1").status_code == 404
    assert client.get(f"/api/album/orgs/{content[0].org_id}/pages/1").status_code == 404
    assert client.post("/api/trades").json()["detail"] == "Nenhum álbum em andamento."
    assert client.get("/api/packs").json()["stock"] == []
    assert client.get("/api/albums/mine").json()["active"] is None


# ---- parâmetros por álbum


def test_parametro_do_album_sobrescreve_o_global_so_naquele_album(db, album, make_album, make_user):
    assert cfg(db, "daily.welcome_packs", album.id) == 2
    db.add(AlbumConfig(album_id=album.id, key="daily.welcome_packs", value=3))
    db.add(AlbumConfig(album_id=album.id, key="code.max_errors_per_hour", value=999))  # não é parâmetro de álbum
    db.commit()
    other = make_album("Rascunho", 200, 290, state="draft")
    assert cfg(db, "daily.welcome_packs", album.id) == 3
    assert cfg(db, "daily.welcome_packs", other.id) == 2
    assert cfg(db, "daily.welcome_packs") == 2
    assert cfg(db, "code.max_errors_per_hour", album.id) == 10
    assert refill_drawer(db, make_user("Tres")) == 3


def test_limite_e_ritmo_da_gaveta_por_album(db, album, make_user, login):
    db.add_all([
        AlbumConfig(album_id=album.id, key="daily.stock_cap", value=5),
        AlbumConfig(album_id=album.id, key="daily.refill_minutes", value=60),
        AlbumConfig(album_id=album.id, key="daily.welcome_packs", value=5),
    ])
    db.commit()
    body = login(make_user("Cinco")).get("/api/packs").json()
    assert body["daily_cap"] == 5 and body["refill_minutes"] == 60 and body["daily_in_stock"] == 5
    assert db.get(Config, "daily.stock_cap").value == 3  # o global não mudou


def test_tipos_de_pacote_do_album_tem_pesos_explicitos(db, album):
    """Mudar o peso global de uma raridade depois não altera o comportamento de álbum existente."""
    common = pack_of(db, album, "comum")
    assert common.rarity_weights == {"1": 80.0, "2": 17.0, "3": 3.0}
    template = db.scalar(select(PackType).where(PackType.album_id.is_(None), PackType.slug == "comum"))
    assert template is not None and template.id != common.id
    rarity = db.scalar(select(Rarity).where(Rarity.name == "lendária"))
    old = rarity.weight
    try:
        rarity.weight = 50
        db.commit()
        db.expire_all()
        assert pack_of(db, album, "comum").rarity_weights["3"] == 3.0
    finally:
        db.get(Rarity, rarity.id).weight = old
        db.commit()


def test_slug_de_pacote_e_unico_por_album(db, album, make_album):
    other = make_album("Rascunho", 200, 290, state="draft")
    assert pack_of(db, other, "comum").id != pack_of(db, album, "comum").id
    db.add(PackType(album_id=album.id, slug="comum", name="dup", stickers_per_pack=6, rarity_weights={}))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(PackType(album_id=None, slug="comum", name="dup", stickers_per_pack=6, rarity_weights={}))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_pacote_modelo_nao_pode_ser_concedido(db, album, make_user):
    template = db.scalar(select(PackType).where(PackType.album_id.is_(None)))
    with pytest.raises(RuntimeError):
        grant_pack(db, make_user("Mod").id, template, "admin")


# ---- timer da gaveta na participação, com concorrência


def test_timer_da_gaveta_mora_na_participacao(db, album, make_user, login):
    user = make_user("Timer2")
    body = login(user).get("/api/packs").json()
    part = db.get(AlbumParticipation, (user.id, album.id))
    assert part.next_pack_at is not None and part.last_daily_grant_on is not None
    assert body["next_pack_at"].startswith(part.next_pack_at.astimezone(tz=None).isoformat()[:10]) or body["next_pack_at"]
    assert body["album"]["id"] == album.id


def test_boas_vindas_simultaneas_entregam_uma_vez_so(db, album, make_user, login):
    user = make_user("Simult")
    client = login(user)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: client.get("/api/packs").status_code, range(8)))
    assert set(results) == {200}
    assert db.scalar(select(func.count()).select_from(PackGrant).where(PackGrant.user_id == user.id)) == 2
    assert db.scalar(select(func.count()).select_from(AlbumParticipation).where(AlbumParticipation.user_id == user.id)) == 1


# ---- fechamento


@pytest.fixture
def played(db, album, content, make_user, give, login):
    """Ana completou parte do álbum, abriu um pacote e fez uma troca com a Bia; há uma troca aberta."""
    ana, bia, cai = make_user("Ana"), make_user("Bia"), make_user("Cai")
    a, b, c = login(ana), login(bia), login(cai)
    for client in (a, b, c):
        client.get("/api/packs")  # entra no álbum
    opened = a.post(f"/api/packs/{a.get('/api/packs').json()['stock'][0]['id']}/open").json()["items"]
    db.execute(UserSticker.__table__.delete().where(UserSticker.user_id == ana.id))  # inventário controlado
    db.commit()
    give(ana, content[0], 3, pasted=True)
    give(ana, content[1], 1, pasted=True)
    give(ana, content[2], 2)
    give(bia, content[3], 2)
    token = a.post("/api/trades").json()["token"]
    b.post(f"/api/trades/{token}/join")
    a.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[0].id, "quantity": 1}]})
    b.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[3].id, "quantity": 1}]})
    for client in (a, b):
        version = client.get(f"/api/trades/{token}").json()["offer_version"]
        assert client.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 200
    open_token = c.post("/api/trades").json()["token"]
    return ana, bia, cai, a, b, c, len(opened), open_token


def test_fechamento_expira_trocas_e_grava_o_resumo_final(db, album, content, played):
    ana, bia, cai, a, b, c, opened, open_token = played
    assert albums.close_if_due(db, db.get(Album, album.id)) == []  # em andamento: não faz nada
    set_period(db, album, -30, -1)

    mine = a.get("/api/albums/mine").json()  # primeiro acesso depois do fim
    assert mine["active"] is None and [x["id"] for x in mine["ended"]] == [album.id]
    stats = mine["ended"][0]["stats"]
    # Ana: 2 coladas (a 0 e a 1); tinha 3 da 0, deu 1 na troca e recebeu 1 da figurinha 3.
    assert stats == {"pasted": 2, "total_stickers": 17, "received_total": opened, "duplicates": 2, "trades_done": 1}
    db.expire_all()
    assert db.scalar(select(Trade.state).where(Trade.token == open_token)) == "expired"
    parts = {p.user_id: p.final_stats for p in db.scalars(select(AlbumParticipation))}
    assert parts[ana.id] == stats
    # Bia tinha 2 da figurinha 3, deu 1 e recebeu 1 da 0: nenhuma repetida sobrando.
    assert parts[bia.id]["trades_done"] == 1 and parts[bia.id]["pasted"] == 0 and parts[bia.id]["duplicates"] == 0
    assert parts[cai.id] == {"pasted": 0, "total_stickers": 17, "received_total": 0, "duplicates": 0, "trades_done": 0}
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "album.closed")) == 1

    # Idempotente: fechar de novo não muda nada, nem se o inventário mudar por fora depois.
    db.get(UserSticker, (ana.id, content[2].id)).pasted = True
    db.commit()
    assert albums.close_if_due(db, db.get(Album, album.id)) == []
    assert albums.close_due_albums(db) == []
    db.commit()
    db.expire_all()
    assert db.get(AlbumParticipation, (ana.id, album.id)).final_stats == stats
    assert a.get(f"/api/albums/{album.id}/summary").json()["stats"] == stats
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "album.closed")) == 1


def test_varredura_fecha_album_e_avisa_as_trocas(db, album, played):
    from app.main import _sweep

    *_, open_token = played
    trade_id = db.scalar(select(Trade.id).where(Trade.token == open_token))
    set_period(db, album, -30, -1)
    assert trade_id in _sweep()
    assert _sweep() == []
    assert db.scalar(select(func.count()).select_from(AlbumParticipation).where(AlbumParticipation.final_stats.is_(None))) == 0


def test_cli_fecha_albuns_em_lote(db, album, played, capsys):
    from app.cli import close_albums

    set_period(db, album, -30, -1)
    close_albums()
    assert f"[{album.id}]" in capsys.readouterr().out
    assert db.scalar(select(func.count()).select_from(AlbumParticipation).where(AlbumParticipation.final_stats.is_(None))) == 0
    close_albums()
    assert "nenhum" in capsys.readouterr().out


def test_album_completo_registra_quando_foi_completado(db, album, content, make_user, give, login):
    user = make_user("Completa")
    login(user).get("/api/packs")
    for sticker in content:
        give(user, sticker, 1)
    assert len(login(user).post("/api/album/paste-all").json()["pasted"]) == 17
    set_period(db, album, -30, -1)
    albums.close_due_albums(db)
    db.commit()
    stats = db.get(AlbumParticipation, (user.id, album.id)).final_stats
    assert stats["pasted"] == stats["total_stickers"] == 17 and "completed_at" in stats


def test_troca_em_andamento_morre_com_o_album(db, album, content, make_user, give, login):
    ana, bia = make_user("Ana"), make_user("Bia")
    give(ana, content[0], 2)
    give(bia, content[1], 2)
    a, b = login(ana), login(bia)
    token = a.post("/api/trades").json()["token"]
    b.post(f"/api/trades/{token}/join")
    a.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[0].id, "quantity": 1}]})
    set_period(db, album, -30, -1)
    # A troca ainda teria tempo pelo relógio dela, mas o álbum acabou.
    response = b.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[1].id, "quantity": 1}]})
    assert response.status_code == 409 and response.json()["detail"] == "Álbum encerrado."
    assert a.get(f"/api/trades/{token}").json()["state"] == "expired"
    assert quantity_of(db, ana, content[0]) == 2 and quantity_of(db, bia, content[1]) == 2


# ---- "Meus álbuns" e leitura de álbum encerrado


def test_meus_albuns_e_album_encerrado_em_modo_leitura(db, two_albums, make_user, login):
    user, client, old, old_content, new, new_content, *_ = two_albums
    mine = client.get("/api/albums/mine").json()
    assert mine["active"]["id"] == new.id and mine["active"]["status"] == "active"
    assert mine["active"]["stats"]["total_stickers"] == 17
    assert [a["id"] for a in mine["ended"]] == [old.id] and mine["ended"][0]["status"] == "ended"
    assert mine["ended"][0]["stats"] == {"pasted": 1, "total_stickers": 17, "received_total": 0, "duplicates": 2, "trades_done": 0}

    index = client.get(f"/api/albums/{old.id}").json()
    assert index["read_only"] is True and index["tray"] == [] and index["filled"] == 1
    assert index["album"]["status"] == "ended"
    page = client.get(f"/api/albums/{old.id}/orgs/{old_content[0].org_id}/pages/1").json()
    filled = [s for s in page["slots"] if s["filled"]]
    assert [s["sticker_id"] for s in filled] == [old_content[1].id]
    assert client.get(f"/api/stickers/{old_content[1].id}/back").status_code == 200  # o verso continua lá

    # Quem não participou do álbum encerrado não o enxerga (mesma 404 de álbum inexistente).
    stranger = login(make_user("Novato"))
    for path in (f"/api/albums/{old.id}", f"/api/albums/{old.id}/summary", f"/api/albums/{old.id}/orgs/{old_content[0].org_id}/pages/1"):
        assert stranger.get(path).json() == {"detail": "Álbum não encontrado."}
    assert stranger.get("/api/albums/999999").json() == {"detail": "Álbum não encontrado."}
    assert stranger.get("/api/albums/mine").json()["ended"] == []
    # O em andamento qualquer um da universidade vê.
    assert stranger.get(f"/api/albums/{new.id}").json()["read_only"] is False


def test_rascunho_e_agendado_nao_aparecem_para_jogador(db, album, make_album, make_user, login):
    client = login(make_user("Curiosa"))
    draft = make_album("Rascunho", 200, 290, state="draft")
    scheduled = make_album("Agendado", 300, 390)
    for row in (draft, scheduled):
        assert client.get(f"/api/albums/{row.id}").status_code == 404
        assert client.get(f"/api/albums/{row.id}/summary").status_code == 404
    mine = client.get("/api/albums/mine").json()
    assert mine["upcoming"] is None  # o em andamento esconde o próximo
    set_period(db, album, -30, -1)
    mine = client.get("/api/albums/mine").json()
    assert mine["active"] is None and mine["upcoming"]["title"] == "Agendado"


def test_rotas_de_album_exigem_login(album):
    from fastapi.testclient import TestClient

    from app.main import app

    anonymous = TestClient(app)
    for path in ("/api/albums/mine", f"/api/albums/{album.id}", f"/api/albums/{album.id}/summary"):
        assert anonymous.get(path).status_code == 401


# ---- imutabilidade e congelamento no admin


def _sticker_body(sticker, **changes):
    return {
        "org_id": sticker.org_id, "name": sticker.name, "rarity_id": sticker.rarity_id, "page_no": 1, "number": sticker.number,
        "image_url": sticker.image_url, "back_text": sticker.back_text, "slot_layout": {"x": 1, "y": 1, "w": 10, "h": 10},
        **changes,
    }


def test_album_em_andamento_congela_a_estrutura_mas_aceita_o_cosmetico(db, album, content, make_user, login):
    admin = login(make_user("Adm", admin=True))
    sticker = content[0]
    url = f"/api/admin/stickers/{sticker.id}"
    cosmetic = _sticker_body(sticker, name="Novo nome", hint="Nova dica", back_text="Novo verso", tag="gestão",
                             slot_layout={"x": 5, "y": 5, "w": 20, "h": 20}, page_no=2,
                             back_links=[{"label": "Site", "url": "https://example.com"}])
    assert admin.put(url, json=cosmetic).status_code == 200
    for change in ({"number": 900}, {"rarity_id": 2}, {"active": False}):
        response = admin.put(url, json=_sticker_body(sticker, **change))
        assert response.status_code == 409 and "congelada" in response.json()["detail"], change
    assert admin.post("/api/admin/stickers", json=_sticker_body(sticker, number=None, name="Nova")).status_code == 409
    assert admin.delete(url).status_code == 409
    chapter = db.scalar(select(AlbumChapter).where(AlbumChapter.album_id == album.id))
    base = f"/api/admin/albums/{album.id}/chapters"
    assert admin.post(f"{base}/{chapter.id}/mosaic", json={"cols": 2, "rows": 2, "rarity_id": 1}).status_code == 409
    assert admin.post(base, json={"org": {"name": "Nova org"}}).status_code == 409
    assert admin.delete(f"{base}/{chapter.id}").status_code == 409
    assert admin.put(f"{base}/{chapter.id}", json={"sort_order": 3}).status_code == 200  # ordem é layout
    db.expire_all()
    row = db.get(Sticker, sticker.id)
    assert (row.name, row.number, row.rarity_id, row.active, row.page_no) == ("Novo nome", sticker.number, sticker.rarity_id, True, 2)
    assert db.scalar(select(func.count()).select_from(Sticker)) == 17


def test_album_encerrado_e_imutavel_no_admin(db, album, content, make_user, login):
    admin = login(make_user("Adm", admin=True))
    set_period(db, album, -30, -1)
    sticker = content[0]
    chapter = db.scalar(select(AlbumChapter).where(AlbumChapter.album_id == album.id))
    pack = pack_of(db, album, "comum")
    pack_body = {"album_id": album.id, "slug": "comum", "name": "X", "stickers_per_pack": 9}
    album_body = {"title": "Outro nome", "starts_at": _iso(-30), "ends_at": _iso(30), "state": "ready"}
    for response in (
        admin.put(f"/api/admin/stickers/{sticker.id}", json=_sticker_body(sticker, name="Mudou")),
        admin.delete(f"/api/admin/stickers/{sticker.id}"),
        admin.post("/api/admin/stickers", json={**_sticker_body(sticker, number=None), "album_id": album.id}),
        admin.put(f"/api/admin/albums/{album.id}/chapters/{chapter.id}", json={"sort_order": 9}),
        admin.put(f"/api/admin/albums/{album.id}", json=album_body),
        admin.delete(f"/api/admin/albums/{album.id}"),
        admin.put(f"/api/admin/pack-types/{pack.id}", json=pack_body),
        admin.delete(f"/api/admin/pack-types/{pack.id}"),
        admin.post("/api/admin/codes", json={"pack_type_id": pack.id, "count": 1}),
        admin.put(f"/api/admin/albums/{album.id}/config/daily.stock_cap", json={"value": 9}),
    ):
        assert response.status_code == 409, response.text
    db.expire_all()
    assert db.get(Sticker, sticker.id).name == sticker.name
    assert db.get(Album, album.id).title == "Calourada"
    assert pack_of(db, album, "comum").stickers_per_pack == 6


def test_album_em_andamento_so_muda_titulo_capa_e_fim(db, album, make_user, login):
    admin = login(make_user("Adm", admin=True))
    row = db.get(Album, album.id)
    url = f"/api/admin/albums/{album.id}"
    base = {"title": "Calourada 2", "starts_at": row.starts_at.isoformat(), "ends_at": _iso(120), "state": "ready"}
    assert admin.put(url, json=base).json()["title"] == "Calourada 2"
    assert admin.put(url, json={**base, "state": "draft"}).status_code == 409
    assert admin.put(url, json={**base, "starts_at": _iso(-5)}).status_code == 409
    assert admin.put(url, json={**base, "ends_at": _iso(-0.5)}).status_code == 409
    assert admin.delete(url).status_code == 409


def test_admin_de_album_exige_superadmin(db, album, make_user, login):
    player = login(make_user("Comum"))
    for response in (
        player.get("/api/admin/albums"),
        player.post("/api/admin/albums", json={"title": "X", "starts_at": _iso(200), "ends_at": _iso(290)}),
        player.put(f"/api/admin/albums/{album.id}", json={"title": "X", "starts_at": _iso(200), "ends_at": _iso(290)}),
        player.post(f"/api/admin/albums/{album.id}/duplicate", json={"title": "X", "starts_at": _iso(200), "ends_at": _iso(290)}),
        player.get(f"/api/admin/albums/{album.id}/config"),
        player.put(f"/api/admin/albums/{album.id}/config/daily.stock_cap", json={"value": 9}),
        player.post("/api/admin/albums/estimate", json={"album_id": album.id}),
        player.get(f"/api/admin/albums/{album.id}/chapters"),
    ):
        assert response.status_code == 403
    assert db.get(Album, album.id).title == "Calourada"


# ---- criar, duplicar, parâmetros e estimador (painel)


def test_admin_cria_album_com_capitulos_e_figurinhas(db, album, make_user, login):
    admin = login(make_user("Adm", admin=True))
    created = admin.post("/api/admin/albums", json={"title": "Inverno", "starts_at": _iso(100), "ends_at": _iso(190)}).json()
    assert created["status"] == "draft" and created["chapters"] == 0
    aid = created["id"]
    assert {p["slug"] for p in admin.get(f"/api/admin/pack-types?album_id={aid}").json()} == {"comum", "premium"}
    chapter = admin.post(f"/api/admin/albums/{aid}/chapters", json={"org": {"name": "Atlética", "theme_color": "#123456"}, "sort_order": 1})
    assert chapter.status_code == 201
    org_id = chapter.json()["org_id"]
    assert admin.post(f"/api/admin/albums/{aid}/chapters", json={"org_id": org_id}).status_code == 409  # já tem
    assert admin.post(f"/api/admin/albums/{aid}/chapters", json={}).status_code == 422
    body = {"album_id": aid, "org_id": org_id, "name": "Fulana", "rarity_id": 1, "page_no": 1, "slot_layout": {"x": 1, "y": 1, "w": 10, "h": 10}}
    first = admin.post("/api/admin/stickers", json=body).json()
    second = admin.post("/api/admin/stickers", json=body).json()
    assert (first["number"], second["number"], first["album_id"]) == (1, 2, aid)  # numeração própria do álbum
    # Org sem capítulo no álbum não recebe figurinha.
    other_org = admin.post("/api/admin/orgs", json={"name": "Sem capítulo"}).json()["id"]
    assert admin.post("/api/admin/stickers", json={**body, "org_id": other_org}).status_code == 400
    assert [s["id"] for s in admin.get(f"/api/admin/stickers?album_id={aid}").json()] == [first["id"], second["id"]]
    assert admin.get("/api/admin/stickers").json() == []  # sem album_id: o em andamento
    listed = {a["id"]: a for a in admin.get("/api/admin/albums").json()}
    assert listed[aid]["stickers"] == 2 and listed[album.id]["status"] == "active"
    # Rascunho sem jogadores pode ser apagado, com tudo o que é dele.
    assert admin.delete(f"/api/admin/albums/{aid}").status_code == 204
    assert db.get(Album, aid) is None
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.album_id == aid)) == 0
    assert db.scalar(select(func.count()).select_from(PackType).where(PackType.album_id == aid)) == 0


def test_duplicar_album_copia_conteudo_e_nao_copia_jogadores(db, album, content, make_user, give, login):
    admin = login(make_user("Adm", admin=True))
    player = make_user("Joga")
    client = login(player)
    give(player, content[0], 2)
    client.get("/api/packs")
    _code(db, album)
    client.post("/api/trades")
    db.add(AlbumConfig(album_id=album.id, key="daily.stock_cap", value=7))
    pack_of(db, album, "premium").stickers_per_pack = 9
    db.commit()

    copy = admin.post(f"/api/admin/albums/{album.id}/duplicate", json={"title": "Cópia", "starts_at": _iso(100), "ends_at": _iso(190)})
    assert copy.status_code == 201
    cid = copy.json()["id"]
    assert copy.json()["status"] == "draft" and copy.json()["stickers"] == 17 and copy.json()["chapters"] == 1
    assert copy.json()["participants"] == 0
    originals = {s.number: s for s in content}
    copies = db.scalars(select(Sticker).where(Sticker.album_id == cid)).all()
    assert {s.number for s in copies} == set(originals)
    for sticker in copies:
        source = originals[sticker.number]
        assert sticker.id != source.id
        assert (sticker.name, sticker.rarity_id, sticker.image_url, sticker.org_id) == (source.name, source.rarity_id, source.image_url, source.org_id)
    assert cfg(db, "daily.stock_cap", cid) == 7
    assert db.scalar(select(PackType.stickers_per_pack).where(PackType.album_id == cid, PackType.slug == "premium")) == 9
    for model in (PackGrant, RedeemCode, Trade, AlbumParticipation):
        assert db.scalar(select(func.count()).select_from(model).where(model.album_id == cid)) == 0
    assert db.scalar(
        select(func.count()).select_from(UserSticker).join(Sticker, Sticker.id == UserSticker.sticker_id).where(Sticker.album_id == cid)
    ) == 0
    # Apagar a cópia não some com as imagens que o original ainda usa.
    assert admin.delete(f"/api/admin/albums/{cid}").status_code == 204
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.album_id == album.id)) == 17


def test_parametros_do_album_pelo_painel(db, album, make_user, login):
    admin = login(make_user("Adm", admin=True))
    url = f"/api/admin/albums/{album.id}/config"
    rows = {r["key"]: r for r in admin.get(url).json()}
    assert rows["daily.welcome_packs"] == {"key": "daily.welcome_packs", "default": 2, "value": None, "effective": 2}
    assert "code.max_errors_per_hour" not in rows and "rate.login_per_hour" not in rows
    assert admin.put(f"{url}/daily.welcome_packs", json={"value": 4}).json()["effective"] == 4
    assert cfg(db, "daily.welcome_packs", album.id) == 4 and cfg(db, "daily.welcome_packs") == 2
    assert admin.put(f"{url}/daily.welcome_packs", json={"value": "4"}).status_code == 400  # tipo
    assert admin.put(f"{url}/daily.welcome_packs", json={"value": -1}).status_code == 400
    assert admin.put(f"{url}/code.max_errors_per_hour", json={"value": 1}).status_code == 404
    assert admin.put(f"{url}/nao.existe", json={"value": 1}).status_code == 404
    assert admin.put(f"{url}/daily.welcome_packs", json={"value": None}).json()["effective"] == 2
    assert db.get(AlbumConfig, (album.id, "daily.welcome_packs")) is None
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "admin.album_config_update")) == 2


def test_estimador_usa_o_album_e_responde_a_mudanca_de_parametro(db, album, content, make_user, login):
    admin = login(make_user("Adm", admin=True))
    base = admin.post("/api/admin/albums/estimate", json={"album_id": album.id})
    assert base.status_code == 200
    result = base.json()
    assert result["total_stickers"] == 17
    assert result["inputs"]["refill_minutes"] == 720 and result["inputs"]["duration_days"] == 91
    assert result["stickers_by_rarity"] == [
        {"rarity": "comum", "count": 10}, {"rarity": "rara", "count": 5}, {"rarity": "lendária", "count": 2}
    ]
    assert result["packs"]["premium"]["stickers_per_pack"] == 7
    assert [a["code"] for a in result["alerts"]] == ["too_generous"]  # 17 figurinhas em 90 dias é fácil demais
    tight = admin.post("/api/admin/albums/estimate", json={
        "album_id": album.id, "refill_minutes": 43200, "welcome_packs": 0, "premium_every_n_days": 0,
    }).json()
    assert tight["totals"]["percent"] < result["totals"]["percent"]
    assert tight["inputs"]["refill_minutes"] == 43200 and tight["packs"]["premium"] is not None
    # Sobrescrita gravada no álbum vira o padrão do estimador.
    db.add(AlbumConfig(album_id=album.id, key="daily.refill_minutes", value=1440))
    db.commit()
    assert admin.post("/api/admin/albums/estimate", json={"album_id": album.id}).json()["inputs"]["refill_minutes"] == 1440
    assert admin.post("/api/admin/albums/estimate", json={"album_id": 999999}).status_code == 404
    assert admin.post("/api/admin/albums/estimate", json={"album_id": album.id, "open_fraction": 2}).status_code == 422


def test_estimador_avisa_raridade_inalcancavel(db, album, content, make_user, login):
    admin = login(make_user("Adm", admin=True))
    for slug in ("comum", "premium"):
        pack_of(db, album, slug).rarity_weights = {"1": 100, "2": 0, "3": 0}
    db.commit()
    alerts = admin.post("/api/admin/albums/estimate", json={"album_id": album.id}).json()["alerts"]
    message = next(a["message"] for a in alerts if a["code"] == "unreachable")
    assert "lendária" in message and "rara" in message


def test_metricas_sao_do_album(db, two_albums, make_user, login):
    user, client, old, old_content, new, new_content, *_ = two_albums
    admin = login(make_user("Adm", admin=True))
    current = admin.get("/api/admin/metrics").json()
    assert current["album"]["id"] == new.id and current["ranking"] == []
    past = admin.get(f"/api/admin/metrics?album_id={old.id}").json()
    assert past["album"]["id"] == old.id and past["ranking"] == [{"nickname": "Dois", "pasted": 1, "percent": 5.9}]


# ---- CLI


def test_cli_cria_album_e_recusa_sobreposicao(db, album, content, capsys):
    from app.cli import create_album

    create_album("Verão", "2030-01-01", "2030-04-01T00:00+00:00", None, None, True)
    assert "criado (ready)" in capsys.readouterr().out
    created = db.scalar(select(Album).where(Album.title == "Verão"))
    assert created.state == "ready" and (created.ends_at - created.starts_at).days == 90
    assert db.scalar(select(func.count()).select_from(PackType).where(PackType.album_id == created.id)) == 2
    with pytest.raises(SystemExit) as exit_info:
        create_album("Choque", "2030-02-01", "2030-05-01", None, None, True)
    assert "Já existe um álbum pronto" in str(exit_info.value)
    create_album("Cópia", "2031-01-01", "2031-04-01", None, album.id, False)
    copy = db.scalar(select(Album).where(Album.title == "Cópia"))
    assert copy.state == "draft" and db.scalar(select(func.count()).select_from(Sticker).where(Sticker.album_id == copy.id)) == 17
    with pytest.raises(SystemExit):
        create_album("Invertido", "2032-02-01", "2032-01-01", None, None, False)


def test_purge_de_um_album_mantem_contas_conteudo_e_outros_albuns(db, two_albums, give):
    from app.cli import purge_campaign

    user, client, old, old_content, new, new_content, old_grant, old_code = two_albums
    give(user, new_content[0], 1)
    client.get("/api/packs")
    purge_campaign(old.id)
    db.expire_all()
    assert db.get(User, user.id) is not None
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.album_id == old.id)) == 17
    assert db.get(PackGrant, old_grant) is None
    assert db.get(UserSticker, (user.id, old_content[0].id)) is None
    assert db.get(AlbumParticipation, (user.id, old.id)) is None
    # O álbum em andamento ficou intacto.
    assert db.get(UserSticker, (user.id, new_content[0].id)).quantity == 1
    assert db.get(AlbumParticipation, (user.id, new.id)) is not None
    assert db.scalar(select(func.count()).select_from(PackGrant).where(PackGrant.album_id == new.id)) == 2
    with pytest.raises(SystemExit):
        purge_campaign(999999)
