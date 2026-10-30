from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import update
from starlette.websockets import WebSocketDisconnect
import pytest

from app.models import AuditLog, Trade
from app.services.core import now
from app.services.trades import expire_due_trades
from tests.conftest import quantity_of


@pytest.fixture
def session(content, make_user, give, login):
    """Ana e Bia em uma troca ativa, cada uma com repetidas diferentes."""
    ana, bia = make_user("Ana"), make_user("Bia")
    give(ana, content[0], 3)
    give(bia, content[1], 2)
    a, b = login(ana), login(bia)
    token = a.post("/api/trades").json()["token"]
    assert b.post(f"/api/trades/{token}/join").status_code == 200
    return ana, bia, a, b, token


def offer(client, token, sticker, quantity=1):
    return client.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": sticker.id, "quantity": quantity}]})


def accept(client, token):
    version = client.get(f"/api/trades/{token}").json()["offer_version"]
    return client.post(f"/api/trades/{token}/accept", json={"version": version})


def expire_now(db, token):
    db.execute(update(Trade).where(Trade.token == token).values(expires_at=now() - timedelta(seconds=1)))
    db.commit()


def test_troca_completa(db, content, session):
    ana, bia, a, b, token = session
    offer(a, token, content[0], 2)
    offer(b, token, content[1])
    view = b.get(f"/api/trades/{token}").json()
    assert [o["id"] for o in view["other_offer"]] == [content[0].id]

    assert accept(a, token).json()["state"] == "active"
    assert accept(b, token).json()["state"] == "done"

    assert quantity_of(db, ana, content[0]) == 1
    assert quantity_of(db, bia, content[0]) == 2
    assert quantity_of(db, ana, content[1]) == 1
    assert quantity_of(db, bia, content[1]) == 1
    assert db.query(AuditLog).filter_by(action="trade.done").count() == 1


def test_mudar_oferta_invalida_aceite(db, content, session):
    ana, bia, a, b, token = session
    offer(a, token, content[0])
    offer(b, token, content[1])
    accept(a, token)
    offer(a, token, content[0], 2)
    view = b.get(f"/api/trades/{token}").json()
    assert view["other_accepted"] is False
    # Aceitar uma versão antiga não conclui.
    assert b.post(f"/api/trades/{token}/accept", json={"version": view["offer_version"] - 1}).status_code == 409
    assert accept(b, token).json()["state"] == "active"
    assert quantity_of(db, ana, content[0]) == 3


def test_so_oferece_repetida(content, session, give):
    ana, _, a, _, token = session
    give(ana, content[5], 1)
    assert offer(a, token, content[5]).status_code == 409
    assert offer(a, token, content[0], 3).status_code == 409  # 3 no inventário, só 2 repetidas
    assert offer(a, token, content[9]).status_code == 409  # não possui


def test_nao_aceita_oferta_vazia(content, session):
    _, _, a, _, token = session
    offer(a, token, content[0])
    assert accept(a, token).status_code == 409


def test_cancelar_nao_muda_inventario_e_invalida_link(db, content, session, make_user, login):
    ana, bia, a, b, token = session
    offer(a, token, content[0])
    assert b.post(f"/api/trades/{token}/cancel").json()["state"] == "cancelled"
    assert quantity_of(db, ana, content[0]) == 3
    assert accept(a, token).status_code == 409
    assert login(make_user("Outra")).post(f"/api/trades/{token}/join").status_code == 410


def test_expiracao_decidida_no_servidor(db, content, session):
    ana, bia, a, b, token = session
    offer(a, token, content[0])
    offer(b, token, content[1])
    accept(a, token)
    version = b.get(f"/api/trades/{token}").json()["offer_version"]
    expire_now(db, token)
    assert b.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 410
    assert a.get(f"/api/trades/{token}").json()["state"] == "expired"
    assert quantity_of(db, ana, content[0]) == 3
    assert quantity_of(db, bia, content[1]) == 2


def test_tempos_do_link_e_da_sessao(make_user, login):
    a, b = login(make_user("Tempo")), login(make_user("Tempo2"))
    created = a.post("/api/trades").json()
    assert 590 < created["expires_in"] <= 600
    joined = b.post(f"/api/trades/{created['token']}/join").json()
    assert 290 < joined["expires_in"] <= 300


def test_link_vencido_e_varredura(db, make_user, login):
    a, b = login(make_user("Venc")), login(make_user("Venc2"))
    token = a.post("/api/trades").json()["token"]
    expire_now(db, token)
    assert len(expire_due_trades(db)) == 1
    assert b.post(f"/api/trades/{token}/join").status_code == 410
    # Depois de expirar, o criador pode abrir outra troca.
    assert a.post("/api/trades").status_code == 201


def test_uma_troca_ativa_por_vez(make_user, login):
    a, b, c = login(make_user("U1")), login(make_user("U2")), login(make_user("U3"))
    token = a.post("/api/trades").json()["token"]
    assert a.post("/api/trades").status_code == 409
    other = c.post("/api/trades").json()["token"]
    assert b.post(f"/api/trades/{token}/join").status_code == 200
    assert b.post(f"/api/trades/{other}/join").status_code == 409
    assert a.post(f"/api/trades/{token}/join").status_code == 410


def test_join_simultaneo_tem_um_vencedor(make_user, login):
    creator = login(make_user("Criador"))
    others = [login(make_user(f"Jog{i}")) for i in range(6)]
    token = creator.post("/api/trades").json()["token"]
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda c: c.post(f"/api/trades/{token}/join").status_code, others))
    assert sorted(codes) == [200] + [410] * 5


def test_terceiro_nao_ve_troca_ativa(session, make_user, login):
    _, _, _, _, token = session
    assert login(make_user("Xereta")).get(f"/api/trades/{token}").status_code == 410


def test_falha_na_conclusao_faz_rollback(db, content, session, give):
    """A repetida some entre a oferta e o aceite: nenhum inventário muda."""
    ana, bia, a, b, token = session
    offer(a, token, content[0], 2)
    offer(b, token, content[1])
    accept(a, token)
    from app.models import UserSticker

    db.get(UserSticker, (ana.id, content[0].id)).quantity = 1
    db.commit()
    assert accept(b, token).status_code == 409
    assert quantity_of(db, ana, content[0]) == 1
    assert quantity_of(db, bia, content[1]) == 2
    assert quantity_of(db, bia, content[0]) == 0
    assert b.get(f"/api/trades/{token}").json()["state"] == "active"


def test_aceite_simultaneo_nao_duplica(db, content, session):
    ana, bia, a, b, token = session
    offer(a, token, content[0])
    offer(b, token, content[1])
    version = a.get(f"/api/trades/{token}").json()["offer_version"]
    with ThreadPoolExecutor(8) as pool:
        codes = list(
            pool.map(
                lambda c: c.post(f"/api/trades/{token}/accept", json={"version": version}).status_code,
                [a, b] * 4,
            )
        )
    assert 200 in codes
    assert quantity_of(db, ana, content[0]) + quantity_of(db, bia, content[0]) == 3
    assert quantity_of(db, ana, content[1]) + quantity_of(db, bia, content[1]) == 2
    assert quantity_of(db, ana, content[1]) == 1


def test_websocket_avisa_e_recusa_estranhos(content, session, make_user, login):
    _, _, a, b, token = session
    with a.websocket_connect(f"/api/trades/{token}/ws") as socket:
        offer(b, token, content[1])
        assert socket.receive_text() == "changed"
    with pytest.raises(WebSocketDisconnect):
        with login(make_user("Fora")).websocket_connect(f"/api/trades/{token}/ws"):
            pass
