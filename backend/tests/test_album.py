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
