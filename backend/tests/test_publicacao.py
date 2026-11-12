"""Fase 2: papéis de editor, rascunho, revisão, publicação e 2FA.

A propriedade central: a API do editor só escreve em `chapter_revision` e `asset`.
Só a publicação (superadmin + segundo fator) leva conteúdo para as tabelas de produção.
"""
import copy
import io
import time
import uuid
from datetime import timedelta

import pyotp
import pytest
from PIL import Image
from sqlalchemy import func, select, text

from app.models import (
    Album,
    AlbumChapter,
    Asset,
    AuditLog,
    ChapterRevision,
    RecoveryCode,
    RoleGrant,
    Sticker,
    User,
    UserSession,
)
from app.services import mfa as mfa_service
from app.services import revisions
from app.services.core import now
from app.settings import settings
from tests.conftest import add_content, set_period

# Tudo o que é "produção": nada disto pode mudar por uma rota de editor.
PRODUCTION = (
    "sticker", "album_chapter", "album", "org", "pack_type", "rarity", "config", "album_config", "redeem_code",
    "pack_grant", "user_sticker", "trade", "role_grant", "university", "album_participation",
)


def snapshot(db) -> dict[str, list]:
    db.rollback()
    return {
        table: [tuple(row) for row in db.execute(text(f"SELECT md5(t::text) FROM {table} t ORDER BY 1"))] for table in PRODUCTION
    }


def png(color=(200, 30, 30), size=(60, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def media_dir(tmp_path, monkeypatch):
    """Os envios destes testes vão para uma pasta temporária, não para backend/media."""
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    return tmp_path


@pytest.fixture
def chapter(db, album, content) -> AlbumChapter:
    return db.scalar(select(AlbumChapter).where(AlbumChapter.album_id == album.id))


@pytest.fixture
def grant(db):
    def factory(user: User, chapter: AlbumChapter, **kwargs) -> RoleGrant:
        row = RoleGrant(user_id=user.id, chapter_id=chapter.id, role="editor", **kwargs)
        db.add(row)
        db.commit()
        return row

    return factory


@pytest.fixture
def editor(make_user, login, grant, chapter):
    user = make_user("Edita")
    grant(user, chapter)
    return user, login(user)


@pytest.fixture
def admin(make_user, mfa_login):
    user = make_user("Super", admin=True)
    return user, mfa_login(user)


def draft_of(client, chapter) -> dict:
    response = client.get(f"/api/editor/chapters/{chapter.id}/draft")
    assert response.status_code == 200, response.text
    return response.json()


def save(client, chapter, content):
    return client.put(f"/api/editor/chapters/{chapter.id}/draft", json={"content": content})


def upload(client, chapter, kind="sticker", data=None, name="foto.png", mime="image/png"):
    return client.post(
        f"/api/editor/chapters/{chapter.id}/assets", files={"file": (name, data or png(), mime)}, data={"kind": kind}
    )


def new_sticker(**changes) -> dict:
    return {
        "key": str(uuid.uuid4()), "name": "Nova figurinha", "asset_id": None, "size": "P", "tag": "membro", "hint": "",
        "back_text": "", "back_links": [], "page_no": 1, "slot": {"x": 40, "y": 40, "w": 20, "h": 20},
        "suggested_rarity_id": None, "active": True, **changes,
    }


def submit_and_approve(editor_client, admin_client, chapter, content) -> int:
    assert save(editor_client, chapter, content).status_code == 200
    assert editor_client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 200
    revision_id = admin_client.get("/api/admin/revisions").json()[0]["id"]
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/approve", json={"note": "ok"}).status_code == 200
    return revision_id


# ====================================================================== papéis


def test_superadmin_concede_lista_e_revoga_com_efeito_imediato(db, chapter, make_user, login, admin):
    _, admin_client = admin
    user = make_user("Futura")
    client = login(user)
    assert client.get(f"/api/editor/chapters/{chapter.id}/draft").status_code == 404
    assert client.get("/api/editor/chapters").json() == []

    granted = admin_client.post(f"/api/admin/chapters/{chapter.id}/editors", json={"email": "FUTURA@example.com"})
    assert granted.status_code == 201 and granted.json()["state"] == "active"
    assert admin_client.post(f"/api/admin/chapters/{chapter.id}/editors", json={"email": "futura@example.com"}).status_code == 409
    # Mesma sessão do editor, sem novo login: o papel é lido do banco a cada requisição.
    assert client.get(f"/api/editor/chapters/{chapter.id}/draft").status_code == 200
    assert [c["id"] for c in client.get("/api/editor/chapters").json()] == [chapter.id]
    assert client.get("/api/me").json()["editor_chapters"] == 1
    assert [g["nickname"] for g in admin_client.get(f"/api/admin/chapters/{chapter.id}/editors").json()] == ["Futura"]

    assert admin_client.delete(f"/api/admin/roles/{granted.json()['id']}").status_code == 204
    # Revogado: a requisição seguinte já é recusada, em todas as rotas do editor.
    content = draft_of(admin_client, chapter)["content"] if False else None
    for response in (
        client.get(f"/api/editor/chapters/{chapter.id}/draft"),
        client.put(f"/api/editor/chapters/{chapter.id}/draft", json={"content": {}}),
        client.post(f"/api/editor/chapters/{chapter.id}/submit"),
        client.get(f"/api/editor/chapters/{chapter.id}/assets"),
        upload(client, chapter),
    ):
        assert response.status_code == 404, response.text
    assert content is None and client.get("/api/editor/chapters").json() == []
    assert admin_client.get(f"/api/admin/chapters/{chapter.id}/editors").json()[0]["state"] == "revoked"
    actions = [a for a in db.scalars(select(AuditLog.action).where(AuditLog.action.like("role.%")).order_by(AuditLog.id))]
    assert actions == ["role.grant", "role.revoke"]


def test_papel_expirado_ou_de_conta_nao_confirmada_nao_vale(db, chapter, make_user, login, grant):
    expired = make_user("Venceu")
    grant(expired, chapter, expires_at=now() - timedelta(seconds=1))
    assert login(expired).get(f"/api/editor/chapters/{chapter.id}/draft").status_code == 404
    valid = make_user("Vale")
    grant(valid, chapter, expires_at=now() + timedelta(hours=1))
    assert login(valid).get(f"/api/editor/chapters/{chapter.id}/draft").status_code == 200
    pending = make_user("Pendente", verified=False)
    grant(pending, chapter)
    assert login(pending).get(f"/api/editor/chapters/{chapter.id}/draft").status_code == 403


def test_conceder_exige_superadmin_segundo_fator_e_conta_valida(db, chapter, make_user, login, mfa_login, editor):
    _, editor_client = editor
    body = {"email": "alvo@example.com"}
    make_user("Alvo")
    url = f"/api/admin/chapters/{chapter.id}/editors"
    assert editor_client.post(url, json=body).status_code == 403  # editor não é superadmin
    assert editor_client.get(url).status_code == 403
    plain = login(make_user("SemFator", admin=True))
    response = plain.post(url, json=body)
    assert response.status_code == 403 and response.headers["x-mfa"] == "setup"
    stale = mfa_login(make_user("Vencido", admin=True), fresh=False)
    response = stale.post(url, json=body)
    assert response.status_code == 403 and response.headers["x-mfa"] == "verify"
    assert db.scalar(select(func.count()).select_from(RoleGrant)) == 1  # só o do fixture

    ok = mfa_login(make_user("Certo", admin=True))
    assert ok.post(url, json={"email": "ninguem@example.com"}).status_code == 404
    make_user("NaoConf", verified=False)
    assert ok.post(url, json={"email": "naoconf@example.com"}).status_code == 409
    assert ok.post(url, json={"email": "alvo@example.com", "expires_at": (now() - timedelta(days=1)).isoformat()}).status_code == 400
    assert ok.post(url, json=body).status_code == 201


# ============================================= o editor não escreve em produção


def test_editor_so_escreve_em_revisao_e_ativo(db, album, chapter, editor):
    """Passa por TODAS as rotas do editor que escrevem e confere que nenhuma tabela de produção mudou."""
    user, client = editor
    set_period(db, album, 10, 100)  # álbum ainda não começou: o editor pode propor até figurinha nova
    before = snapshot(db)
    data = draft_of(client, chapter)
    content = data["content"]
    asset = upload(client, chapter)
    assert asset.status_code == 201 and asset.json()["status"] == "pending"
    background = upload(client, chapter, kind="background").json()
    content["stickers"][0].update(name="Nome proposto", asset_id=asset.json()["id"], back_text="Verso proposto")
    content["stickers"].append(new_sticker(suggested_rarity_id=3))
    content["pages"][0].update(title="Quem somos", text="Texto da página", background_asset_id=background["id"])
    content["layout"]["orientation"] = "landscape"
    assert save(client, chapter, content).status_code == 200
    assert save(client, chapter, content).status_code == 200  # salvar de novo atualiza o mesmo rascunho
    spare = upload(client, chapter).json()
    assert client.delete(f"/api/editor/chapters/{chapter.id}/assets/{spare['id']}").status_code == 204
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 200

    assert snapshot(db) == before  # nada de produção mudou, nem uma linha
    assert db.scalar(select(func.count()).select_from(ChapterRevision)) == 1
    revision = db.scalar(select(ChapterRevision))
    assert revision.status == "submitted" and revision.author_id == user.id
    assert db.get(Sticker, db.scalar(select(Sticker.id).order_by(Sticker.number).limit(1))).name == "Figurinha 1"
    assert db.scalar(select(func.count()).select_from(Sticker)) == 17


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/admin/stickers", {"org_id": 1, "name": "X", "rarity_id": 1, "slot_layout": {"x": 1, "y": 1, "w": 5, "h": 5}}),
        ("put", "/api/admin/stickers/{sticker}", {"org_id": 1, "name": "X", "rarity_id": 3}),
        ("delete", "/api/admin/stickers/{sticker}", None),
        ("put", "/api/admin/rarities/1", {"name": "comum", "rank": 1, "weight": 999, "color": "#000000"}),
        ("post", "/api/admin/rarities", {"name": "mítica", "rank": 9, "weight": 1, "color": "#000000"}),
        ("put", "/api/admin/pack-types/{pack}", {"album_id": None, "slug": "comum", "name": "X", "stickers_per_pack": 30}),
        ("post", "/api/admin/pack-types", {"slug": "novo", "name": "X", "stickers_per_pack": 30}),
        ("post", "/api/admin/codes", {"pack_type_id": 1, "count": 5}),
        ("put", "/api/admin/config/daily.stock_cap", {"value": 99}),
        ("put", "/api/admin/albums/{album}/config/daily.stock_cap", {"value": 99}),
        ("put", "/api/admin/albums/{album}", {"title": "X", "starts_at": "2030-01-01T00:00:00Z", "ends_at": "2030-02-01T00:00:00Z"}),
        ("put", "/api/admin/albums/{album}/chapters/{chapter}", {"sort_order": 9}),
        ("post", "/api/admin/albums/{album}/chapters/{chapter}/mosaic", {"cols": 2, "rows": 2, "rarity_id": 1}),
        ("post", "/api/admin/revisions/1/approve", {"note": ""}),
        ("post", "/api/admin/revisions/1/publish", {}),
        ("post", "/api/admin/revisions/1/rollback", None),
        ("post", "/api/admin/revisions/1/reject", {"note": "x"}),
        ("delete", "/api/admin/roles/1", None),
        ("get", "/api/admin/revisions", None),
        ("get", "/api/admin/editors/overview", None),
        ("post", "/api/admin/upload", None),
    ],
)
def test_editor_nao_altera_raridade_pesos_pacotes_codigos_config(db, album, chapter, editor, method, path, body):
    _, client = editor
    before = snapshot(db)
    ids = {
        "sticker": db.scalar(select(Sticker.id).limit(1)), "album": album.id, "chapter": chapter.id,
        "pack": db.scalar(text("SELECT id FROM pack_type WHERE album_id IS NOT NULL LIMIT 1")),
    }
    response = getattr(client, method)(path.format(**ids), **({"json": body} if body is not None and method != "get" else {}))
    assert response.status_code == 403, response.text
    assert snapshot(db) == before


def test_conteudo_nao_carrega_numero_raridade_nem_url_de_imagem(db, chapter, editor):
    """O editor não escolhe número nem raridade, e imagem só entra por ativo do próprio capítulo."""
    _, client = editor
    content = draft_of(client, chapter)["content"]
    for field, value in (("number", 1), ("rarity_id", 3), ("image_url", "https://evil.example/x.png"), ("is_mosaic_piece", True), ("album_id", 1)):
        bad = copy.deepcopy(content)
        bad["stickers"][0][field] = value
        response = save(client, chapter, bad)
        assert response.status_code == 422, (field, response.text)
    bad = copy.deepcopy(content)
    bad["pages"][0]["background_url"] = "https://evil.example/x.png"
    assert save(client, chapter, bad).status_code == 422
    bad = copy.deepcopy(content)
    bad["weights"] = {"1": 0}
    assert save(client, chapter, bad).status_code == 422
    assert db.scalar(select(func.count()).select_from(ChapterRevision)) == 0


# ============================================================ IDOR entre capítulos


@pytest.fixture
def other_chapter(db, album, chapter):
    add_content(db, db.get(Album, album.id), "Org B")
    return db.scalar(select(AlbumChapter).where(AlbumChapter.id != chapter.id))


def test_editor_do_capitulo_a_nao_le_nem_escreve_o_capitulo_b(db, chapter, other_chapter, editor, make_user, login, grant):
    _, client_a = editor
    user_b = make_user("EditaB")
    grant(user_b, other_chapter)
    client_b = login(user_b)
    content_b = draft_of(client_b, other_chapter)["content"]
    asset_b = upload(client_b, other_chapter).json()
    content_b["stickers"][0]["asset_id"] = asset_b["id"]
    assert save(client_b, other_chapter, content_b).status_code == 200
    before = db.scalar(select(ChapterRevision.content).where(ChapterRevision.chapter_id == other_chapter.id))

    base = f"/api/editor/chapters/{other_chapter.id}"
    for response in (
        client_a.get(f"{base}/draft"),
        client_a.put(f"{base}/draft", json={"content": content_b}),
        client_a.post(f"{base}/submit"),
        client_a.get(f"{base}/assets"),
        upload(client_a, other_chapter),
        client_a.delete(f"{base}/assets/{asset_b['id']}"),
    ):
        assert response.status_code == 404 and response.json() == {"detail": "Capítulo não encontrado."}
    # Capítulo inexistente responde igual: não dá para enumerar.
    assert client_a.get("/api/editor/chapters/999999/draft").json() == {"detail": "Capítulo não encontrado."}
    # Pelo próprio capítulo, não alcança o ativo alheio: nem apagar, nem usar no conteúdo, nem baixar.
    assert client_a.delete(f"/api/editor/chapters/{chapter.id}/assets/{asset_b['id']}").status_code == 404
    content_a = draft_of(client_a, chapter)["content"]
    content_a["stickers"][0]["asset_id"] = asset_b["id"]
    response = save(client_a, chapter, content_a)
    assert response.status_code == 422 and "não pertence a este capítulo" in response.json()["detail"]
    assert client_a.get(asset_b["url"]).status_code == 404
    assert [c["id"] for c in client_a.get("/api/editor/chapters").json()] == [chapter.id]

    db.expire_all()
    assert db.scalar(select(ChapterRevision.content).where(ChapterRevision.chapter_id == other_chapter.id)) == before
    assert db.get(Asset, asset_b["id"]) is not None
    assert db.scalar(select(func.count()).select_from(ChapterRevision).where(ChapterRevision.chapter_id == chapter.id)) == 0


def test_ativo_pendente_e_privado_ate_a_publicacao(db, chapter, editor, admin, make_user, login):
    _, client = editor
    _, admin_client = admin
    asset = upload(client, chapter).json()
    assert asset["url"].startswith("/media/capitulos/") and asset["url"].endswith(".webp")
    assert "foto" not in asset["url"]  # o nome do arquivo enviado não vai para a URL
    assert client.get(asset["url"]).status_code == 200  # editor do capítulo
    assert admin_client.get(asset["url"]).status_code == 200  # superadmin
    player = login(make_user("Jogador"))
    assert player.get(asset["url"]).status_code == 404  # jogador comum: como se não existisse
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get(asset["url"]).status_code == 401

    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["asset_id"] = asset["id"]
    revision_id = submit_and_approve(client, admin_client, chapter, content)
    assert player.get(asset["url"]).status_code == 404  # aprovado ainda não é publicado
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    assert player.get(asset["url"]).status_code == 200
    assert db.get(Asset, asset["id"]).status == "approved"
    assert client.delete(f"/api/editor/chapters/{chapter.id}/assets/{asset['id']}").status_code == 409


# ============================================================ validação do conteúdo


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)", "JAVASCRIPT:alert(1)", "http://example.com", "data:text/html,<script>alert(1)</script>",
        "//example.com", "https://", "https://localhost", "https://user:pw@example.com", "https://exa mple.com",
        "https://example.com:8443/x", "ftp://example.com", "vbscript:x", "https://exam\nple.com", "https://-bad-.com",
        "https://example.com/" + "a" * 300, "https://127.0.0.1/x",
    ],
)
def test_link_do_verso_so_https_com_host_valido(chapter, editor, url):
    _, client = editor
    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["back_links"] = [{"label": "Site", "url": url}]
    response = save(client, chapter, content)
    assert response.status_code == 422, url


def test_link_https_valido_e_aceito_e_texto_fica_como_texto(db, chapter, editor):
    _, client = editor
    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["back_links"] = [{"label": "Instagram", "url": "https://www.instagram.com/atletica?x=1"}]
    content["stickers"][0]["name"] = "  <b>Fulana</b>‮ \x00de  Tal "
    content["stickers"][0]["back_text"] = "<script>alert(1)</script>\nlinha 2"
    assert save(client, chapter, content).status_code == 200
    saved = db.scalar(select(ChapterRevision.content))["stickers"][0]
    # Nada é interpretado como HTML: o texto é guardado como texto, sem caracteres de controle.
    assert saved["name"] == "<b>Fulana</b> de Tal"
    assert saved["back_text"] == "<script>alert(1)</script>\nlinha 2"
    assert saved["back_links"] == [{"label": "Instagram", "url": "https://www.instagram.com/atletica?x=1"}]


def test_limites_de_tamanho_e_de_quantidade(chapter, editor):
    _, client = editor
    base = draft_of(client, chapter)["content"]

    def rejected(change) -> bool:
        content = copy.deepcopy(base)
        change(content)
        return save(client, chapter, content).status_code == 422

    assert rejected(lambda c: c["stickers"][0].update(name="x" * 121))
    assert rejected(lambda c: c["stickers"][0].update(name="   "))
    assert rejected(lambda c: c["stickers"][0].update(back_text="x" * 2001))
    assert rejected(lambda c: c["stickers"][0].update(hint="x" * 161))
    assert rejected(lambda c: c["stickers"][0].update(size="GG"))
    assert rejected(lambda c: c["stickers"][0].update(key="nao-e-uuid"))
    assert rejected(lambda c: c["stickers"][0].update(page_no=9))  # página que não existe
    assert rejected(lambda c: c["stickers"][0].update(slot={"x": 90, "y": 0, "w": 20, "h": 10}))  # sai da página
    assert rejected(lambda c: c["stickers"][0].update(suggested_rarity_id=999))
    assert rejected(lambda c: c["stickers"][0].update(asset_id=999999))
    assert rejected(lambda c: c["stickers"][0].update(back_links=[{"label": "a", "url": "https://example.com"}] * 6))
    assert rejected(lambda c: c["stickers"].append(dict(c["stickers"][0])))  # chave repetida
    assert rejected(lambda c: c["stickers"].extend(new_sticker() for _ in range(200)))
    assert rejected(lambda c: c["pages"].append(dict(c["pages"][0])))  # página repetida
    assert rejected(lambda c: c["pages"][0].update(text="x" * 601))
    assert rejected(lambda c: c["pages"][0].update(layout="grid-9x9"))
    assert rejected(lambda c: c.update(pages=[]))
    assert rejected(lambda c: c["layout"].update(orientation="diagonal"))
    assert rejected(lambda c: c.update(mosaic={"asset_id": None, "cols": 11, "rows": 2}))


def test_upload_recusa_o_que_nao_e_imagem_e_o_que_e_grande_demais(db, chapter, editor, media_dir):
    _, client = editor
    assert upload(client, chapter, data=b"<svg onload=alert(1)>", name="x.svg", mime="image/svg+xml").status_code == 400
    assert upload(client, chapter, data=b"MZ\x90\x00 nao sou imagem", name="x.png").status_code == 400
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    assert upload(client, chapter, data=gif.getvalue(), name="x.gif", mime="image/gif").status_code == 400
    assert upload(client, chapter, data=png() + b"\x00" * (8 * 1024 * 1024), name="x.png").status_code == 413
    assert upload(client, chapter, kind="executavel").status_code == 400
    assert db.scalar(select(func.count()).select_from(Asset)) == 0
    assert not any(media_dir.rglob("*.webp"))

    # O que é aceito é sempre reencodado: sai WebP, pequeno, com nome aleatório.
    noisy = Image.effect_noise((1600, 1600), 90).convert("RGB")
    buffer = io.BytesIO()
    noisy.save(buffer, "PNG")
    ok = upload(client, chapter, data=buffer.getvalue(), name="../../etc/passwd.png")
    assert ok.status_code == 201
    stored = (media_dir / ok.json()["url"].removeprefix("/media/")).read_bytes()
    assert stored[:4] == b"RIFF" and stored[8:12] == b"WEBP" and len(stored) <= 150 * 1024
    assert ok.json()["bytes"] == len(stored)
    assert "passwd" not in ok.json()["url"] and ".." not in ok.json()["url"]


def test_cota_de_ativos_por_capitulo_e_limite_por_hora(db, chapter, editor, monkeypatch):
    from app.api import editor as editor_api
    from app.models import Config

    _, client = editor
    monkeypatch.setattr(editor_api, "MAX_ASSETS", 3)
    for _ in range(3):
        assert upload(client, chapter).status_code == 201
    assert upload(client, chapter).status_code == 409
    monkeypatch.setattr(editor_api, "MAX_ASSETS", 100)
    db.get(Config, "rate.asset_upload_per_hour").value = 5
    db.commit()
    try:
        assert upload(client, chapter).status_code == 201  # o quinto envio da hora (o recusado pela cota contou)
        assert upload(client, chapter).status_code == 429
    finally:
        db.get(Config, "rate.asset_upload_per_hour").value = 120
        db.commit()


# ================================================================ fluxo da revisão


def test_um_rascunho_por_capitulo_enviar_congela_rejeitar_devolve(db, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    data = draft_of(client, chapter)
    assert data["revision"] is None and data["editable"] is True and data["content"] == data["published"]
    content = data["content"]
    content["stickers"][0]["name"] = "Primeira edição"
    first = save(client, chapter, content).json()["revision"]
    content["stickers"][0]["name"] = "Segunda edição"
    second = save(client, chapter, content).json()["revision"]
    assert first["id"] == second["id"] and second["status"] == "draft"
    assert db.scalar(select(func.count()).select_from(ChapterRevision)) == 1
    # O banco também garante uma revisão aberta por capítulo.
    db.add(ChapterRevision(chapter_id=chapter.id, number=99, status="draft", content={}))
    with pytest.raises(Exception, match="uq_chapter_revision_open"):
        db.commit()
    db.rollback()

    assert admin_client.get("/api/admin/revisions").json() == []  # rascunho não entra na fila
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").json()["revision"]["status"] == "submitted"
    assert save(client, chapter, content).status_code == 409  # enviada: congelada
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 409
    assert draft_of(client, chapter)["editable"] is False
    queue = admin_client.get("/api/admin/revisions").json()
    assert [(r["id"], r["org_name"], r["author"]) for r in queue] == [(first["id"], "Org Teste", "Edita")]

    assert admin_client.post(f"/api/admin/revisions/{first['id']}/reject", json={"note": ""}).status_code == 400
    rejected = admin_client.post(f"/api/admin/revisions/{first['id']}/reject", json={"note": "Falta a foto."})
    assert rejected.json()["status"] == "rejected"
    data = draft_of(client, chapter)
    assert data["editable"] is True and data["revision"]["review_note"] == "Falta a foto."
    content["stickers"][0]["name"] = "Terceira edição"
    assert save(client, chapter, content).json()["revision"]["status"] == "draft"  # mexeu: volta a rascunho
    assert db.scalar(select(func.count()).select_from(ChapterRevision)) == 1
    assert db.get(Sticker, db.scalar(select(Sticker.id).order_by(Sticker.number).limit(1))).name == "Figurinha 1"


def test_publicar_exige_revisao_aprovada(db, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["name"] = "Proposta"
    save(client, chapter, content)
    revision_id = db.scalar(select(ChapterRevision.id))
    url = f"/api/admin/revisions/{revision_id}"
    before = snapshot(db)
    assert admin_client.post(f"{url}/publish", json={}).status_code == 409  # rascunho
    assert admin_client.post(f"{url}/approve", json={}).status_code == 409  # rascunho não se aprova
    client.post(f"/api/editor/chapters/{chapter.id}/submit")
    assert admin_client.post(f"{url}/publish", json={}).status_code == 409  # enviada, não aprovada
    assert admin_client.post(f"{url}/reject", json={"note": "não"}).status_code == 200
    assert admin_client.post(f"{url}/publish", json={}).status_code == 409  # rejeitada
    assert admin_client.post(f"{url}/rollback").status_code == 409
    assert admin_client.post("/api/admin/revisions/999999/publish", json={}).status_code == 404
    assert snapshot(db) == before


def test_publicar_e_aprovar_exigem_segundo_fator(db, chapter, editor, make_user, login, mfa_login):
    _, client = editor
    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["name"] = "Proposta"
    save(client, chapter, content)
    client.post(f"/api/editor/chapters/{chapter.id}/submit")
    revision_id = db.scalar(select(ChapterRevision.id))
    url = f"/api/admin/revisions/{revision_id}"
    before = snapshot(db)

    plain = login(make_user("SemFator", admin=True))  # superadmin sem 2FA ativo
    for action, body in (("approve", {}), ("publish", {}), ("rollback", None)):
        response = plain.post(f"{url}/{action}", **({"json": body} if body is not None else {}))
        assert response.status_code == 403 and response.headers["x-mfa"] == "setup", action
        assert "Ative a verificação em duas etapas" in response.json()["detail"]
    stale_user = make_user("Vencido", admin=True)
    stale = mfa_login(stale_user, fresh=False)  # 2FA ativo, mas não confirmado nesta sessão
    assert stale.post(f"{url}/approve", json={}).headers["x-mfa"] == "verify"
    db.execute(UserSession.__table__.update().where(UserSession.user_id == stale_user.id).values(mfa_at=now() - timedelta(minutes=16)))
    db.commit()
    assert stale.post(f"{url}/approve", json={}).status_code == 403  # confirmação antiga não vale
    assert snapshot(db) == before and db.get(ChapterRevision, revision_id).status == "submitted"

    # Confirmando o código de verdade, a mesma sessão passa a poder.
    secret = db.get(User, stale_user.id).totp_secret
    assert stale.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    assert stale.post(f"{url}/approve", json={}).status_code == 200
    assert stale.post(f"{url}/publish", json={}).status_code == 200


# ============================================================== regras da publicação


def test_antes_de_comecar_publica_cria_altera_e_remove(db, album, chapter, content, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)  # agendado
    draft = draft_of(client, chapter)["content"]
    asset = upload(client, chapter).json()
    kept, removed = draft["stickers"][0], draft["stickers"][16]
    kept.update(name="Renomeada", asset_id=asset["id"], hint="Dica nova")
    draft["stickers"] = draft["stickers"][:16]
    added_a, added_b = new_sticker(name="Nova A", suggested_rarity_id=2), new_sticker(name="Nova B")
    added_c = new_sticker(name="Nova C", suggested_rarity_id=2, active=False)
    draft["stickers"] += [added_a, added_b, added_c]
    draft["pages"][0].update(title="Abertura", text="Bem-vindos")
    draft["layout"]["orientation"] = "landscape"
    revision_id = submit_and_approve(client, admin_client, chapter, draft)

    detail = admin_client.get(f"/api/admin/revisions/{revision_id}").json()
    assert sorted(s["name"] for s in detail["diff"]["added"]) == ["Nova A", "Nova B", "Nova C"]
    assert [s["name"] for s in detail["diff"]["removed"]] == ["Figurinha 17"]
    changed = {s["name"]: s["fields"] for s in detail["diff"]["changed"]}
    assert set(changed) == {"Renomeada"} and set(changed["Renomeada"]) == {"name", "hint", "image"}
    assert changed["Renomeada"]["image"] == {"old": "/media/x/1.webp", "new": asset["url"]}
    assert detail["diff"]["orientation"] == {"old": "portrait", "new": "landscape"}
    assert detail["diff"]["pages"][0]["fields"]["title"] == {"old": "", "new": "Abertura"}

    published = admin_client.post(
        f"/api/admin/revisions/{revision_id}/publish",
        json={"stickers": {added_b["key"]: {"rarity_id": 3, "number": 500}}},
    )
    assert published.status_code == 200 and published.json()["status"] == "published" and published.json()["is_live"]
    db.expire_all()
    rows = {s.content_key: s for s in db.scalars(select(Sticker).where(Sticker.album_id == album.id))}
    assert len(rows) == 19 and removed["key"] not in rows
    assert (rows[kept["key"]].name, rows[kept["key"]].image_url, rows[kept["key"]].number, rows[kept["key"]].rarity_id) == (
        "Renomeada", asset["url"], 1, content[0].rarity_id,
    )
    # Número: próximo livre do álbum quando o superadmin não escolhe. Raridade: escolhida > sugerida > a mais comum.
    assert (rows[added_a["key"]].number, rows[added_a["key"]].rarity_id) == (17, 2)  # o 17 ficou livre com a remoção
    assert (rows[added_b["key"]].number, rows[added_b["key"]].rarity_id) == (500, 3)
    assert (rows[added_c["key"]].number, rows[added_c["key"]].active) == (501, False)
    assert db.get(AlbumChapter, chapter.id).layout["orientation"] == "landscape"
    assert db.get(AlbumChapter, chapter.id).layout["pages"][0]["title"] == "Abertura"
    assert db.get(AlbumChapter, chapter.id).published_revision_id == revision_id
    # O jogador vê o publicado quando o álbum começar.
    set_period(db, album, -1, 100)
    page = client.get(f"/api/album/orgs/{chapter.org_id}/pages/1").json()
    assert page["page"]["title"] == "Abertura" and page["orientation"] == "landscape"
    assert len(page["slots"]) == 18  # 16 antigas + A e B; a C está inativa


def test_numero_ja_usado_e_raridade_desconhecida_sao_recusados_sem_efeito(db, album, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    added = new_sticker()
    draft["stickers"].append(added)
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    before = snapshot(db)
    url = f"/api/admin/revisions/{revision_id}/publish"
    assert admin_client.post(url, json={"stickers": {added["key"]: {"number": 3}}}).status_code == 409
    assert admin_client.post(url, json={"stickers": {added["key"]: {"rarity_id": 999}}}).status_code == 422
    assert admin_client.post(url, json={"stickers": {str(uuid.uuid4()): {"rarity_id": 2}}}).status_code == 422
    assert snapshot(db) == before and db.get(ChapterRevision, revision_id).status == "approved"


def test_album_em_andamento_so_publica_o_cosmetico(db, album, chapter, content, editor, admin, give, make_user):
    _, client = editor
    _, admin_client = admin
    player = make_user("Tem")
    give(player, content[0], 2, pasted=True)
    data = draft_of(client, chapter)
    assert data["chapter"]["frozen"] is True
    base = data["content"]

    # O servidor recusa já no rascunho o que mexe na estrutura.
    for change in (
        lambda c: c["stickers"].pop(),
        lambda c: c["stickers"][0].update(active=False),
        lambda c: c.update(mosaic={"asset_id": upload(client, chapter, kind="mosaic").json()["id"], "cols": 2, "rows": 2}),
    ):
        bad = copy.deepcopy(base)
        change(bad)
        response = save(client, chapter, bad)
        assert response.status_code == 409 and "congelada" in response.json()["detail"]

    # E a publicação confere de novo: uma revisão aprovada antes do álbum começar não fura o congelamento.
    set_period(db, album, 10, 100)
    structural = copy.deepcopy(base)
    removed = structural["stickers"].pop()
    revision_id = submit_and_approve(client, admin_client, chapter, structural)
    set_period(db, album, -1, 100)
    before = snapshot(db)
    response = admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={})
    assert response.status_code == 409 and f"figurinha removida: {removed['name']}" in response.json()["detail"]
    assert snapshot(db) == before
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/reject", json={"note": "x"}).status_code == 409  # aprovada não se rejeita
    db.execute(ChapterRevision.__table__.delete())
    db.commit()

    # Cosmético passa: nome, textos, links, imagem, posição, página, fundo e orientação.
    asset = upload(client, chapter).json()
    cosmetic = copy.deepcopy(base)
    cosmetic["pages"].append({"page_no": 2, "title": "Mais gente", "text": "", "background_asset_id": None, "layout": "grid-2x3"})
    cosmetic["stickers"][0].update(
        name="Nome novo", tag="gestão", hint="Dica", back_text="Verso novo", asset_id=asset["id"], size="G", page_no=2,
        slot={"x": 10, "y": 10, "w": 30, "h": 40}, back_links=[{"label": "Site", "url": "https://example.com"}],
        suggested_rarity_id=3,  # sugestão é ignorada: a raridade não muda com o álbum em andamento
    )
    revision_id = submit_and_approve(client, admin_client, chapter, cosmetic)
    url = f"/api/admin/revisions/{revision_id}/publish"
    key = cosmetic["stickers"][0]["key"]
    assert admin_client.post(url, json={"stickers": {key: {"rarity_id": 3}}}).status_code == 409  # nem o superadmin muda raridade
    assert admin_client.post(url, json={"stickers": {key: {"number": 900}}}).status_code == 409  # nem renumera
    assert admin_client.post(url, json={}).status_code == 200
    db.expire_all()
    row = db.get(Sticker, content[0].id)
    assert (row.name, row.tag, row.back_text, row.image_url, row.size, row.page_no) == ("Nome novo", "gestão", "Verso novo", asset["url"], "G", 2)
    assert (row.number, row.rarity_id, row.active) == (content[0].number, content[0].rarity_id, True)
    assert db.scalar(select(func.count()).select_from(Sticker)) == 17
    # O inventário do jogador não foi tocado.
    assert db.execute(text("SELECT quantity, pasted FROM user_sticker WHERE user_id = :u"), {"u": player.id}).one() == (2, True)


def test_album_em_andamento_aceita_figurinha_nova_no_proximo_numero(db, album, chapter, content, editor, admin, give, make_user):
    _, client = editor
    _, admin_client = admin
    player = make_user("Tem")
    give(player, content[0], 1, pasted=True)
    base = draft_of(client, chapter)["content"]
    draft = copy.deepcopy(base)
    draft["stickers"].append(new_sticker(name="Tardia", suggested_rarity_id=3))
    key = draft["stickers"][-1]["key"]
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    top = db.scalar(select(func.max(Sticker.number)).where(Sticker.album_id == album.id))
    url = f"/api/admin/revisions/{revision_id}/publish"
    assert admin_client.post(url, json={"stickers": {key: {"number": 1}}}).status_code == 409  # número em uso
    assert admin_client.post(url, json={"stickers": {key: {"rarity_id": 2}}}).status_code == 200  # revisor escolhe a raridade
    db.expire_all()
    row = db.scalar(select(Sticker).where(Sticker.name == "Tardia"))
    assert (row.number, row.rarity_id, row.active) == (top + 1, 2, True)
    # O que já existia e o inventário do jogador seguem intactos.
    assert db.execute(text("SELECT quantity, pasted FROM user_sticker WHERE user_id = :u"), {"u": player.id}).one() == (1, True)
    # Já removê-la agora é estrutura: continua barrado.
    again = copy.deepcopy(draft_of(client, chapter)["content"])
    again["stickers"] = [s for s in again["stickers"] if s["key"] != key]
    response = save(client, chapter, again)
    assert response.status_code == 409 and "congelada" in response.json()["detail"]


def test_versao_do_album_muda_quando_a_revisao_e_publicada(db, album, chapter, content, editor, admin, make_user, login):
    _, client = editor
    _, admin_client = admin
    player = login(make_user("Joga"))
    before = player.get("/api/album/version").json()["version"]
    assert player.get("/api/album/version").json()["version"] == before  # estável sem mudança
    draft = draft_of(client, chapter)["content"]
    draft["stickers"][0]["name"] = "Mudou ao vivo"
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    assert player.get("/api/album/version").json()["version"] == before  # aprovada ainda não vale
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    after = player.get("/api/album/version").json()["version"]
    assert after != before
    # O jogador vê a mudança na hora, sem reiniciar nada: é só pedir de novo.
    sticker = db.scalar(select(Sticker).where(Sticker.name == "Mudou ao vivo"))
    assert sticker is not None


def test_mosaico_guarda_origem_e_enquadramento_sem_levar_para_producao(db, album, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    poster = upload(client, chapter, kind="mosaic", data=png((10, 90, 160), (300, 400))).json()
    source = upload(client, chapter, kind="mosaic_source", data=png((10, 90, 160), (900, 1200))).json()
    crop = {"zoom": 1.4, "x": 0.1, "y": -0.05, "turns": 1, "flip": True, "fit": "whole"}
    draft["mosaic"] = {"asset_id": poster["id"], "cols": 1, "rows": 1, "source_asset_id": source["id"], "crop": crop}
    assert save(client, chapter, draft).status_code == 200
    bad = copy.deepcopy(draft)
    bad["mosaic"]["crop"]["turns"] = 4
    assert save(client, chapter, bad).status_code == 422
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    detail = admin_client.get(f"/api/admin/revisions/{revision_id}").json()
    assert detail["diff"]["mosaic"]["new"] == {"asset_id": poster["id"], "cols": 1, "rows": 1, "scale": 1.0, "rot": 0.0, "image_url": poster["url"]}
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    # A origem continua privada; só a imagem do mosaico foi publicada.
    db.expire_all()
    assert db.get(Asset, source["id"]).status == "pending" and db.get(Asset, poster["id"]).status == "approved"
    # Reabrir o rascunho devolve o enquadramento para o editor ajustar de novo.
    saved = save(client, chapter, draft).json()["content"]
    assert saved["mosaic"]["crop"] == crop


def test_pecas_do_mosaico_tem_texto_proprio_mesmo_com_album_em_andamento(db, album, chapter, editor, admin, make_user, login, give):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    poster = upload(client, chapter, kind="mosaic", data=png((10, 90, 160), (600, 800))).json()
    draft["mosaic"] = {"asset_id": poster["id"], "cols": 2, "rows": 2}
    first = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{first}/publish", json={}).status_code == 200
    set_period(db, album, -1, 100)  # começou: a estrutura do mosaico está congelada
    published = draft_of(client, chapter)["content"]
    assert [p["index"] for p in published["mosaic"]["pieces"]] == [0, 1, 2, 3]
    assert published["mosaic"]["pieces"][0]["name"].endswith("peça 1 de 4")

    edited = copy.deepcopy(published)
    edited["mosaic"]["pieces"][1].update(name="Quadro da diretoria", back_text="Quem somos", hint="Canto direito",
                                         back_links=[{"label": "Site", "url": "https://example.com"}])
    bad = copy.deepcopy(edited)
    bad["mosaic"]["pieces"].append({"index": 7, "name": "Fora da grade"})
    assert save(client, chapter, bad).status_code == 422
    revision_id = submit_and_approve(client, admin_client, chapter, edited)
    diff = admin_client.get(f"/api/admin/revisions/{revision_id}").json()["diff"]
    assert diff["mosaic"] is None and [(c["index"], sorted(c["fields"])) for c in diff["mosaic_pieces"]] == [(1, ["back_links", "back_text", "hint", "name"])]
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    db.expire_all()
    piece = db.scalar(select(Sticker).where(Sticker.is_mosaic_piece.is_(True), Sticker.mosaic_index == 1))
    assert (piece.name, piece.back_text, piece.hint, piece.back_links) == ("Quadro da diretoria", "Quem somos", "Canto direito", [{"label": "Site", "url": "https://example.com"}])
    # O jogador que colou a peça lê o verso como o de qualquer figurinha.
    player = make_user("Colou")
    give(player, piece, 1, pasted=True)
    back = login(player).get(f"/api/stickers/{piece.id}/back").json()
    assert back["back_text"] == "Quem somos" and back["back_links"][0]["label"] == "Site"
    # Desfazer devolve os textos de antes.
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/rollback").status_code == 200
    db.expire_all()
    assert db.get(Sticker, piece.id).name.endswith("peça 2 de 4")


def test_mosaico_muda_de_tamanho_e_gira_na_pagina_com_album_em_andamento(db, album, chapter, editor, admin, make_user, login):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    poster = upload(client, chapter, kind="mosaic", data=png((10, 90, 160), (600, 800))).json()
    draft["mosaic"] = {"asset_id": poster["id"], "cols": 2, "rows": 2}
    first = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{first}/publish", json={}).status_code == 200
    set_period(db, album, -1, 100)
    org_id = db.get(AlbumChapter, chapter.id).org_id
    player = login(make_user("Veja"))
    page = player.get(f"/api/album/orgs/{org_id}/pages/0").json()
    assert page["org"]["mosaic_scale"] == 1 and page["org"]["mosaic_rot"] == 0

    edited = copy.deepcopy(draft_of(client, chapter)["content"])
    edited["mosaic"].update(scale=1.3, rot=-8)
    for bad in ({"scale": 3}, {"scale": 0.1}, {"rot": 300}):
        wrong = copy.deepcopy(edited)
        wrong["mosaic"].update(bad)
        assert save(client, chapter, wrong).status_code == 422
    revision_id = submit_and_approve(client, admin_client, chapter, edited)
    mosaic_diff = admin_client.get(f"/api/admin/revisions/{revision_id}").json()["diff"]["mosaic"]
    assert mosaic_diff["old"]["scale"] == 1 and mosaic_diff["new"]["scale"] == 1.3 and mosaic_diff["new"]["rot"] == -8
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    page = player.get(f"/api/album/orgs/{org_id}/pages/0").json()
    assert page["org"]["mosaic_scale"] == 1.3 and page["org"]["mosaic_rot"] == -8
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/rollback").status_code == 200
    assert player.get(f"/api/album/orgs/{org_id}/pages/0").json()["org"]["mosaic_scale"] == 1


def test_figurinha_gira_na_pagina_mesmo_com_album_em_andamento(db, album, chapter, content, editor, admin):
    _, client = editor
    _, admin_client = admin
    base = draft_of(client, chapter)["content"]
    # Sem mudar nada, a diferença com o publicado é vazia (o rot padrão 0 não conta como mudança).
    assert save(client, chapter, base).status_code == 200
    draft = copy.deepcopy(base)
    draft["stickers"][0]["slot"]["rot"] = -12.5
    bad = copy.deepcopy(base)
    bad["stickers"][0]["slot"]["rot"] = 200
    assert save(client, chapter, bad).status_code == 422
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    diff = admin_client.get(f"/api/admin/revisions/{revision_id}").json()["diff"]
    assert [c["name"] for c in diff["changed"]] == [draft["stickers"][0]["name"]] and diff["changed"][0]["fields"].keys() == {"slot"}
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    db.expire_all()
    assert db.get(Sticker, content[0].id).slot_layout["rot"] == -12.5
    assert client.get(f"/api/editor/chapters/{chapter.id}/draft").json()["published"]["stickers"][0]["slot"]["rot"] == -12.5


def test_editor_retira_o_envio_e_edita_de_novo(db, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    draft = draft_of(client, chapter)["content"]
    assert client.post(f"/api/editor/chapters/{chapter.id}/withdraw").status_code == 409  # nada enviado
    assert save(client, chapter, draft).status_code == 200
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 200
    assert save(client, chapter, draft).status_code == 409
    assert client.post(f"/api/editor/chapters/{chapter.id}/withdraw").status_code == 200
    assert admin_client.get("/api/admin/revisions").json() == []  # saiu da fila
    draft["pages"][0]["title"] = "Mudei"
    assert save(client, chapter, draft).status_code == 200
    # Aprovada e ainda não publicada também volta; publicada não.
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    assert client.post(f"/api/editor/chapters/{chapter.id}/withdraw").status_code == 200
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 409
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    assert client.post(f"/api/editor/chapters/{chapter.id}/withdraw").status_code == 409
    assert "revision.withdraw" in {log.action for log in db.scalars(select(AuditLog))}


# ====================================================================== revisor


@pytest.fixture
def reviewer(db, chapter, make_user, mfa_login, admin):
    user = make_user("Revisa")
    _, admin_client = admin
    response = admin_client.post(f"/api/admin/chapters/{chapter.id}/editors", json={"email": user.email, "role": "reviewer"})
    assert response.status_code == 201 and response.json()["role"] == "reviewer"
    return user, mfa_login(user)


def test_revisor_aprova_publica_e_desfaz_so_no_seu_capitulo(db, album, chapter, other_chapter, editor, reviewer, admin):
    _, client = editor
    _, rev = reviewer
    draft = draft_of(client, chapter)["content"]
    draft["stickers"][0]["name"] = "Por revisor"
    assert save(client, chapter, draft).status_code == 200
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 200
    queue = rev.get("/api/admin/revisions").json()
    assert [r["chapter_id"] for r in queue] == [chapter.id]
    revision_id = queue[0]["id"]
    assert rev.get(f"/api/admin/revisions/{revision_id}").status_code == 200
    assert rev.post(f"/api/admin/revisions/{revision_id}/approve", json={"note": "ok"}).status_code == 200
    assert rev.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.name == "Por revisor")) == 1
    assert rev.post(f"/api/admin/revisions/{revision_id}/rollback").status_code == 200
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.name == "Por revisor")) == 0
    actions = {log.action for log in db.scalars(select(AuditLog))}
    assert {"revision.approve", "revision.publish", "revision.rollback"} <= actions


def test_revisor_nao_alcanca_o_que_nao_e_dele(db, chapter, other_chapter, editor, reviewer, make_user, login, admin):
    _, client = editor
    _, rev = reviewer
    # Revisão de outro capítulo: mesma 404 de inexistente.
    other_rev = ChapterRevision(chapter_id=other_chapter.id, number=1, status="submitted", content={})
    db.add(other_rev)
    db.commit()
    for method, path in (("get", ""), ("post", "/approve"), ("post", "/reject"), ("post", "/publish"), ("post", "/rollback")):
        response = getattr(rev, method)(f"/api/admin/revisions/{other_rev.id}{path}", **({"json": {}} if method == "post" else {}))
        assert response.status_code == 404, path
    assert rev.get(f"/api/admin/revisions?chapter_id={other_chapter.id}").status_code == 404
    # Papéis, auditoria e visão geral seguem só do superadmin; conceder também.
    me = db.scalar(select(User).where(User.nickname == "Revisa"))
    assert rev.get(f"/api/admin/chapters/{chapter.id}/editors").status_code == 403
    assert rev.get(f"/api/admin/chapters/{chapter.id}/audit").status_code == 403
    assert rev.get("/api/admin/editors/overview").status_code == 403
    assert rev.post(f"/api/admin/chapters/{chapter.id}/editors", json={"email": me.email}).status_code == 403
    # Quem não tem papel nenhum fica fora da fila.
    assert login(make_user("Nada")).get("/api/admin/revisions").status_code == 403
    # E o painel de admin continua fechado.
    assert rev.get("/api/admin/albums").status_code == 403


def test_revisor_precisa_de_2fa_e_nao_julga_a_propria_revisao(db, chapter, editor, make_user, login, mfa_login, admin, grant):
    _, admin_client = admin
    user = make_user("Dupla")
    grant(user, chapter)  # editor e revisor do mesmo capítulo
    assert admin_client.post(f"/api/admin/chapters/{chapter.id}/editors", json={"email": user.email, "role": "reviewer"}).status_code == 201
    plain = login(user)
    assert plain.get("/api/me").json()["reviewer_chapters"] == 1
    assert plain.get("/api/me").json()["editor_chapters"] == 1
    draft = draft_of(plain, chapter)["content"]
    assert save(plain, chapter, draft).status_code == 200
    assert plain.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 200
    revision_id = plain.get("/api/admin/revisions").json()[0]["id"]
    response = plain.post(f"/api/admin/revisions/{revision_id}/approve", json={"note": "x"})
    assert response.status_code == 403 and response.headers["x-mfa"] == "setup"  # sem 2FA ativo
    strong = mfa_login(user)
    assert strong.post(f"/api/admin/revisions/{revision_id}/approve", json={"note": "x"}).status_code == 403
    assert strong.post(f"/api/admin/revisions/{revision_id}/reject", json={"note": "x"}).status_code == 403
    # O superadmin pode.
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/approve", json={"note": "ok"}).status_code == 200


def test_revogar_revisor_vale_na_hora_e_ele_ativa_o_2fa(db, chapter, reviewer, admin, login):
    user, rev = reviewer
    _, admin_client = admin
    assert rev.get("/api/admin/revisions").status_code == 200
    grant_id = db.scalar(select(RoleGrant.id).where(RoleGrant.user_id == user.id))
    assert admin_client.delete(f"/api/admin/roles/{grant_id}").status_code == 204
    assert rev.get("/api/admin/revisions").status_code == 403
    assert rev.get("/api/me").json()["reviewer_chapters"] == 0


def test_revisor_ve_imagem_privada_do_capitulo(db, chapter, editor, reviewer, make_user, login):
    _, client = editor
    _, rev = reviewer
    asset = upload(client, chapter).json()
    assert rev.get(asset["url"]).status_code == 200
    assert login(make_user("Fora")).get(asset["url"]).status_code == 404


def test_album_encerrado_recusa_tudo(db, album, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    content = draft_of(client, chapter)["content"]
    content["stickers"][0]["name"] = "Proposta"
    revision_id = submit_and_approve(client, admin_client, chapter, content)
    set_period(db, album, -30, -1)
    before = snapshot(db)
    assert admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={}).status_code == 409
    assert snapshot(db) == before
    db.execute(ChapterRevision.__table__.delete())
    db.commit()
    assert draft_of(client, chapter)["chapter"]["closed"] is True
    assert save(client, chapter, content).status_code == 409
    assert client.post(f"/api/editor/chapters/{chapter.id}/submit").status_code == 409
    assert upload(client, chapter).status_code == 409
    assert snapshot(db) == before


def test_rollback_restaura_o_que_havia(db, album, chapter, content, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    original = {s.content_key: (s.number, s.name, s.rarity_id, s.image_url, s.active, s.page_no) for s in content}
    draft = draft_of(client, chapter)["content"]
    draft["stickers"][0]["name"] = "Mudou"
    removed_key = draft["stickers"].pop()["key"]
    draft["stickers"].append(new_sticker(name="Entrou"))
    draft["pages"][0]["title"] = "Título novo"
    first = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{first}/publish", json={}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.name == "Entrou")) == 1

    assert admin_client.get(f"/api/admin/revisions/{first}").json()["can_rollback"] is True
    restored = admin_client.post(f"/api/admin/revisions/{first}/rollback")
    assert restored.status_code == 200 and restored.json()["status"] == "published" and restored.json()["is_live"]
    db.expire_all()
    now_rows = {
        s.content_key: (s.number, s.name, s.rarity_id, s.image_url, s.active, s.page_no)
        for s in db.scalars(select(Sticker).where(Sticker.album_id == album.id))
    }
    assert now_rows == original  # inclusive a figurinha removida, de volta com o mesmo número e raridade
    assert removed_key in now_rows
    assert db.get(AlbumChapter, chapter.id).layout["pages"][0]["title"] == ""
    assert db.get(ChapterRevision, first).status == "superseded"
    assert admin_client.post(f"/api/admin/revisions/{first}/rollback").status_code == 409  # não está mais publicada
    history = admin_client.get(f"/api/admin/revisions?chapter_id={chapter.id}").json()
    assert [(r["number"], r["status"]) for r in history] == [(2, "published"), (1, "superseded")]
    # Dá para desfazer o rollback (volta o conteúdo da revisão 1).
    again = admin_client.post(f"/api/admin/revisions/{restored.json()['id']}/rollback")
    assert again.status_code == 200
    assert db.scalar(select(func.count()).select_from(Sticker).where(Sticker.name == "Entrou")) == 1


def test_rollback_respeita_o_album_em_andamento(db, album, chapter, content, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    draft["stickers"].append(new_sticker(name="Entrou"))
    first = submit_and_approve(client, admin_client, chapter, draft)
    admin_client.post(f"/api/admin/revisions/{first}/publish", json={})
    set_period(db, album, -1, 100)  # o álbum começou: desfazer removeria uma figurinha
    before = snapshot(db)
    response = admin_client.post(f"/api/admin/revisions/{first}/rollback")
    assert response.status_code == 409 and "congelada" in response.json()["detail"]
    assert snapshot(db) == before

    # Já um rollback cosmético passa.
    cosmetic = draft_of(client, chapter)["content"]
    cosmetic["stickers"][0]["name"] = "Só o nome"
    second = submit_and_approve(client, admin_client, chapter, cosmetic)
    admin_client.post(f"/api/admin/revisions/{second}/publish", json={})
    assert admin_client.post(f"/api/admin/revisions/{second}/rollback").status_code == 200
    db.expire_all()
    assert db.get(Sticker, content[0].id).name == "Figurinha 1"
    assert db.scalar(select(func.count()).select_from(Sticker)) == 18


def test_mosaico_pela_publicacao(db, album, chapter, editor, admin):
    _, client = editor
    _, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    poster = upload(client, chapter, kind="mosaic", data=png((10, 90, 160), (400, 600))).json()
    draft["mosaic"] = {"asset_id": poster["id"], "cols": 2, "rows": 3}
    first = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{first}/publish", json={"mosaic_rarity_id": 2}).status_code == 200
    pieces = db.scalars(select(Sticker).where(Sticker.is_mosaic_piece.is_(True)).order_by(Sticker.mosaic_index)).all()
    assert len(pieces) == 6 and {p.rarity_id for p in pieces} == {2} and [p.number for p in pieces] == list(range(18, 24))
    # Toda peça tem a proporção 3:4 das figurinhas normais, mesmo com a imagem em outra proporção (400x600 numa grade 2x3).
    from app.services.images import read_media
    for piece in pieces:
        with Image.open(io.BytesIO(read_media(piece.image_url))) as tile:
            assert abs(tile.width / tile.height - 3 / 4) < 0.02, (tile.width, tile.height)
    db.expire_all()
    assert db.get(AlbumChapter, chapter.id).mosaic_image_url == poster["url"]
    old_images = [p.image_url for p in pieces]

    # Com o álbum em andamento: trocar a imagem (mesma grade) é cosmético; mudar a grade ou remover não.
    set_period(db, album, -1, 100)
    draft = draft_of(client, chapter)["content"]
    assert {k: draft["mosaic"][k] for k in ("asset_id", "cols", "rows")} == {"asset_id": poster["id"], "cols": 2, "rows": 3}
    assert len(draft["mosaic"]["pieces"]) == 6
    other = upload(client, chapter, kind="mosaic", data=png((200, 200, 0), (400, 600))).json()
    assert save(client, chapter, {**draft, "mosaic": {"asset_id": other["id"], "cols": 3, "rows": 3}}).status_code == 409
    assert save(client, chapter, {**draft, "mosaic": None}).status_code == 409
    draft["mosaic"]["asset_id"] = other["id"]
    second = submit_and_approve(client, admin_client, chapter, draft)
    assert admin_client.post(f"/api/admin/revisions/{second}/publish", json={}).status_code == 200
    db.expire_all()
    after = db.scalars(select(Sticker).where(Sticker.is_mosaic_piece.is_(True)).order_by(Sticker.mosaic_index)).all()
    assert [p.id for p in after] == [p.id for p in pieces] and [p.number for p in after] == list(range(18, 24))
    assert all(new != old for new, old in zip([p.image_url for p in after], old_images))
    assert db.get(AlbumChapter, chapter.id).mosaic_image_url == other["url"]


def test_acoes_de_revisao_sao_auditadas(db, album, chapter, editor, admin):
    editor_user, client = editor
    admin_user, admin_client = admin
    set_period(db, album, 10, 100)
    draft = draft_of(client, chapter)["content"]
    draft["stickers"][0]["name"] = "A"
    save(client, chapter, draft)
    client.post(f"/api/editor/chapters/{chapter.id}/submit")
    revision_id = db.scalar(select(ChapterRevision.id))
    admin_client.post(f"/api/admin/revisions/{revision_id}/reject", json={"note": "ajuste"})
    draft["stickers"][0]["name"] = "B"
    revision_id = submit_and_approve(client, admin_client, chapter, draft)
    admin_client.post(f"/api/admin/revisions/{revision_id}/publish", json={})
    admin_client.post(f"/api/admin/revisions/{revision_id}/rollback")

    rows = db.execute(
        select(AuditLog.action, AuditLog.user_id).where(AuditLog.action.like("revision.%")).order_by(AuditLog.id)
    ).all()
    assert [tuple(r) for r in rows] == [
        ("revision.edit", editor_user.id), ("revision.submit", editor_user.id), ("revision.reject", admin_user.id), ("revision.submit", editor_user.id),
        ("revision.approve", admin_user.id), ("revision.publish", admin_user.id), ("revision.rollback", admin_user.id),
    ]
    trail = admin_client.get(f"/api/admin/chapters/{chapter.id}/audit").json()
    assert [t["action"] for t in trail][:2] == ["revision.rollback", "revision.publish"]
    assert {t["by"] for t in trail} == {"Edita", "Super"}
    overview = admin_client.get("/api/admin/editors/overview").json()
    assert overview[0]["org_name"] == "Org Teste" and [e["nickname"] for e in overview[0]["editors"]] == ["Edita"]
    assert overview[0]["last_edit_at"] is not None and overview[0]["revision"] is None


# ========================================================================== 2FA


def _setup(client) -> str:
    response = client.post("/api/auth/2fa/setup", json={"password": "senha-de-teste"})
    assert response.status_code == 200, response.text
    assert response.json()["otpauth_uri"].startswith("otpauth://totp/UniAlbum:")
    return response.json()["secret"]


def test_ativar_2fa_confirma_codigo_e_entrega_codigos_de_recuperacao(db, make_user, login):
    user = make_user("Dois", admin=True)
    client = login(user)
    assert client.get("/api/me").json()["mfa_enabled"] is False
    assert client.post("/api/auth/2fa/setup", json={"password": "errada"}).status_code == 403
    secret = _setup(client)
    assert client.get("/api/me").json()["mfa_enabled"] is False  # só vale depois de confirmar
    assert client.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    enabled = client.post("/api/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
    assert enabled.status_code == 200
    codes = enabled.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    me = client.get("/api/me").json()
    assert me["mfa_enabled"] is True and me["mfa_verified"] is True
    # No banco só há hashes, nunca o código.
    stored = db.scalars(select(RecoveryCode.code_hash).where(RecoveryCode.user_id == user.id)).all()
    assert len(stored) == 10 and all(len(h) == 64 for h in stored)
    assert not set(stored) & set(codes) and not any(code.replace("-", "") in h for code in codes for h in stored)
    assert client.post("/api/auth/2fa/setup", json={"password": "senha-de-teste"}).status_code == 409


def test_2fa_e_so_para_superadmin_e_revisor(make_user, login):
    client = login(make_user("Comum"))
    for path, body in (("setup", {"password": "senha-de-teste"}), ("enable", {"code": "123456"}), ("verify", {"code": "123456"})):
        assert client.post(f"/api/auth/2fa/{path}", json=body).status_code == 403


def test_codigo_totp_nao_vale_duas_vezes_e_codigo_de_recuperacao_tambem_nao(db, make_user, login):
    user = make_user("Replay", admin=True)
    client = login(user)
    secret = _setup(client)
    codes = client.post("/api/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()}).json()["recovery_codes"]
    other = login(user)  # outra sessão: ainda não confirmou
    assert other.get("/api/me").json()["mfa_verified"] is False
    # O código usado para ativar já foi gasto: reapresentá-lo (replay) falha.
    assert other.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()}).status_code == 400
    assert other.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).at(time.time() + 30)}).status_code == 200
    assert other.get("/api/me").json()["mfa_verified"] is True

    third = login(user)
    assert third.post("/api/auth/2fa/verify", json={"code": codes[0].upper().replace("-", " ")}).json()["method"] == "recovery"
    assert third.post("/api/auth/2fa/verify", json={"code": codes[0]}).status_code == 400  # uso único
    assert db.scalar(select(func.count()).select_from(RecoveryCode).where(RecoveryCode.used_at.is_not(None))) == 1
    assert login(user).post("/api/auth/2fa/verify", json={"code": codes[1]}).json()["recovery_codes_left"] == 8


def test_bloqueio_por_tentativas_erradas(db, make_user, mfa_login):
    user = make_user("Bruta", admin=True)
    client = mfa_login(user, fresh=False)
    for _ in range(5):
        assert client.post("/api/auth/2fa/verify", json={"code": "000000"}).status_code == 400
    secret = db.get(User, user.id).totp_secret
    # Bloqueado: nem o código certo passa até a janela acabar.
    blocked = client.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()})
    assert blocked.status_code == 429
    assert client.get("/api/me").json()["mfa_verified"] is False
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "mfa.verify_fail")) == 5


def test_desligar_2fa_pede_senha_e_segundo_fator(db, make_user, mfa_login):
    user = make_user("Desliga", admin=True)
    stale = mfa_login(user, fresh=False)
    assert stale.post("/api/auth/2fa/disable", json={"password": "errada"}).status_code == 403
    response = stale.post("/api/auth/2fa/disable", json={"password": "senha-de-teste"})
    assert response.status_code == 403 and response.headers["x-mfa"] == "verify"
    secret = db.get(User, user.id).totp_secret
    stale.post("/api/auth/2fa/verify", json={"code": pyotp.TOTP(secret).now()})
    assert stale.post("/api/auth/2fa/disable", json={"password": "senha-de-teste"}).status_code == 200
    db.expire_all()
    row = db.get(User, user.id)
    assert row.totp_secret is None and row.totp_enabled_at is None
    assert stale.get("/api/me").json() | {} and stale.get("/api/me").json()["mfa_enabled"] is False


def test_cli_reset_2fa(db, make_user, mfa_login, capsys):
    from app.cli import reset_2fa

    user = make_user("Perdeu", admin=True)
    client = mfa_login(user)
    db.add(RecoveryCode(user_id=user.id, code_hash="x" * 64))
    db.commit()
    reset_2fa("PERDEU@example.com")
    assert "desligada" in capsys.readouterr().out
    db.expire_all()
    assert db.get(User, user.id).totp_secret is None
    assert db.scalar(select(func.count()).select_from(RecoveryCode)) == 0
    me = client.get("/api/me").json()
    assert me["mfa_enabled"] is False and me["mfa_verified"] is False
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "mfa.reset_cli")) == 1
    with pytest.raises(SystemExit):
        reset_2fa("ninguem@example.com")


def test_segredo_e_passos_do_totp(db, make_user):
    """Unidade: aceita o passo atual e os vizinhos, recusa fora da janela e formato errado."""
    user = make_user("Unit", admin=True)
    user.totp_secret = pyotp.random_base32()
    totp = pyotp.TOTP(user.totp_secret)
    moment = time.time()
    assert mfa_service._check_totp(user, totp.at(moment - 90)) is False
    assert mfa_service._check_totp(user, "12345") is False and mfa_service._check_totp(user, "abcdef") is False
    assert mfa_service._check_totp(user, totp.at(moment - 30)) is True
    assert mfa_service._check_totp(user, totp.at(moment - 30)) is False  # mesmo passo
    assert mfa_service._check_totp(user, f"{totp.at(moment)[:3]} {totp.at(moment)[3:]}") is True  # com espaço
    db.rollback()


def test_conteudo_de_producao_ida_e_volta(db, chapter):
    """O que está em produção, convertido para o formato do editor, valida e não muda nada ao ser reaplicado."""
    snapshot_before = snapshot(db)
    current = revisions.production_content(db, chapter)
    db.commit()
    content = revisions.parse_content(current["content"])
    assert len(content.stickers) == 17 and revisions.structural_problems(db, chapter, content) == []
    assert snapshot(db) == snapshot_before


def test_identidade_da_org_publica_em_andamento_e_volta_no_rollback(db, album, chapter, content, editor, admin):
    _, client = editor
    _, admin_client = admin
    org = chapter.org
    before = (org.name, org.description, org.theme_color, org.logo_url)
    draft = draft_of(client, chapter)["content"]
    assert draft["org"]["name"] == org.name  # ponto de partida: o que está publicado

    logo = upload(client, chapter, kind="logo").json()
    draft["org"] = {"name": "  Nome  novo ", "description": "Quem somos", "color": "#aa3300", "logo_asset_id": logo["id"]}
    # Álbum em andamento: identidade é cosmética e passa.
    first = submit_and_approve(client, admin_client, chapter, draft)
    detail = admin_client.get(f"/api/admin/revisions/{first}").json()
    assert set(detail["diff"]["org"]) == {"name", "description", "color", "logo"}
    assert admin_client.post(f"/api/admin/revisions/{first}/publish", json={}).status_code == 200
    db.expire_all()
    org = db.get(AlbumChapter, chapter.id).org
    assert (org.name, org.description, org.theme_color, org.logo_url) == ("Nome novo", "Quem somos", "#aa3300", logo["url"])

    assert admin_client.post(f"/api/admin/revisions/{first}/rollback").status_code == 200
    db.expire_all()
    org = db.get(AlbumChapter, chapter.id).org
    assert (org.name, org.description, org.theme_color, org.logo_url) == before


def test_identidade_da_org_valida_cor_logo_e_conteudo_antigo(db, chapter, editor, admin):
    _, client = editor
    draft = draft_of(client, chapter)["content"]
    for bad in (
        {"name": "", "description": "", "color": "#112233", "logo_asset_id": None},
        {"name": "X", "description": "", "color": "vermelho", "logo_asset_id": None},
        {"name": "X", "description": "", "color": "#112233", "logo_asset_id": 999999},
    ):
        assert save(client, chapter, {**draft, "org": bad}).status_code == 422
    # Rascunho sem `org` (formato antigo) continua válido e não mexe na org.
    old = {k: v for k, v in draft.items() if k != "org"}
    assert save(client, chapter, old).status_code == 200
