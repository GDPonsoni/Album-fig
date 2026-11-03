from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from PIL.Image import DecompressionBombError
from pydantic import AfterValidator, BaseModel, Field, HttpUrl
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.api.trades import hub
from app.db import get_db
from app.models import (
    Config,
    Org,
    PackGrant,
    PackType,
    Rarity,
    RedeemCode,
    Sticker,
    Trade,
    User,
    UserSticker,
)
from app.services import content, images
from app.services.codes import generate_code
from app.services.core import audit

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_user)])


def _media_url(value: str | None) -> str | None:
    if value and not value.startswith(("/media/", "https://")):
        raise ValueError("A imagem precisa ser um envio ou uma URL https.")
    return value


MediaUrl = Annotated[str | None, AfterValidator(_media_url)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


def _rows(rows: Any) -> list[dict]:
    return [{c.key: getattr(row, c.key) for c in row.__table__.columns} for row in rows]


def _row(row: Any) -> dict:
    return _rows([row])[0]


def _get(db: Session, model: Any, row_id: int) -> Any:
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(404, "Registro não encontrado.")
    return row


# ---- Orgs ----
class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    logo_url: MediaUrl = None
    theme_color: Color = "#1d4ed8"
    sort_order: int = 0
    mosaic_image_url: MediaUrl = None


@router.get("/orgs")
def list_orgs(db: Session = Depends(get_db)):
    return _rows(db.scalars(select(Org).order_by(Org.sort_order, Org.id)))


@router.post("/orgs", status_code=201)
def create_org(body: OrgIn, db: Session = Depends(get_db)):
    org = Org(**body.model_dump())
    db.add(org)
    db.commit()
    return _row(org)


@router.put("/orgs/{org_id}")
def update_org(org_id: int, body: OrgIn, db: Session = Depends(get_db)):
    org = _get(db, Org, org_id)
    for key, value in body.model_dump().items():
        setattr(org, key, value)
    db.commit()
    return _row(org)


@router.delete("/orgs/{org_id}", status_code=204)
def delete_org(org_id: int, db: Session = Depends(get_db)):
    org = _get(db, Org, org_id)
    if db.scalar(select(func.count()).select_from(Sticker).where(Sticker.org_id == org_id)):
        raise HTTPException(409, "Remova as figurinhas da org antes de apagá-la.")
    db.delete(org)
    db.commit()


class MosaicIn(BaseModel):
    cols: int = Field(ge=1, le=10)
    rows: int = Field(ge=1, le=10)
    rarity_id: int


@router.post("/orgs/{org_id}/mosaic")
def build_mosaic(org_id: int, body: MosaicIn, db: Session = Depends(get_db)):
    org = _get(db, Org, org_id)
    _get(db, Rarity, body.rarity_id)
    try:
        pieces = content.generate_mosaic(db, org, body.cols, body.rows, body.rarity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"pieces": len(pieces)}


# ---- Figurinhas ----
class BackLink(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    url: HttpUrl  # só http/https: o link vira href no verso


class SlotLayout(BaseModel):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    w: float = Field(gt=0, le=100)
    h: float = Field(gt=0, le=100)


class StickerIn(BaseModel):
    org_id: int
    number: int | None = None
    name: str = Field(min_length=1, max_length=120)
    image_url: MediaUrl = None
    size: Literal["P", "M", "G"] = "P"
    rarity_id: int
    tag: str = Field(default="membro", max_length=40)
    hint: str = Field(default="", max_length=160)
    back_text: str = ""
    back_links: list[BackLink] = []
    page_no: int = Field(default=1, ge=0)
    slot_layout: SlotLayout | None = None  # peças de mosaico não têm espaço próprio
    active: bool = True

    def values(self) -> dict:
        data = self.model_dump(mode="json")
        data["slot_layout"] = data["slot_layout"] or {}
        return {k: v for k, v in data.items() if not (k == "number" and v is None)}

    def require_slot(self) -> None:
        if self.page_no < 1 or self.slot_layout is None:
            raise HTTPException(400, "Informe a página (a partir de 1) e a posição do espaço.")


@router.get("/stickers")
def list_stickers(org_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Sticker).order_by(Sticker.number)
    if org_id is not None:
        query = query.where(Sticker.org_id == org_id)
    return _rows(db.scalars(query))


@router.post("/stickers", status_code=201)
def create_sticker(body: StickerIn, db: Session = Depends(get_db)):
    body.require_slot()
    sticker = Sticker(**body.values())
    if sticker.number is None:
        sticker.number = content.next_number(db)
    db.add(sticker)
    db.commit()
    return _row(sticker)


@router.put("/stickers/{sticker_id}")
def update_sticker(sticker_id: int, body: StickerIn, db: Session = Depends(get_db)):
    sticker = _get(db, Sticker, sticker_id)
    if not sticker.is_mosaic_piece:
        body.require_slot()
    old_image = sticker.image_url
    for key, value in body.values().items():
        setattr(sticker, key, value)
    db.commit()
    if old_image != sticker.image_url:
        images.remove_media(old_image)
    return _row(sticker)


@router.delete("/stickers/{sticker_id}", status_code=204)
def delete_sticker(sticker_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    affected = content.delete_sticker(db, _get(db, Sticker, sticker_id))
    audit(db, admin.id, "admin.sticker_delete", sticker_id=sticker_id)
    db.commit()
    for trade_id in affected:
        hub.publish(trade_id)


# ---- Raridades ----
class RarityIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    rank: int
    weight: float = Field(ge=0)
    color: Color


@router.get("/rarities")
def list_rarities(db: Session = Depends(get_db)):
    return _rows(db.scalars(select(Rarity).order_by(Rarity.rank)))


@router.post("/rarities", status_code=201)
def create_rarity(body: RarityIn, db: Session = Depends(get_db)):
    rarity = Rarity(**body.model_dump())
    db.add(rarity)
    db.commit()
    return _row(rarity)


@router.put("/rarities/{rarity_id}")
def update_rarity(rarity_id: int, body: RarityIn, db: Session = Depends(get_db)):
    rarity = _get(db, Rarity, rarity_id)
    for key, value in body.model_dump().items():
        setattr(rarity, key, value)
    db.commit()
    return _row(rarity)


# ---- Tipos de pacote ----
class PackTypeIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    stickers_per_pack: int = Field(ge=1, le=30)
    rarity_weights: dict[str, Annotated[float, Field(ge=0)]] = {}
    guaranteed_min_rarity: int | None = None
    org_filter: int | None = None
    config: dict[str, Any] = {}


@router.get("/pack-types")
def list_pack_types(db: Session = Depends(get_db)):
    return _rows(db.scalars(select(PackType).order_by(PackType.id)))


@router.post("/pack-types", status_code=201)
def create_pack_type(body: PackTypeIn, db: Session = Depends(get_db)):
    pack = PackType(**body.model_dump())
    db.add(pack)
    db.commit()
    return _row(pack)


@router.put("/pack-types/{pack_id}")
def update_pack_type(pack_id: int, body: PackTypeIn, db: Session = Depends(get_db)):
    pack = _get(db, PackType, pack_id)
    for key, value in body.model_dump().items():
        setattr(pack, key, value)
    db.commit()
    return _row(pack)


@router.delete("/pack-types/{pack_id}", status_code=204)
def delete_pack_type(pack_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, PackType, pack_id))
    db.commit()


# ---- Códigos ----
class CodeBatchIn(BaseModel):
    pack_type_id: int
    label: str = Field(default="", max_length=80)
    count: int = Field(ge=1, le=1000)
    expires_at: datetime | None = None


@router.post("/codes", status_code=201)
def create_codes(body: CodeBatchIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    _get(db, PackType, body.pack_type_id)
    codes = [generate_code() for _ in range(body.count)]
    db.add_all(
        RedeemCode(code=code, pack_type_id=body.pack_type_id, label=body.label, expires_at=body.expires_at)
        for code in codes
    )
    audit(db, admin.id, "admin.codes_create", count=body.count, label=body.label, pack_type_id=body.pack_type_id)
    db.commit()
    return {"codes": codes}


@router.get("/codes")
def list_codes(db: Session = Depends(get_db)):
    rows = db.execute(
        select(RedeemCode, User.nickname)
        .outerjoin(User, User.id == RedeemCode.redeemed_by)
        .order_by(RedeemCode.id.desc())
        .limit(1000)
    ).all()
    return [
        {
            "id": code.id,
            "code": code.code,
            "label": code.label,
            "pack_type": code.pack_type.name,
            "expires_at": code.expires_at,
            "redeemed_by": nickname,
            "redeemed_at": code.redeemed_at,
        }
        for code, nickname in rows
    ]


# ---- Configuração ----
class ConfigIn(BaseModel):
    value: Any


@router.get("/config")
def list_config(db: Session = Depends(get_db)):
    return _rows(db.scalars(select(Config).order_by(Config.key)))


@router.put("/config/{key}")
def update_config(key: str, body: ConfigIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    row = db.get(Config, key)
    if row is None:
        raise HTTPException(404, "Parâmetro desconhecido.")
    if type(row.value) is not type(body.value):
        raise HTTPException(400, "O valor precisa manter o mesmo tipo do parâmetro.")
    row.value = body.value
    audit(db, admin.id, "admin.config_update", key=key, value=body.value)
    db.commit()
    return _row(row)


# ---- Upload ----
@router.post("/upload")
async def upload(file: UploadFile, name: Annotated[str, Form(max_length=300)] = "uploads/imagem"):
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Imagem maior que 10 MB.")
    try:
        image = images.open_upload(data)
    except (ValueError, OSError, DecompressionBombError) as exc:
        raise HTTPException(400, "Envie uma imagem PNG, WebP ou JPEG válida.") from exc
    return {"url": images.save_webp(image, name)}


# ---- Métricas ----
@router.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    players = select(User.id).where(User.is_admin.is_(False), User.email_verified_at.is_not(None))
    total_stickers = db.scalar(select(func.count()).select_from(Sticker).where(Sticker.active.is_(True))) or 0
    verified = db.scalar(select(func.count()).select_from(players.subquery())) or 0
    pasted_count = func.count(case((UserSticker.pasted.is_(True), 1)))
    ranking_rows = db.execute(
        select(User.nickname, pasted_count.label("pasted"))
        .join(UserSticker, UserSticker.user_id == User.id)
        .join(Sticker, Sticker.id == UserSticker.sticker_id)
        .where(User.id.in_(players), Sticker.active.is_(True))
        .group_by(User.id)
        .order_by(pasted_count.desc(), User.nickname)
    ).all()
    pasted_total = sum(row.pasted for row in ranking_rows)
    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "verified_users": verified,
        "packs_opened": db.scalar(select(func.count()).select_from(PackGrant).where(PackGrant.opened_at.is_not(None))),
        "trades_done": db.scalar(select(func.count()).select_from(Trade).where(Trade.state == "done")),
        "avg_album_percent": round(100 * pasted_total / (verified * total_stickers), 1)
        if verified and total_stickers
        else 0,
        "ranking": [
            {
                "nickname": row.nickname,
                "pasted": row.pasted,
                "percent": round(100 * row.pasted / total_stickers, 1) if total_stickers else 0,
            }
            for row in ranking_rows[:20]
        ],
    }
