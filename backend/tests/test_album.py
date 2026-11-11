from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.models import AuditLog, UserSticker
from tests.conftest import quantity_of


def test_colar_verso_e_progresso(db, content, make_user, give, login):
    user = make_user("Cola")
    client = login(user)
    sticker = content[0]
    give(user, sticker, 2)

    # Antes de colar, nem verso nem imagem/nome no espaço.
    assert client.get(f"/api/stickers/{sticker.id}/back").status_code == 403
    page = client.get(f"/api/album/orgs/{sticker.org_id}/pages/1").json()
    slot = next(s for s in page["slots"] if s["sticker_id"] == sticker.id)
    assert slot["filled"] is False and "card" not in slot
    assert "name" not in slot and "image_url" not in slot

    index = client.get("/api/album").json()
    assert [t["id"] for t in index["tray"]] == [sticker.id] and index["filled"] == 0

    assert client.post(f"/api/album/paste/{sticker.id}").status_code == 200
    assert client.post(f"/api/album/paste/{sticker.id}").status_code == 409

    index = client.get("/api/album").json()
    assert index["tray"] == [] and index["filled"] == 1 and index["orgs"][0]["filled"] == 1
    assert client.get(f"/api/stickers/{sticker.id}/back").json()["back_text"] == "Verso 1"
    assert quantity_of(db, user, sticker) == 2


def test_colar_sem_ter_falha(content, make_user, login):
    assert login(make_user("Sem")).post(f"/api/album/paste/{content[0].id}").status_code == 404


def test_repetidas_e_o_que_passa_de_um(content, make_user, give, login):
    user = make_user("Rep")
    give(user, content[0], 3, pasted=True)
    give(user, content[1], 1)
    give(user, content[2], 2)
    dup = login(user).get("/api/inventory/duplicates").json()
    assert {d["id"]: d["available"] for d in dup} == {content[0].id: 2, content[2].id: 1}
    assert {d["id"]: d["pasted"] for d in dup} == {content[0].id: True, content[2].id: False}


def test_colar_tudo_cola_a_bandeja_e_e_idempotente(db, content, make_user, give, login):
    user = make_user("Tudo")
    client = login(user)
    give(user, content[0], 1)
    give(user, content[1], 3)
    give(user, content[2], 2, pasted=True)
    give(user, content[3], 0)  # já trocada: não está na bandeja

    first = client.post("/api/album/paste-all")
    assert first.status_code == 200
    assert sorted(first.json()["pasted"]) == sorted([content[0].id, content[1].id])

    index = client.get("/api/album").json()
    assert index["tray"] == [] and index["filled"] == 3
    assert quantity_of(db, user, content[1]) == 3  # colar não consome exemplar

    again = client.post("/api/album/paste-all")
    assert again.status_code == 200 and again.json() == {"pasted": []}
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "sticker.paste_all")) == 1


def test_colar_tudo_sem_figurinhas_responde_vazio(make_user, login):
    response = login(make_user("Vazio")).post("/api/album/paste-all")
    assert response.status_code == 200 and response.json() == {"pasted": []}


def test_colar_tudo_ignora_figurinha_inativa(db, content, make_user, give, login):
    user = make_user("Inativa")
    give(user, content[0], 1)
    give(user, content[1], 1)
    content[1].active = False
    db.commit()
    assert login(user).post("/api/album/paste-all").json() == {"pasted": [content[0].id]}


def test_colar_tudo_exige_conta_confirmada(make_user, login):
    assert login(make_user("Pend", verified=False)).post("/api/album/paste-all").status_code == 403


def test_colar_tudo_so_mexe_no_proprio_inventario(db, content, make_user, give, login):
    mine, other = make_user("Meu"), make_user("Outro")
    give(mine, content[0], 1)
    give(other, content[0], 1)
    login(mine).post("/api/album/paste-all")
    db.expire_all()
    assert db.get(UserSticker, (other.id, content[0].id)).pasted is False


def test_colar_tudo_concorrente_com_colar_individual(db, content, make_user, give, login):
    user = make_user("Corre")
    client = login(user)
    for sticker in content[:8]:
        give(user, sticker, 1)

    def call(i):
        if i % 2:
            return ("um", client.post(f"/api/album/paste/{content[i // 2].id}"))
        return ("tudo", client.post("/api/album/paste-all"))

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(call, range(8)))

    pasted_once = []
    for kind, response in results:
        if kind == "tudo":
            assert response.status_code == 200
            pasted_once += response.json()["pasted"]
        else:
            assert response.status_code in (200, 409)
            if response.status_code == 200:
                pasted_once.append(response.json()["id"])
    # Cada figurinha foi colada por exatamente uma das chamadas.
    assert sorted(pasted_once) == sorted(s.id for s in content[:8])
    assert db.scalar(
        select(func.count()).select_from(UserSticker).where(UserSticker.user_id == user.id, UserSticker.pasted.is_(True))
    ) == 8
