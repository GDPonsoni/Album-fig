"""Promotor (códigos com cota) e rastreio: pacote, código, exemplar, troca e edição."""
import pytest
from sqlalchemy import func, select

from app.models import AuditLog, PackGrant, PackType, RedeemCode, RoleGrant, Sticker, StickerCopy, StickerCopyEvent
from app.services import provenance
from app.services.core import now


@pytest.fixture
def admin(make_user, mfa_login):
    user = make_user("Super", admin=True)
    return user, mfa_login(user)


@pytest.fixture
def promoter(db, make_user, login, admin):
    user = make_user("Promo")
    _, admin_client = admin
    response = admin_client.post("/api/admin/promoters", json={"email": user.email})
    assert response.status_code == 201 and response.json()["role"] == "promoter" and response.json()["chapter_id"] is None
    return user, login(user)


def pack_id(db, album, slug="comum"):
    return db.scalar(select(PackType.id).where(PackType.album_id == album.id, PackType.slug == slug))


def test_promotor_gera_codigos_dentro_da_cota_e_tudo_fica_auditado(db, album, promoter, make_user, login, monkeypatch):
    user, client = promoter
    assert client.get("/api/me").json()["promoter"] is True
    overview = client.get("/api/promo/overview").json()
    assert overview["limit"] == 20 and overview["left"] == 20 and {p["slug"] for p in overview["pack_types"]} >= {"comum", "premium"}
    body = {"pack_type_id": pack_id(db, album, "premium"), "count": 15, "label": "feira"}  # pacote extra, não só o comum
    created = client.post("/api/promo/codes", json=body)
    assert created.status_code == 201 and len(created.json()["codes"]) == 15 and created.json()["left"] == 5
    assert client.post("/api/promo/codes", json={**body, "count": 6}).status_code == 429  # estoura a cota
    assert client.post("/api/promo/codes", json={**body, "count": 5}).status_code == 201
    rows = db.scalars(select(RedeemCode).where(RedeemCode.created_by == user.id)).all()
    assert len(rows) == 20 and all(r.label == "feira" for r in rows)
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "promo.codes_create", AuditLog.user_id == user.id)) == 2
    # Quem não é promotor não vê nada; pacote de outro álbum é recusado.
    other = login(make_user("Comum"))
    assert other.get("/api/promo/overview").status_code == 404
    assert other.post("/api/promo/codes", json=body).status_code == 404
    assert client.post("/api/promo/codes", json={**body, "pack_type_id": 999999, "count": 1}).status_code == 404


def test_revogar_promotor_vale_na_hora(db, promoter, admin):
    user, client = promoter
    _, admin_client = admin
    grant_id = db.scalar(select(RoleGrant.id).where(RoleGrant.user_id == user.id))
    assert admin_client.delete(f"/api/admin/roles/{grant_id}").status_code == 204
    assert client.get("/api/promo/overview").status_code == 404
    assert client.get("/api/me").json()["promoter"] is False


def test_conceder_promotor_exige_superadmin_com_2fa(db, make_user, login, promoter):
    comum = login(make_user("Outra"))
    assert comum.post("/api/admin/promoters", json={"email": "promo@example.com"}).status_code == 403
    assert promoter[1].post("/api/admin/promoters", json={"email": "outra@example.com"}).status_code == 403


def test_cadeia_completa_codigo_pacote_exemplar_e_troca(db, album, content, promoter, admin, make_user, login, give):
    promo_user, promo = promoter
    _, admin_client = admin
    code = promo.post("/api/promo/codes", json={"pack_type_id": pack_id(db, album), "count": 1, "label": "bar"}).json()["codes"][0]
    ana, bia = make_user("Ana"), make_user("Bia")
    a, b = login(ana), login(bia)
    grant = a.post("/api/codes/redeem", json={"code": code}).json()
    assert a.post(f"/api/packs/{grant['id']}/open").status_code == 200

    # Pacote: gerado pelo promotor, aberto pela Ana, com a origem do código.
    row = db.get(PackGrant, grant["id"])
    assert row.origin_user_id == promo_user.id and row.user_id == ana.id and row.opened_at is not None
    traced = admin_client.get(f"/api/admin/trace/code/{code}").json()
    assert traced["created_by"] == "Promo" and traced["redeemed_by"] == "Ana" and traced["pack"]["owner"] == "Ana"
    assert traced["pack"]["generated_by"] == "Promo" and traced["pack"]["source"] == "code"
    items = traced["pack"]["items"]
    assert len(items) == 6 and all(i["copy"]["origin"]["generated_by"] == "Promo" and i["copy"]["owner"] == "Ana" for i in items)
    assert all([e["event"] for e in i["copy"]["events"]] == ["drawn"] for i in items)

    # Troca: um exemplar duplicado muda de mãos, e a cadeia mostra de onde veio e por quem passou.
    sticker_id = items[0]["sticker_id"]
    ana_sticker = db.get(Sticker, sticker_id)
    copies_before = db.scalars(select(StickerCopy).where(StickerCopy.owner_id == ana.id, StickerCopy.sticker_id == sticker_id)).all()
    assert len(copies_before) >= 1
    # Garante uma repetida: segundo exemplar sorteado "no mesmo pacote" via inventário e cópia.
    from app.services.packs import add_to_inventory
    add_to_inventory(db, ana.id, sticker_id, 1)
    provenance.record_draw(db, ana.id, sticker_id, grant["id"], 99)
    db.commit()
    offered = ana_sticker
    other = next(c for c in content if c.id != sticker_id)
    give(bia, other, 2)
    token = a.post("/api/trades").json()["token"]
    assert b.post(f"/api/trades/{token}/join").status_code == 200
    assert a.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": offered.id, "quantity": 1}]}).status_code == 200
    assert b.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": other.id, "quantity": 1}]}).status_code == 200
    version = a.get(f"/api/trades/{token}").json()["offer_version"]
    assert a.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 200
    assert b.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 200
    moved = db.scalars(select(StickerCopy).where(StickerCopy.owner_id == bia.id, StickerCopy.sticker_id == sticker_id)).all()
    assert len(moved) == 1
    chain = provenance.copy_chain(db, moved[0], {})
    assert chain["owner"] == "Bia" and chain["origin"]["generated_by"] == "Promo"
    assert [(e["event"], e["from"], e["to"]) for e in chain["events"]][-1] == ("traded", "Ana", "Bia")
    traced = admin_client.get(f"/api/admin/trace/sticker/{sticker_id}").json()
    assert traced["copy_total"] >= 2 and any(c["owner"] == "Bia" for c in traced["copies"])
    by_user = admin_client.get("/api/admin/trace/user", params={"email": "bia@example.com"}).json()
    assert by_user["user"]["nickname"] == "Bia"
    promoted = admin_client.get("/api/admin/trace/user", params={"email": promo_user.email}).json()
    assert len(promoted["packs_generated_by_user"]) == 1


def test_exemplares_anteriores_ao_rastreio_viram_legacy_na_troca(db, content, make_user, login, give):
    ana, bia = make_user("Ana"), make_user("Bia")
    give(ana, content[0], 3)  # inventário sem exemplares registrados
    a, b = login(ana), login(bia)
    token = a.post("/api/trades").json()["token"]
    b.post(f"/api/trades/{token}/join")
    give(bia, content[1], 2)
    a.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[0].id, "quantity": 2}]})
    b.put(f"/api/trades/{token}/offer", json={"items": [{"sticker_id": content[1].id, "quantity": 1}]})
    version = a.get(f"/api/trades/{token}").json()["offer_version"]
    assert a.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 200
    assert b.post(f"/api/trades/{token}/accept", json={"version": version}).status_code == 200
    counts = dict(db.execute(select(StickerCopy.owner_id, func.count()).group_by(StickerCopy.owner_id)).all())
    assert counts == {ana.id: 2, bia.id: 3}  # Ana: 1 que ficou + 1 recebida; Bia: 1 que ficou + 2 recebidas
    events = db.scalars(select(StickerCopyEvent.event)).all()
    assert events.count("legacy") == 5 and events.count("traded") == 3


def test_colar_registra_no_exemplar_mais_antigo(db, content, make_user, login, give):
    ana = make_user("Ana")
    give(ana, content[0], 2)
    a = login(ana)
    assert a.post(f"/api/album/paste/{content[0].id}").status_code == 200
    copies = db.scalars(select(StickerCopy).where(StickerCopy.owner_id == ana.id).order_by(StickerCopy.id)).all()
    assert len(copies) == 2
    pasted = db.scalars(select(StickerCopyEvent).where(StickerCopyEvent.event == "pasted")).all()
    assert [e.copy_id for e in pasted] == [copies[0].id]  # o mais antigo é o colado


def test_superadmin_usa_a_area_do_promotor_sem_cota(db, album, admin):
    user, client = admin
    assert client.get("/api/me").json()["promoter"] is True
    body = {"pack_type_id": pack_id(db, album), "count": 50, "label": "admin"}
    assert client.post("/api/promo/codes", json=body).status_code == 201
    assert client.post("/api/promo/codes", json=body).status_code == 201  # passa da cota de 20: admin não tem
    assert client.get("/api/promo/overview").json()["unlimited"] is True
