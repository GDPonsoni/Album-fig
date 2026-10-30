import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import User
from app.services.mail import dev_outbox
from tests.conftest import PASSWORD


def anon() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "album"})


def token_from_mail() -> str:
    return re.search(r"token=([\w-]+)", dev_outbox[-1]["body"]).group(1)


SIGNUP = {"email": "Nova@Example.com", "password": "uma-senha-boa", "nickname": "Nova"}


def test_cadastro_confirmacao_e_login(db):
    client = anon()
    assert client.post("/api/auth/register", json=SIGNUP).status_code == 201
    login = client.post("/api/auth/login", json={"email": "nova@example.com", "password": SIGNUP["password"]})
    assert login.status_code == 200 and login.json()["verified"] is False

    # RF-02: sem confirmar não recebe nem abre pacotes.
    assert client.get("/api/packs").json()["stock"] == []
    assert client.post("/api/trades").status_code == 403

    assert client.post("/api/auth/confirm", json={"token": token_from_mail()}).status_code == 200
    assert client.get("/api/me").json()["verified"] is True
    assert len(client.get("/api/packs").json()["stock"]) == 2


def test_token_de_confirmacao_e_de_uso_unico(db):
    client = anon()
    client.post("/api/auth/register", json=SIGNUP)
    token = token_from_mail()
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 200
    assert client.post("/api/auth/confirm", json={"token": token}).status_code == 400


def test_email_repetido_nao_revela_conta_nem_duplica(db):
    client = anon()
    first = client.post("/api/auth/register", json=SIGNUP)
    second = client.post("/api/auth/register", json={**SIGNUP, "nickname": "Outra"})
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(db.scalars(select(User)).all()) == 1


def test_apelido_unico_ignorando_caixa(db):
    client = anon()
    client.post("/api/auth/register", json=SIGNUP)
    other = {**SIGNUP, "email": "b@example.com", "nickname": "NOVA"}
    assert client.post("/api/auth/register", json=other).status_code == 409


def test_recuperacao_de_senha_derruba_sessoes(make_user, login):
    user = make_user("Rita")
    session = login(user)
    anon().post("/api/auth/forgot", json={"email": user.email})
    assert anon().post("/api/auth/reset", json={"token": token_from_mail(), "password": "senha-nova-123"}).status_code == 200
    assert session.get("/api/me").status_code == 401
    assert anon().post("/api/auth/login", json={"email": user.email, "password": PASSWORD}).status_code == 401
    assert anon().post("/api/auth/login", json={"email": user.email, "password": "senha-nova-123"}).status_code == 200


def test_csrf_exige_header(make_user):
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 403


def test_rate_limit_de_login(make_user):
    user = make_user("Rate")
    client = anon()
    codes = [
        client.post("/api/auth/login", json={"email": user.email, "password": "errada"}).status_code for _ in range(21)
    ]
    assert codes[:20] == [401] * 20 and codes[20] == 429


def test_admin_restrito(make_user, login):
    assert login(make_user("Comum")).get("/api/admin/metrics").status_code == 403
    assert login(make_user("Chefe", admin=True)).get("/api/admin/metrics").status_code == 200
