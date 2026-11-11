from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select

from app.models import Album, PackGrant, RedeemCode, Sticker, UserSticker
from app.services.codes import generate_code, normalize_code
from app.services.core import now
from tests.conftest import pack_of, set_period


def _album(db) -> Album:
    return db.scalar(select(Album).where(Album.title == "Calourada"))


def _code(db, album=None, **kwargs) -> str:
    album = album or _album(db)
    pack = pack_of(db, album, "premium")
    value = generate_code()
    db.add(RedeemCode(code=value, album_id=album.id, pack_type_id=pack.id, label="teste", **kwargs))
    db.commit()
    return value


def redeem(client, code):
    return client.post("/api/codes/redeem", json={"code": code})


def test_resgate_concede_pacote_e_e_de_uso_unico(db, make_user, login):
    user = make_user("Res")
    client = login(user)
    code = _code(db)
    assert redeem(client, code.lower().replace("-", " ")).status_code == 200
    grant = db.scalar(select(PackGrant).where(PackGrant.user_id == user.id, PackGrant.source == "code"))
    assert grant is not None
    assert redeem(client, code).status_code == 400
    row = db.scalar(select(RedeemCode))
    assert row.redeemed_by == user.id and row.redeemed_at is not None


def test_erros_sao_genericos(db, make_user, login):
    client = login(make_user("Gen"))
    used = _code(db)
    redeem(client, used)
    expired = _code(db, expires_at=now() - timedelta(hours=1))
    answers = {redeem(client, value).text for value in (used, expired, "NAO-EXISTE")}
    assert len(answers) == 1


def test_limite_de_tentativas_nao_conta_acertos(db, make_user, login):
    client = login(make_user("Bruta"))
    for _ in range(10):
        assert redeem(client, "ERRADO").status_code == 400
    valid = _code(db)
    assert redeem(client, "ERRADO").status_code == 429
    assert redeem(client, valid).status_code == 429  # bloqueado inclusive para códigos válidos


def test_resgate_simultaneo_tem_um_sucesso(db, make_user, login):
    code = _code(db)
    clients = [login(make_user(f"Cor{i}")) for i in range(8)]
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda c: redeem(c, code).status_code, clients))
    assert sorted(codes) == [200] + [400] * 7
    assert len(db.scalars(select(PackGrant).where(PackGrant.source == "code")).all()) == 1


def test_normalizacao():
    assert normalize_code("abcd efgh-2345 6789") == "ABCD-EFGH-2345-6789"


def test_admin_gera_lote_e_ve_resgates(db, make_user, login):
    admin = login(make_user("Adm", admin=True))
    pack = pack_of(db, _album(db), "comum")
    created = admin.post("/api/admin/codes", json={"pack_type_id": pack.id, "label": "biblioteca", "count": 5}).json()
    assert len(set(created["codes"])) == 5
    player = make_user("Joga")
    assert redeem(login(player), created["codes"][0]).status_code == 200
    listed = admin.get("/api/admin/codes").json()
    assert [c["redeemed_by"] for c in listed if c["code"] == created["codes"][0]] == ["Joga"]


def test_admin_nao_apaga_figurinha_que_alguem_tem(db, content, album, make_user, give, login):
    """Antes a remoção levava o inventário de todo mundo junto. Com álbuns imutáveis, figurinha
    com inventário não sai: a API recusa e o banco também (ON DELETE RESTRICT)."""
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import set_period

    admin = login(make_user("Adm2", admin=True))
    player = make_user("Tem")
    sticker_id, free_id, player_id = content[0].id, content[1].id, player.id
    give(player, content[0], 2)
    # Álbum em andamento: estrutura congelada, nem a figurinha livre sai.
    assert admin.delete(f"/api/admin/stickers/{free_id}").status_code == 409
    set_period(db, album, 5, 60)  # agendado: ainda dá para mexer na estrutura
    assert admin.delete(f"/api/admin/stickers/{sticker_id}").status_code == 409
    assert admin.delete(f"/api/admin/stickers/{free_id}").status_code == 204
    db.expire_all()
    assert db.get(Sticker, sticker_id) is not None and db.get(Sticker, free_id) is None
    assert db.get(UserSticker, (player_id, sticker_id)).quantity == 2
    try:
        db.execute(Sticker.__table__.delete().where(Sticker.id == sticker_id))
        raise AssertionError("o banco deveria recusar")
    except IntegrityError:
        db.rollback()


def test_fim_da_campanha_apaga_jogadores_e_nao_libera_codigos(db, content, make_user, give, login):
    from app.cli import purge_campaign
    from app.models import AuditLog, User

    keeper = make_user("Fica", admin=True)
    player = make_user("Sai")
    give(player, content[0], 1)
    code = _code(db)
    assert redeem(login(player), code).status_code == 200
    keeper_id = keeper.id
    db.commit()

    purge_campaign()  # sem álbum: apaga todas as contas de jogadores

    db.expire_all()
    assert [u.id for u in db.scalars(select(User))] == [keeper_id]
    assert db.scalars(select(UserSticker)).all() == []
    assert db.scalars(select(AuditLog)).all() == []
    other = make_user("Nova")
    assert redeem(login(other), code).status_code == 400


def test_admin_edita_peca_de_mosaico(db, content, make_user, login):
    admin = login(make_user("Adm3", admin=True))
    piece = content[0]
    piece.is_mosaic_piece, piece.mosaic_index, piece.page_no, piece.slot_layout = True, 0, 0, {}
    db.commit()
    body = {"org_id": piece.org_id, "name": "Peça nova", "rarity_id": piece.rarity_id, "page_no": 0, "number": piece.number}
    assert admin.put(f"/api/admin/stickers/{piece.id}", json=body).status_code == 200
    # Com o álbum em andamento não se cria figurinha; agendado, a validação de espaço volta a valer.
    assert admin.post("/api/admin/stickers", json={**body, "number": 500}).status_code == 409
    set_period(db, db.get(Album, piece.album_id), 5, 60)
    assert admin.post("/api/admin/stickers", json={**body, "number": 500}).status_code == 400


def preview(client, code):
    return client.get("/api/codes/preview", params={"code": code})


def test_previa_nao_consome_e_post_consome_uma_vez(db, make_user, login):
    user = make_user("Qr")
    client = login(user)
    code = _code(db)
    for _ in range(3):
        body = preview(client, code.lower())
        assert body.status_code == 200
        assert body.json()["code"] == code and body.json()["pack"]["slug"] == "premium"
    db.expire_all()
    row = db.scalar(select(RedeemCode).where(RedeemCode.code == code))
    assert row.redeemed_at is None and row.redeemed_by is None
    assert db.scalars(select(PackGrant).where(PackGrant.source == "code")).all() == []

    assert redeem(client, code).status_code == 200
    assert redeem(client, code).status_code == 400
    assert preview(client, code).status_code == 400
    assert len(db.scalars(select(PackGrant).where(PackGrant.source == "code")).all()) == 1


def test_resgate_nao_existe_por_get(db, make_user, login):
    client = login(make_user("Get"))
    code = _code(db)
    assert client.get("/api/codes/redeem", params={"code": code}).status_code in (404, 405)
    db.expire_all()
    assert db.scalar(select(RedeemCode).where(RedeemCode.code == code)).redeemed_at is None


def test_previa_invalida_e_generica_e_divide_o_limite_com_o_resgate(db, make_user, login):
    client = login(make_user("Orac"))
    used = _code(db)
    redeem(client, used)
    expired = _code(db, expires_at=now() - timedelta(hours=1))
    answers = {(r.status_code, r.text) for r in (preview(client, v) for v in (used, expired, "NAO-EXISTE"))}
    assert answers == {(400, '{"detail":"Código inválido."}')}
    for _ in range(7):
        assert preview(client, "ERRADO").status_code == 400
    valid = _code(db)
    # 10 erros na hora: prévia e resgate bloqueiam, mesmo para código válido.
    assert preview(client, valid).status_code == 429
    assert redeem(client, valid).status_code == 429


def test_previa_exige_login(db):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/codes/preview", params={"code": _code(db)}).status_code == 401
