import io
import re
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import images, mail
from app.settings import Settings, settings


class FakeR2:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    monkeypatch.setattr(settings, "r2_bucket", "album")
    monkeypatch.setattr(images, "_r2_client", lambda: fake)
    return fake


def test_imagem_vai_para_o_r2_e_volta(r2):
    url = images.save_webp(Image.new("RGB", (40, 40)), "stickers/teste")
    assert url.startswith("/media/stickers/")
    assert images.read_media(url) == r2.objects[url.removeprefix("/media/")]
    images.remove_media(url)
    assert r2.objects == {}
    assert images.read_media(url) is None


def test_imagem_do_r2_so_sai_para_quem_esta_logado(r2, make_user, login):
    url = images.save_webp(Image.new("RGB", (40, 40)), "stickers/teste")
    assert TestClient(app).get(url).status_code == 401
    response = login(make_user("Ana")).get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"].startswith("private")
    assert response.content == r2.objects[url.removeprefix("/media/")]


def test_nome_legivel_com_sufixo_imprevisivel(r2):
    url = images.save_webp(Image.new("RGB", (40, 40)), "orgs/Atlética XV!/figurinhas/007 João Pé")
    assert re.fullmatch(r"/media/orgs/atletica-xv/figurinhas/007-joao-pe-[0-9a-f]{10}\.webp", url)
    assert images.media_key("orgs/../  /").startswith("orgs/imagem-")


def test_admin_escolhe_o_nome_no_upload(r2, make_user, login):
    admin = login(make_user("Ana", admin=True))
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40)).save(buffer, "PNG")
    response = admin.post("/api/admin/upload", files={"file": ("x.png", buffer.getvalue())}, data={"name": "orgs/Atlética/logo"})
    assert response.json()["url"].startswith("/media/orgs/atletica/logo-")


def test_imagem_inexistente_da_404(r2, make_user, login):
    assert login(make_user("Ana")).get("/media/stickers/nada.webp").status_code == 404


def test_url_externa_nunca_e_tocada(r2):
    r2.objects["x.webp"] = b"nosso"
    images.remove_media("https://outro-site.example.com/x.webp")
    assert images.read_media("https://outro-site.example.com/x.webp") is None
    assert r2.objects == {"x.webp": b"nosso"}


def test_sem_r2_continua_no_disco(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    url = images.save_webp(Image.new("RGB", (40, 40)), "stickers/teste")
    assert url.startswith("/media/stickers/")
    assert images.read_media(url)
    images.remove_media(url)
    assert images.read_media(url) is None


def test_caminho_fora_da_pasta_de_midia_e_recusado(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    (tmp_path / "segredo").write_text("x")
    assert images.read_media("/media/../segredo") is None


@pytest.mark.parametrize("url", ["postgresql://u:p@h/d?sslmode=require", "postgres://u:p@h/d?sslmode=require"])
def test_url_do_banco_usa_psycopg(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@h/d?sslmode=require"


def test_email_sai_pela_api_com_a_chave(monkeypatch):
    monkeypatch.setattr(settings, "brevo_api_key", "chave")
    with patch("urllib.request.urlopen") as urlopen:
        mail.send_mail("a@example.com", "Oi", "Corpo")
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://api.brevo.com/v3/smtp/email"
    assert request.get_header("Api-key") == "chave"
    assert b"a@example.com" in request.data
