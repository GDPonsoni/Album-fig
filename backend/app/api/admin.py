from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from PIL.Image import DecompressionBombError
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.db import get_db
from app.models import (
    Album,
    AlbumChapter,
    AlbumConfig,
    AlbumParticipation,
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
from app.services import albums, content, images
from app.services.codes import generate_code
from app.services.core import ALBUM_KEYS, audit, cfg, now
from app.services.estimate import EstimateParams, PackSpec, estimate_album

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_user)])


def _media_url(value: str | None) -> str | None:
    if value and not value.startswith(("/media/", "https://")):
        raise ValueError("A imagem precisa ser um envio ou uma URL https.")
    return value


MediaUrl = Annotated[str | None, AfterValidator(_media_url)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
OVERLAP = "Já existe um álbum pronto nesse período para esta universidade. Ajuste as datas ou deixe um deles como rascunho."


def _rows(rows: Any) -> list[dict]:
    return [{c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs} for row in rows]


def _row(row: Any) -> dict:
    return _rows([row])[0]


def _get(db: Session, model: Any, row_id: int) -> Any:
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(404, "Registro não encontrado.")
    return row


def _working_album(db: Session, admin: User, album_id: int | None) -> Album:
    """O álbum em que o painel está trabalhando: o pedido, ou o em andamento, ou o mais recente."""
    if album_id is not None:
        return _get(db, Album, album_id)
    album = albums.current_album(db, admin) or db.scalar(
        select(Album).where(Album.university_id == admin.university_id).order_by(Album.starts_at.desc()).limit(1)
    )
    if album is None:
        raise HTTPException(409, "Crie um álbum antes de cadastrar conteúdo.")
    return album


@contextmanager
def _album_errors(db: Session) -> Iterator[None]:
    """Traduz as recusas do banco (dois álbuns prontos no mesmo período, fim antes do início)
    para mensagens que dão para agir. Desfaz a transação inteira: nada fica pela metade."""
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "ex_album_ready_overlap" in str(exc.orig):
            raise HTTPException(409, OVERLAP) from exc
        if "ck_album_dates" in str(exc.orig):
            raise HTTPException(400, "O fim do álbum precisa ser depois do início.") from exc
        raise


# ---- Álbuns ----
class AlbumIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    cover_url: MediaUrl = None
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    state: Literal["draft", "ready"] = "draft"

    @model_validator(mode="after")
    def _dates(self) -> "AlbumIn":
        if self.ends_at <= self.starts_at:
            raise ValueError("O fim do álbum precisa ser depois do início.")
        return self


class DuplicateIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def _dates(self) -> "DuplicateIn":
        if self.ends_at <= self.starts_at:
            raise ValueError("O fim do álbum precisa ser depois do início.")
        return self


def _album_row(db: Session, album: Album) -> dict:
    count = lambda model, column: db.scalar(select(func.count()).select_from(model).where(column == album.id)) or 0  # noqa: E731
    return {
        **_row(album),
        "status": albums.album_status(album),
        "chapters": count(AlbumChapter, AlbumChapter.album_id),
        "stickers": count(Sticker, Sticker.album_id),
        "participants": count(AlbumParticipation, AlbumParticipation.album_id),
    }


@router.get("/albums")
def list_albums(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Album).where(Album.university_id == admin.university_id).order_by(Album.starts_at.desc(), Album.id.desc()))
    return [_album_row(db, album) for album in rows]


@router.post("/albums", status_code=201)
def create_album(body: AlbumIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    with _album_errors(db):
        album = albums.create_album(db, admin.university_id, body.title, body.starts_at, body.ends_at, body.cover_url, body.state)
        audit(db, admin.id, "admin.album_create", album_id=album.id, title=album.title, state=album.state)
    return _album_row(db, album)


@router.put("/albums/{album_id}")
def update_album(album_id: int, body: AlbumIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    album = _get(db, Album, album_id)
    status = albums.album_status(album)
    if status == "ended":
        raise HTTPException(409, content.CLOSED)
    if status == "active":
        # Em andamento, o álbum não some da mão de quem está jogando nem muda de início.
        if body.state != "ready" or body.starts_at != album.starts_at:
            raise HTTPException(409, "Álbum em andamento: só título, capa e data de fim podem mudar.")
        if body.ends_at <= now():
            raise HTTPException(409, "Para encerrar antes, escolha uma data de fim no futuro.")
    old_cover = album.cover_url
    with _album_errors(db):
        for key, value in body.model_dump().items():
            setattr(album, key, value)
        audit(
            db, admin.id, "admin.album_update",
            album_id=album.id, state=album.state, starts_at=album.starts_at.isoformat(), ends_at=album.ends_at.isoformat(),
        )
    if old_cover != album.cover_url:
        content.remove_media_if_unused(db, old_cover)
    return _album_row(db, album)


@router.delete("/albums/{album_id}", status_code=204)
def delete_album(album_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Só apaga álbum que ninguém jogou: sem participantes, pacotes, códigos ou trocas."""
    album = _get(db, Album, album_id)
    played = any(
        db.scalar(select(func.count()).select_from(model).where(model.album_id == album.id))
        for model in (AlbumParticipation, PackGrant, RedeemCode, Trade)
    )
    if played or albums.album_status(album) in ("active", "ended"):
        raise HTTPException(409, "Este álbum já teve jogadores ou já começou e não pode ser apagado.")
    for sticker in db.scalars(select(Sticker).where(Sticker.album_id == album.id)).all():
        content.delete_sticker(db, sticker)
    mosaics = list(db.scalars(select(AlbumChapter.mosaic_image_url).where(AlbumChapter.album_id == album.id)))
    cover = album.cover_url
    db.delete(album)  # capítulos, tipos de pacote e parâmetros caem em cascata
    audit(db, admin.id, "admin.album_delete", album_id=album_id)
    db.commit()
    for url in [cover, *mosaics]:
        content.remove_media_if_unused(db, url)


@router.post("/albums/{album_id}/duplicate", status_code=201)
def duplicate_album(album_id: int, body: DuplicateIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    source = _get(db, Album, album_id)
    with _album_errors(db):
        album = albums.duplicate_album(db, source, body.title, body.starts_at, body.ends_at)
        audit(db, admin.id, "admin.album_duplicate", album_id=album.id, source_album_id=source.id)
    return _album_row(db, album)


# ---- Capítulos (a org dentro de um álbum) ----
class ChapterIn(BaseModel):
    org_id: int | None = None  # org existente...
    org: "OrgIn | None" = None  # ...ou uma nova, criada junto
    sort_order: int = 0

    @model_validator(mode="after")
    def _one(self) -> "ChapterIn":
        if (self.org_id is None) == (self.org is None):
            raise ValueError("Informe uma org existente ou os dados de uma nova.")
        return self


class ChapterUpdate(BaseModel):
    sort_order: int = 0
    mosaic_image_url: MediaUrl = None


def _chapter_row(db: Session, chapter: AlbumChapter) -> dict:
    stickers = db.scalar(
        select(func.count()).select_from(Sticker).where(Sticker.album_id == chapter.album_id, Sticker.org_id == chapter.org_id)
    )
    return {**_row(chapter), "org_name": chapter.org.name, "org_color": chapter.org.theme_color, "stickers": stickers or 0}


def _chapter(db: Session, album_id: int, chapter_id: int) -> AlbumChapter:
    chapter = db.get(AlbumChapter, chapter_id)
    if chapter is None or chapter.album_id != album_id:
        raise HTTPException(404, "Registro não encontrado.")
    return chapter


@router.get("/albums/{album_id}/chapters")
def list_chapters(album_id: int, db: Session = Depends(get_db)):
    _get(db, Album, album_id)
    rows = db.scalars(
        select(AlbumChapter).where(AlbumChapter.album_id == album_id).order_by(AlbumChapter.sort_order, AlbumChapter.id)
    )
    return [_chapter_row(db, chapter) for chapter in rows]


@router.post("/albums/{album_id}/chapters", status_code=201)
def create_chapter(album_id: int, body: ChapterIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    album = _get(db, Album, album_id)
    content.require_editable(album, structural=True)
    if body.org is not None:
        org = Org(**body.org.model_dump(), university_id=album.university_id)
        db.add(org)
        db.flush()
    else:
        org = _get(db, Org, body.org_id)
        if org.university_id != album.university_id:
            raise HTTPException(404, "Registro não encontrado.")
    if db.scalar(select(AlbumChapter.id).where(AlbumChapter.album_id == album.id, AlbumChapter.org_id == org.id)):
        raise HTTPException(409, "Esta org já tem capítulo neste álbum.")
    chapter = AlbumChapter(album_id=album.id, org_id=org.id, sort_order=body.sort_order)
    db.add(chapter)
    db.flush()
    audit(db, admin.id, "admin.chapter_create", album_id=album.id, chapter_id=chapter.id, org_id=org.id)
    db.commit()
    return _chapter_row(db, chapter)


@router.put("/albums/{album_id}/chapters/{chapter_id}")
def update_chapter(album_id: int, chapter_id: int, body: ChapterUpdate, db: Session = Depends(get_db)):
    chapter = _chapter(db, album_id, chapter_id)
    content.require_editable(_get(db, Album, album_id))
    old_image = chapter.mosaic_image_url
    chapter.sort_order, chapter.mosaic_image_url = body.sort_order, body.mosaic_image_url
    db.commit()
    if old_image != chapter.mosaic_image_url:
        content.remove_media_if_unused(db, old_image)
    return _chapter_row(db, chapter)


@router.delete("/albums/{album_id}/chapters/{chapter_id}", status_code=204)
def delete_chapter(album_id: int, chapter_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    chapter = _chapter(db, album_id, chapter_id)
    content.require_editable(_get(db, Album, album_id), structural=True)
    if db.scalar(
        select(func.count()).select_from(Sticker).where(Sticker.album_id == album_id, Sticker.org_id == chapter.org_id)
    ):
        raise HTTPException(409, "Remova as figurinhas do capítulo antes de apagá-lo.")
    image = chapter.mosaic_image_url
    db.delete(chapter)
    audit(db, admin.id, "admin.chapter_delete", album_id=album_id, chapter_id=chapter_id)
    db.commit()
    content.remove_media_if_unused(db, image)


class MosaicIn(BaseModel):
    cols: int = Field(ge=1, le=10)
    rows: int = Field(ge=1, le=10)
    rarity_id: int


@router.post("/albums/{album_id}/chapters/{chapter_id}/mosaic")
def build_mosaic(album_id: int, chapter_id: int, body: MosaicIn, db: Session = Depends(get_db)):
    chapter = _chapter(db, album_id, chapter_id)
    content.require_editable(_get(db, Album, album_id), structural=True)  # gerar o mosaico cria e remove figurinhas
    _get(db, Rarity, body.rarity_id)
    try:
        pieces = content.generate_mosaic(db, chapter, body.cols, body.rows, body.rarity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"pieces": len(pieces)}


# ---- Parâmetros do álbum ----
class ConfigIn(BaseModel):
    value: Any


@router.get("/albums/{album_id}/config")
def album_config(album_id: int, db: Session = Depends(get_db)):
    _get(db, Album, album_id)
    overrides = {row.key: row.value for row in db.scalars(select(AlbumConfig).where(AlbumConfig.album_id == album_id))}
    defaults = db.scalars(select(Config).where(Config.key.in_(ALBUM_KEYS)).order_by(Config.key))
    return [
        {"key": row.key, "default": row.value, "value": overrides.get(row.key), "effective": overrides.get(row.key, row.value)}
        for row in defaults
    ]


@router.put("/albums/{album_id}/config/{key}")
def update_album_config(
    album_id: int, key: str, body: ConfigIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)
):
    """`value: null` remove a sobrescrita e o álbum volta a usar o padrão global."""
    album = _get(db, Album, album_id)
    content.require_editable(album)
    default = db.get(Config, key)
    if key not in ALBUM_KEYS or default is None:
        raise HTTPException(404, "Parâmetro desconhecido ou que não pode variar por álbum.")
    row = db.get(AlbumConfig, (album_id, key))
    if body.value is None:
        if row is not None:
            db.delete(row)
    else:
        if type(default.value) is not type(body.value):
            raise HTTPException(400, "O valor precisa manter o mesmo tipo do parâmetro.")
        if isinstance(body.value, int) and body.value < 0:
            raise HTTPException(400, "O valor não pode ser negativo.")
        if row is None:
            db.add(AlbumConfig(album_id=album_id, key=key, value=body.value))
        else:
            row.value = body.value
    audit(db, admin.id, "admin.album_config_update", album_id=album_id, key=key, value=body.value)
    db.commit()
    return {"key": key, "default": default.value, "value": body.value, "effective": default.value if body.value is None else body.value}


# ---- Estimador ----
class EstimateIn(BaseModel):
    """Tudo opcional além do álbum: o que não vier usa o que o álbum tem hoje."""

    album_id: int
    duration_days: int | None = Field(default=None, ge=1, le=730)
    refill_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)
    stock_cap: int | None = Field(default=None, ge=0, le=100)
    welcome_packs: int | None = Field(default=None, ge=0, le=100)
    premium_every_n_days: int | None = Field(default=None, ge=0, le=365)
    open_fraction: float = Field(default=1.0, ge=0, le=1)
    visits_per_day: float = Field(default=1.0, gt=0, le=48)
    generous_threshold: float = Field(default=0.95, gt=0, le=1)
    tight_threshold: float = Field(default=0.50, ge=0, lt=1)


@router.post("/albums/estimate")
def estimate(body: EstimateIn, db: Session = Depends(get_db)):
    album = _get(db, Album, body.album_id)
    rarities = db.scalars(select(Rarity).order_by(Rarity.rank)).all()
    counts = dict(
        db.execute(
            select(Sticker.rarity_id, func.count())
            .where(Sticker.album_id == album.id, Sticker.active.is_(True))
            .group_by(Sticker.rarity_id)
        ).all()
    )

    def spec(slug_key: str) -> tuple[PackType | None, PackSpec | None]:
        pack = db.scalar(select(PackType).where(PackType.album_id == album.id, PackType.slug == cfg(db, slug_key, album.id)))
        if pack is None:
            return None, None
        weights = {str(r.id): float(pack.rarity_weights.get(str(r.id), r.weight)) for r in rarities}
        return pack, PackSpec(pack.stickers_per_pack, weights)

    common_pack, common = spec("daily.common_pack_slug")
    premium_pack, premium = spec("daily.premium_pack_slug")
    if common is None:
        raise HTTPException(409, "O álbum não tem o tipo de pacote comum configurado.")

    def pick(value: int | None, key: str) -> int:
        return int(cfg(db, key, album.id)) if value is None else value

    inputs = {
        "duration_days": body.duration_days or max(1, round((album.ends_at - album.starts_at).total_seconds() / 86400)),
        "refill_minutes": pick(body.refill_minutes, "daily.refill_minutes"),
        "stock_cap": pick(body.stock_cap, "daily.stock_cap"),
        "welcome_packs": pick(body.welcome_packs, "daily.welcome_packs"),
        "premium_every_n_days": pick(body.premium_every_n_days, "daily.premium_every_n_days"),
        "open_fraction": body.open_fraction,
        "visits_per_day": body.visits_per_day,
        "generous_threshold": body.generous_threshold,
        "tight_threshold": body.tight_threshold,
    }
    params = EstimateParams(
        stickers_by_rarity={str(r.id): counts.get(r.id, 0) for r in rarities},
        common=common,
        premium=premium if premium_pack is not None and premium_pack.id != common_pack.id else None,
        **inputs,
    )
    result = estimate_album(params)
    names = {str(r.id): r.name for r in rarities}
    for alert in result["alerts"]:
        if alert["code"] == "unreachable":
            for rarity_id, name in names.items():
                alert["message"] = alert["message"].replace(f" {rarity_id}", f" {name}")
    return {
        **result,
        "inputs": inputs,
        "stickers_by_rarity": [{"rarity": r.name, "count": counts.get(r.id, 0)} for r in rarities],
        "packs": {
            "common": {"name": common_pack.name, "stickers_per_pack": common_pack.stickers_per_pack},
            "premium": {"name": premium_pack.name, "stickers_per_pack": premium_pack.stickers_per_pack} if params.premium else None,
        },
    }


# ---- Orgs ----
class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    logo_url: MediaUrl = None
    theme_color: Color = "#1d4ed8"


ChapterIn.model_rebuild()


@router.get("/orgs")
def list_orgs(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return _rows(db.scalars(select(Org).where(Org.university_id == admin.university_id).order_by(Org.name, Org.id)))


@router.post("/orgs", status_code=201)
def create_org(body: OrgIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    org = Org(**body.model_dump(), university_id=admin.university_id)
    db.add(org)
    db.commit()
    return _row(org)


@router.put("/orgs/{org_id}")
def update_org(org_id: int, body: OrgIn, db: Session = Depends(get_db)):
    org = _get(db, Org, org_id)
    old_logo = org.logo_url
    for key, value in body.model_dump().items():
        setattr(org, key, value)
    db.commit()
    if old_logo != org.logo_url:
        content.remove_media_if_unused(db, old_logo)
    return _row(org)


@router.delete("/orgs/{org_id}", status_code=204)
def delete_org(org_id: int, db: Session = Depends(get_db)):
    org = _get(db, Org, org_id)
    if db.scalar(select(func.count()).select_from(Sticker).where(Sticker.org_id == org_id)) or db.scalar(
        select(func.count()).select_from(AlbumChapter).where(AlbumChapter.org_id == org_id)
    ):
        raise HTTPException(409, "Esta org tem capítulo ou figurinhas em algum álbum e não pode ser apagada.")
    db.delete(org)
    db.commit()


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
    album_id: int | None = None  # ausente = álbum em que o painel está trabalhando
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
        data = self.model_dump(mode="json", exclude={"album_id"})
        data["slot_layout"] = data["slot_layout"] or {}
        return {k: v for k, v in data.items() if not (k == "number" and v is None)}

    def require_slot(self) -> None:
        if self.page_no < 1 or self.slot_layout is None:
            raise HTTPException(400, "Informe a página (a partir de 1) e a posição do espaço.")


# O que define a estrutura do álbum; com ele em andamento, nada disto muda.
STRUCTURAL = ("org_id", "number", "rarity_id", "active")


def _require_chapter(db: Session, album: Album, org_id: int) -> None:
    if db.scalar(select(AlbumChapter.id).where(AlbumChapter.album_id == album.id, AlbumChapter.org_id == org_id)) is None:
        raise HTTPException(400, "Esta org não tem capítulo neste álbum. Adicione o capítulo antes.")


@router.get("/stickers")
def list_stickers(
    album_id: int | None = None, org_id: int | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)
):
    query = select(Sticker).where(Sticker.album_id == _working_album(db, admin, album_id).id).order_by(Sticker.number)
    if org_id is not None:
        query = query.where(Sticker.org_id == org_id)
    return _rows(db.scalars(query))


@router.post("/stickers", status_code=201)
def create_sticker(body: StickerIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    album = _working_album(db, admin, body.album_id)
    content.require_editable(album, structural=True)
    body.require_slot()
    _require_chapter(db, album, body.org_id)
    sticker = Sticker(**body.values(), album_id=album.id)
    if sticker.number is None:
        sticker.number = content.next_number(db, album.id)
    db.add(sticker)
    db.commit()
    return _row(sticker)


@router.put("/stickers/{sticker_id}")
def update_sticker(sticker_id: int, body: StickerIn, db: Session = Depends(get_db)):
    sticker = _get(db, Sticker, sticker_id)
    album = _get(db, Album, sticker.album_id)
    values = body.values()
    structural = any(key in values and values[key] != getattr(sticker, key) for key in STRUCTURAL)
    content.require_editable(album, structural=structural)
    if not sticker.is_mosaic_piece:
        body.require_slot()
    if values["org_id"] != sticker.org_id:
        _require_chapter(db, album, values["org_id"])
    old_image = sticker.image_url
    for key, value in values.items():
        setattr(sticker, key, value)
    db.commit()
    if old_image != sticker.image_url:
        content.remove_media_if_unused(db, old_image)
    return _row(sticker)


@router.delete("/stickers/{sticker_id}", status_code=204)
def delete_sticker(sticker_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    sticker = _get(db, Sticker, sticker_id)
    content.require_editable(_get(db, Album, sticker.album_id), structural=True)
    content.delete_sticker(db, sticker)
    audit(db, admin.id, "admin.sticker_delete", sticker_id=sticker_id, album_id=sticker.album_id)
    db.commit()


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
    album_id: int | None = None  # nulo = modelo global, copiado para os álbuns novos
    slug: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    stickers_per_pack: int = Field(ge=1, le=30)
    rarity_weights: dict[str, Annotated[float, Field(ge=0)]] = {}
    guaranteed_min_rarity: int | None = None
    org_filter: int | None = None
    config: dict[str, Any] = {}


def _pack_values(db: Session, body: PackTypeIn) -> dict:
    values = body.model_dump()
    if body.album_id is not None:
        content.require_editable(_get(db, Album, body.album_id))
        # No álbum os pesos ficam sempre completos, para não depender do peso global da raridade.
        values["rarity_weights"] = albums.explicit_weights(db, body.rarity_weights)
    return values


@router.get("/pack-types")
def list_pack_types(album_id: int | None = None, templates: bool = False, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(PackType).order_by(PackType.id)
    if templates:
        query = query.where(PackType.album_id.is_(None))
    else:
        query = query.where(PackType.album_id == _working_album(db, admin, album_id).id)
    return _rows(db.scalars(query))


@router.post("/pack-types", status_code=201)
def create_pack_type(body: PackTypeIn, db: Session = Depends(get_db)):
    pack = PackType(**_pack_values(db, body))
    db.add(pack)
    db.commit()
    return _row(pack)


@router.put("/pack-types/{pack_id}")
def update_pack_type(pack_id: int, body: PackTypeIn, db: Session = Depends(get_db)):
    pack = _get(db, PackType, pack_id)
    if body.album_id != pack.album_id:
        raise HTTPException(400, "Um tipo de pacote não muda de álbum.")
    for key, value in _pack_values(db, body).items():
        setattr(pack, key, value)
    db.commit()
    return _row(pack)


@router.delete("/pack-types/{pack_id}", status_code=204)
def delete_pack_type(pack_id: int, db: Session = Depends(get_db)):
    pack = _get(db, PackType, pack_id)
    if pack.album_id is not None:
        content.require_editable(_get(db, Album, pack.album_id))
    db.delete(pack)
    db.commit()


# ---- Códigos ----
class CodeBatchIn(BaseModel):
    pack_type_id: int
    label: str = Field(default="", max_length=80)
    count: int = Field(ge=1, le=1000)
    expires_at: datetime | None = None


@router.post("/codes", status_code=201)
def create_codes(body: CodeBatchIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    pack = _get(db, PackType, body.pack_type_id)
    if pack.album_id is None:
        raise HTTPException(400, "Escolha um tipo de pacote de um álbum, não o modelo global.")
    content.require_editable(_get(db, Album, pack.album_id))
    codes = [generate_code() for _ in range(body.count)]
    db.add_all(
        RedeemCode(code=code, album_id=pack.album_id, pack_type_id=pack.id, label=body.label, expires_at=body.expires_at)
        for code in codes
    )
    audit(
        db, admin.id, "admin.codes_create",
        count=body.count, label=body.label, pack_type_id=body.pack_type_id, album_id=pack.album_id,
    )
    db.commit()
    return {"codes": codes}


@router.get("/codes")
def list_codes(album_id: int | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(RedeemCode, User.nickname)
        .outerjoin(User, User.id == RedeemCode.redeemed_by)
        .where(RedeemCode.album_id == _working_album(db, admin, album_id).id)
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
def metrics(album_id: int | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Números do álbum em que o painel está trabalhando. Sem álbum nenhum, tudo zero."""
    try:
        album = _working_album(db, admin, album_id)
    except HTTPException:
        album = None
    users = db.scalar(select(func.count()).select_from(User))
    if album is None:
        return {"album": None, "users": users, "verified_users": 0, "packs_opened": 0, "trades_done": 0, "avg_album_percent": 0, "ranking": []}
    players = (
        select(User.id)
        .join(AlbumParticipation, AlbumParticipation.user_id == User.id)
        .where(AlbumParticipation.album_id == album.id, User.is_admin.is_(False), User.email_verified_at.is_not(None))
    )
    total_stickers = db.scalar(
        select(func.count()).select_from(Sticker).where(Sticker.album_id == album.id, Sticker.active.is_(True))
    ) or 0
    verified = db.scalar(select(func.count()).select_from(players.subquery())) or 0
    pasted_count = func.count(case((UserSticker.pasted.is_(True), 1)))
    ranking_rows = db.execute(
        select(User.nickname, pasted_count.label("pasted"))
        .join(UserSticker, UserSticker.user_id == User.id)
        .join(Sticker, Sticker.id == UserSticker.sticker_id)
        .where(User.id.in_(players), Sticker.album_id == album.id, Sticker.active.is_(True))
        .group_by(User.id)
        .order_by(pasted_count.desc(), User.nickname)
    ).all()
    pasted_total = sum(row.pasted for row in ranking_rows)
    return {
        "album": albums.album_view(album),
        "users": users,
        "verified_users": verified,
        "packs_opened": db.scalar(
            select(func.count()).select_from(PackGrant).where(PackGrant.album_id == album.id, PackGrant.opened_at.is_not(None))
        ),
        "trades_done": db.scalar(
            select(func.count()).select_from(Trade).where(Trade.album_id == album.id, Trade.state == "done")
        ),
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
