"""Rascunho, revisão e publicação do conteúdo de um capítulo.

Princípio de segurança: o editor só escreve em `chapter_revision` e `asset`.
O `content` é um documento JSON validado; quem o leva para as tabelas de
produção (`sticker`, `album_chapter`) é só `publish`/`rollback`, chamados pelo
superadmin, numa transação.
"""
import re
import uuid
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Album, AlbumChapter, Asset, ChapterRevision, Rarity, Sticker, User
from app.services import content as content_service
from app.services.albums import album_status
from app.services.core import audit, now

OPEN = ("draft", "submitted", "approved", "rejected")
EDITABLE = ("draft", "rejected")
MAX_STICKERS = 200
MAX_PAGES = 30
COSMETIC = ("name", "image_url", "size", "tag", "hint", "back_text", "back_links", "page_no", "slot_layout")
FROZEN = "Com o álbum em andamento a estrutura fica congelada"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _clean(value: str) -> str:
    """Tira caracteres de controle e de direção de texto. O front mostra tudo como texto puro
    (nunca como HTML), então `<` e `>` não precisam ser escapados aqui."""
    return _CONTROL.sub("", value).strip()


def _line(value: str) -> str:
    return " ".join(_clean(value).split())


def _https(value: str) -> str:
    """Só link https com host de verdade: nada de javascript:, http:, credenciais ou IP solto."""
    value = value.strip()
    if len(value) > 300 or _CONTROL.search(value) or " " in value or "\n" in value:
        raise ValueError("Link inválido.")
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or not _HOST.match(host):
        raise ValueError("O link precisa começar com https:// e apontar para um site válido.")
    try:
        if parts.port not in (None, 443):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Link inválido.") from exc
    return value


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Identificador de figurinha inválido.") from exc


def _required(value: str) -> str:
    value = _line(value)
    if not value:
        raise ValueError("Não pode ficar em branco.")
    return value


Line = Annotated[str, AfterValidator(_line)]
Name = Annotated[str, AfterValidator(_required)]
Text = Annotated[str, AfterValidator(_clean)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # campo desconhecido (number, rarity_id, image_url...) é recusado


class SlotC(Strict):
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    w: float = Field(gt=0, le=100)
    h: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _inside(self) -> "SlotC":
        if self.x + self.w > 100.001 or self.y + self.h > 100.001:
            raise ValueError("O espaço da figurinha sai da página.")
        return self


class BackLinkC(Strict):
    label: Name = Field(max_length=40)
    url: Annotated[str, AfterValidator(_https)]


class LayoutC(Strict):
    orientation: Literal["portrait", "landscape"] = "portrait"


class PageC(Strict):
    page_no: int = Field(ge=1, le=MAX_PAGES)
    title: Line = Field(default="", max_length=80)
    text: Text = Field(default="", max_length=600)
    background_asset_id: int | None = None
    layout: str = Field(default="free", pattern=r"^(free|grid-[1-5]x[1-6])$")


class MosaicC(Strict):
    asset_id: int | None = None
    cols: int = Field(ge=1, le=10)
    rows: int = Field(ge=1, le=10)


class StickerC(Strict):
    key: Annotated[str, AfterValidator(_uuid)]
    name: Name = Field(max_length=120)
    asset_id: int | None = None
    size: Literal["P", "M", "G"] = "P"
    tag: Line = Field(default="membro", max_length=40)
    hint: Line = Field(default="", max_length=160)
    back_text: Text = Field(default="", max_length=2000)
    back_links: list[BackLinkC] = Field(default_factory=list, max_length=5)
    page_no: int = Field(ge=1, le=MAX_PAGES)
    slot: SlotC
    suggested_rarity_id: int | None = None
    active: bool = True


class OrgC(Strict):
    """Identidade da organização. Ausente no conteúdo = não mexer (revisões antigas não têm)."""

    name: Name = Field(max_length=120)
    description: Text = Field(default="", max_length=600)
    color: str = Field(default="#1d4ed8", pattern=r"^#[0-9a-fA-F]{6}$")
    logo_asset_id: int | None = None


class Content(Strict):
    layout: LayoutC = Field(default_factory=LayoutC)
    org: OrgC | None = None
    pages: list[PageC] = Field(min_length=1, max_length=MAX_PAGES)
    mosaic: MosaicC | None = None
    stickers: list[StickerC] = Field(default_factory=list, max_length=MAX_STICKERS)

    @model_validator(mode="after")
    def _consistent(self) -> "Content":
        pages = [page.page_no for page in self.pages]
        if len(set(pages)) != len(pages):
            raise ValueError("Há páginas com o mesmo número.")
        keys = [sticker.key for sticker in self.stickers]
        if len(set(keys)) != len(keys):
            raise ValueError("Há figurinhas com o mesmo identificador.")
        missing = {sticker.page_no for sticker in self.stickers} - set(pages)
        if missing:
            raise ValueError("Há figurinha em página que não existe.")
        return self


def parse_content(data: Any) -> Content:
    try:
        return Content.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        message = first["msg"].removeprefix("Value error, ")
        raise HTTPException(422, f"Conteúdo inválido em {where or 'conteúdo'}: {message}") from exc


# ---- ativos


def chapter_assets(db: Session, chapter_id: int) -> dict[int, Asset]:
    return {asset.id: asset for asset in db.scalars(select(Asset).where(Asset.chapter_id == chapter_id))}


def _asset_for(db: Session, chapter: AlbumChapter, url: str | None, kind: str) -> int | None:
    """Ativo (já aprovado) que representa uma imagem que já está em produção. Só escreve em `asset`."""
    if not url:
        return None
    asset = db.scalar(select(Asset).where(Asset.chapter_id == chapter.id, Asset.url == url).order_by(Asset.id).limit(1))
    if asset is None:
        asset = Asset(chapter_id=chapter.id, uploaded_by=None, key=url.removeprefix("/media/"), url=url, kind=kind, status="approved")
        db.add(asset)
        db.flush()
    return asset.id


def check_references(db: Session, chapter: AlbumChapter, content: Content) -> None:
    """Toda imagem precisa ser um ativo DESTE capítulo; raridade sugerida precisa existir."""
    assets = chapter_assets(db, chapter.id)
    wanted = {s.asset_id for s in content.stickers} | {p.background_asset_id for p in content.pages}
    if content.mosaic is not None:
        wanted.add(content.mosaic.asset_id)
    if content.org is not None:
        wanted.add(content.org.logo_asset_id)
    if any(asset_id not in assets for asset_id in wanted - {None}):
        raise HTTPException(422, "Conteúdo inválido: há imagem que não pertence a este capítulo.")
    rarities = set(db.scalars(select(Rarity.id)))
    if any(s.suggested_rarity_id not in rarities for s in content.stickers if s.suggested_rarity_id is not None):
        raise HTTPException(422, "Conteúdo inválido: raridade sugerida desconhecida.")


# ---- produção <-> conteúdo


def _production_stickers(db: Session, chapter: AlbumChapter, mosaic: bool = False) -> list[Sticker]:
    return list(
        db.scalars(
            select(Sticker)
            .where(Sticker.album_id == chapter.album_id, Sticker.org_id == chapter.org_id, Sticker.is_mosaic_piece.is_(mosaic))
            .order_by(Sticker.number)
        )
    )


def production_content(db: Session, chapter: AlbumChapter) -> dict:
    """O que está publicado hoje, no formato do editor. `meta` guarda número e raridade de cada
    figurinha: é o que o rollback usa para restaurar exatamente o que havia."""
    stickers = _production_stickers(db, chapter)
    layout = chapter.layout or {}
    stored = {page["page_no"]: page for page in layout.get("pages", [])}
    numbers = sorted({s.page_no for s in stickers} | set(stored)) or [1]
    pages = [
        {
            "page_no": page_no,
            "title": stored.get(page_no, {}).get("title", ""),
            "text": stored.get(page_no, {}).get("text", ""),
            "background_asset_id": _asset_for(db, chapter, stored.get(page_no, {}).get("background_url"), "background"),
            "layout": stored.get(page_no, {}).get("layout", "free"),
        }
        for page_no in numbers
    ]
    pieces = _production_stickers(db, chapter, mosaic=True)
    mosaic = None
    if chapter.mosaic_image_url or pieces:
        mosaic = {
            "asset_id": _asset_for(db, chapter, chapter.mosaic_image_url, "mosaic"),
            "cols": chapter.mosaic_cols,
            "rows": chapter.mosaic_rows,
        }
    org = chapter.org
    return {
        "content": {
            "layout": {"orientation": layout.get("orientation", "portrait")},
            "org": {
                "name": org.name, "description": org.description, "color": org.theme_color,
                "logo_asset_id": _asset_for(db, chapter, org.logo_url, "logo"),
            },
            "pages": pages,
            "mosaic": mosaic,
            "stickers": [
                {
                    "key": s.content_key,
                    "name": s.name,
                    "asset_id": _asset_for(db, chapter, s.image_url, "sticker"),
                    "size": s.size,
                    "tag": s.tag,
                    "hint": s.hint,
                    "back_text": s.back_text,
                    "back_links": list(s.back_links or []),
                    "page_no": s.page_no,
                    "slot": s.slot_layout or {"x": 5, "y": 15, "w": 28, "h": 27},
                    "suggested_rarity_id": s.rarity_id,
                    "active": s.active,
                }
                for s in stickers
            ],
        },
        "meta": {s.content_key: {"number": s.number, "rarity_id": s.rarity_id} for s in stickers},
        "mosaic_rarity_id": pieces[0].rarity_id if pieces else None,
    }


def structural_problems(db: Session, chapter: AlbumChapter, content: Content) -> list[str]:
    """O que o conteúdo mudaria na ESTRUTURA do capítulo (figurinha nova, removida, desativada,
    mosaico refeito). Com o álbum em andamento, qualquer item aqui barra a publicação."""
    existing = {s.content_key: s for s in _production_stickers(db, chapter)}
    wanted = {s.key: s for s in content.stickers}
    problems = []
    added = [wanted[key].name for key in wanted if key not in existing]
    removed = [existing[key].name for key in existing if key not in wanted]
    if added:
        problems.append("figurinha nova: " + ", ".join(added[:5]))
    if removed:
        problems.append("figurinha removida: " + ", ".join(removed[:5]))
    toggled = [wanted[key].name for key in wanted if key in existing and wanted[key].active != existing[key].active]
    if toggled:
        problems.append("figurinha ativada ou desativada: " + ", ".join(toggled[:5]))
    pieces = _production_stickers(db, chapter, mosaic=True)
    has_mosaic = content.mosaic is not None and content.mosaic.asset_id is not None
    if bool(pieces) != has_mosaic:
        problems.append("mosaico criado ou removido")
    elif has_mosaic and (content.mosaic.cols, content.mosaic.rows) != (chapter.mosaic_cols, chapter.mosaic_rows):
        problems.append("mosaico com outra grade")
    return problems


def _lowest_rarity(db: Session) -> int:
    return db.scalar(select(Rarity.id).order_by(Rarity.rank).limit(1))


def apply_content(
    db: Session,
    chapter: AlbumChapter,
    album: Album,
    content: Content,
    overrides: dict[str, dict[str, int]] | None = None,
    mosaic_rarity_id: int | None = None,
) -> None:
    """Leva um conteúdo aprovado para produção, respeitando o estado do álbum. Único ponto
    do sistema em que `content` vira linha de `sticker`/`album_chapter`."""
    status = album_status(album)
    if status == "ended":
        raise HTTPException(409, content_service.CLOSED)
    overrides = overrides or {}
    check_references(db, chapter, content)
    assets = chapter_assets(db, chapter.id)
    url = lambda asset_id: assets[asset_id].url if asset_id is not None else None  # noqa: E731
    existing = {s.content_key: s for s in _production_stickers(db, chapter)}
    rarities = set(db.scalars(select(Rarity.id)))
    unknown = set(overrides) - {s.key for s in content.stickers}
    if unknown:
        raise HTTPException(422, "Há ajuste de raridade ou número para figurinha que não está no conteúdo.")

    if status == "active":
        problems = structural_problems(db, chapter, content)
        for key, change in overrides.items():
            row = existing.get(key)
            if row is not None and (change.get("rarity_id", row.rarity_id) != row.rarity_id or change.get("number", row.number) != row.number):
                problems.append(f"raridade ou número de {row.name}")
        if problems:
            raise HTTPException(409, f"{FROZEN}: {'; '.join(problems)}.")

    # Removidas (só chega aqui antes do álbum começar): sem histórico, apaga; com histórico, desativa.
    wanted = {s.key for s in content.stickers}
    for key, row in existing.items():
        if key not in wanted:
            if content_service.has_history(db, row.id):
                row.active = False
            else:
                db.delete(row)
    db.flush()

    taken = set(db.scalars(select(Sticker.number).where(Sticker.album_id == album.id)))
    default_rarity = _lowest_rarity(db)
    for item in content.stickers:
        change = overrides.get(item.key, {})
        rarity_id = change.get("rarity_id")
        if rarity_id is not None and rarity_id not in rarities:
            raise HTTPException(422, "Raridade desconhecida.")
        values = {
            "name": item.name,
            "image_url": url(item.asset_id),
            "size": item.size,
            "tag": item.tag,
            "hint": item.hint,
            "back_text": item.back_text,
            "back_links": [link.model_dump() for link in item.back_links],
            "page_no": item.page_no,
            "slot_layout": item.slot.model_dump(),
        }
        row = existing.get(item.key)
        if row is None:
            number = change.get("number")
            if number is None:
                number = max(taken, default=0) + 1  # próximo livre no álbum; o editor não escolhe número
            elif number in taken:
                raise HTTPException(409, f"O número {number} já está em uso neste álbum.")
            taken.add(number)
            # Raridade: a que o superadmin escolheu, senão a sugerida pelo editor, senão a mais comum.
            db.add(
                Sticker(
                    album_id=album.id, org_id=chapter.org_id, content_key=item.key, number=number,
                    rarity_id=rarity_id or item.suggested_rarity_id or default_rarity, active=item.active, **values,
                )
            )
            continue
        for field, value in values.items():
            setattr(row, field, value)
        if status != "active":
            row.active = item.active
            if rarity_id is not None:
                row.rarity_id = rarity_id
            number = change.get("number")
            if number is not None and number != row.number:
                if number in taken:
                    raise HTTPException(409, f"O número {number} já está em uso neste álbum.")
                taken.discard(row.number)
                taken.add(number)
                row.number = number

    if content.org is not None:
        chapter.org.name = content.org.name
        chapter.org.description = content.org.description
        chapter.org.theme_color = content.org.color
        chapter.org.logo_url = url(content.org.logo_asset_id)
    chapter.layout = {
        "orientation": content.layout.orientation,
        "pages": [
            {
                "page_no": page.page_no, "title": page.title, "text": page.text,
                "background_url": url(page.background_asset_id), "layout": page.layout,
            }
            for page in sorted(content.pages, key=lambda p: p.page_no)
        ],
    }
    db.flush()
    _apply_mosaic(db, chapter, content, url, mosaic_rarity_id or default_rarity)

    used = {s.asset_id for s in content.stickers} | {p.background_asset_id for p in content.pages}
    if content.mosaic is not None:
        used.add(content.mosaic.asset_id)
    if content.org is not None:
        used.add(content.org.logo_asset_id)
    for asset_id in used - {None}:
        assets[asset_id].status = "approved"  # publicado: a imagem deixa de ser privada
    db.flush()
    # As imagens que saíram de produção ficam guardadas: o rollback precisa delas.


def _apply_mosaic(db: Session, chapter: AlbumChapter, content: Content, url, rarity_id: int) -> None:
    pieces = _production_stickers(db, chapter, mosaic=True)
    target = url(content.mosaic.asset_id) if content.mosaic is not None else None
    if target is None:
        # Sem imagem não há mosaico. (Com o álbum em andamento, `structural_problems` já barrou a remoção.)
        for piece in pieces:
            content_service.delete_sticker(db, piece)
        chapter.mosaic_image_url = None
        if content.mosaic is not None:
            chapter.mosaic_cols, chapter.mosaic_rows = content.mosaic.cols, content.mosaic.rows
        return
    same_grid = pieces and (content.mosaic.cols, content.mosaic.rows) == (chapter.mosaic_cols, chapter.mosaic_rows)
    if same_grid and chapter.mosaic_image_url == target:
        return
    chapter.mosaic_image_url = target
    try:
        if same_grid:
            content_service.recut_mosaic(db, chapter)  # só a imagem mudou: mesmas peças, novos recortes
        else:
            content_service.generate_mosaic(db, chapter, content.mosaic.cols, content.mosaic.rows, rarity_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---- fluxo da revisão


def open_revision(db: Session, chapter_id: int) -> ChapterRevision | None:
    return db.scalar(select(ChapterRevision).where(ChapterRevision.chapter_id == chapter_id, ChapterRevision.status.in_(OPEN)))


def _next_number(db: Session, chapter_id: int) -> int:
    return (db.scalar(select(func.max(ChapterRevision.number)).where(ChapterRevision.chapter_id == chapter_id)) or 0) + 1


def _album(db: Session, chapter: AlbumChapter) -> Album:
    album = db.get(Album, chapter.album_id)
    if album_status(album) == "ended":
        raise HTTPException(409, content_service.CLOSED)
    return album


def save_draft(db: Session, user: User, chapter: AlbumChapter, data: Any) -> ChapterRevision:
    """Cria ou atualiza o único rascunho aberto do capítulo. Não toca em produção."""
    album = _album(db, chapter)
    content = parse_content(data)
    check_references(db, chapter, content)
    if album_status(album) == "active":
        problems = structural_problems(db, chapter, content)
        if problems:
            raise HTTPException(409, f"{FROZEN}: {'; '.join(problems)}.")
    revision = open_revision(db, chapter.id)
    if revision is None:
        revision = ChapterRevision(
            chapter_id=chapter.id, number=_next_number(db, chapter.id), status="draft", author_id=user.id,
            base_published_revision_id=chapter.published_revision_id, content={},
        )
        db.add(revision)
    elif revision.status not in EDITABLE:
        raise HTTPException(409, "Esta revisão já foi enviada e não pode mais ser editada.")
    revision.status = "draft"  # rejeitada volta a ser rascunho quando o editor mexe
    revision.content = content.model_dump(mode="json")
    revision.author_id = user.id
    revision.updated_at = now()
    db.flush()
    return revision


def submit(db: Session, user: User, chapter: AlbumChapter) -> ChapterRevision:
    _album(db, chapter)
    revision = open_revision(db, chapter.id)
    if revision is None or revision.status not in EDITABLE:
        raise HTTPException(409, "Não há rascunho para enviar.")
    content = parse_content(revision.content)
    check_references(db, chapter, content)
    revision.status = "submitted"
    revision.submitted_at = now()
    audit(db, user.id, "revision.submit", revision_id=revision.id, chapter_id=chapter.id)
    return revision


def review(db: Session, admin: User, revision: ChapterRevision, approve: bool, note: str) -> ChapterRevision:
    if revision.status != "submitted":
        raise HTTPException(409, "Só revisão enviada pode ser aprovada ou rejeitada.")
    if not approve and not note.strip():
        raise HTTPException(400, "Explique o motivo da rejeição para quem vai corrigir.")
    revision.status = "approved" if approve else "rejected"
    revision.reviewed_by = admin.id
    revision.reviewed_at = now()
    revision.review_note = _clean(note)[:2000]
    audit(
        db, admin.id, "revision.approve" if approve else "revision.reject",
        revision_id=revision.id, chapter_id=revision.chapter_id, note=revision.review_note,
    )
    return revision


def _publish(
    db: Session, admin: User, chapter: AlbumChapter, revision: ChapterRevision, content: Content,
    overrides: dict[str, dict[str, int]] | None, mosaic_rarity_id: int | None,
) -> None:
    album = db.get(Album, chapter.album_id)
    snapshot = production_content(db, chapter)
    apply_content(db, chapter, album, content, overrides, mosaic_rarity_id)
    previous = db.get(ChapterRevision, chapter.published_revision_id) if chapter.published_revision_id else None
    if previous is not None:
        previous.status = "superseded"
    revision.status = "published"
    revision.published_at = now()
    revision.previous_content = snapshot
    db.flush()
    chapter.published_revision_id = revision.id


def publish(
    db: Session, admin: User, revision: ChapterRevision,
    overrides: dict[str, dict[str, int]] | None = None, mosaic_rarity_id: int | None = None,
) -> ChapterRevision:
    if revision.status != "approved":
        raise HTTPException(409, "Só revisão aprovada pode ser publicada.")
    chapter = db.execute(
        select(AlbumChapter).where(AlbumChapter.id == revision.chapter_id).with_for_update(of=AlbumChapter)
    ).scalar_one()
    _publish(db, admin, chapter, revision, parse_content(revision.content), overrides, mosaic_rarity_id)
    audit(
        db, admin.id, "revision.publish",
        revision_id=revision.id, chapter_id=chapter.id, album_id=chapter.album_id, overrides=overrides or {},
    )
    return revision


def rollback(db: Session, admin: User, revision: ChapterRevision) -> ChapterRevision:
    """Republica o que estava em produção antes de `revision`, pelas mesmas regras de publicação."""
    chapter = db.execute(
        select(AlbumChapter).where(AlbumChapter.id == revision.chapter_id).with_for_update(of=AlbumChapter)
    ).scalar_one()
    if revision.status != "published" or chapter.published_revision_id != revision.id:
        raise HTTPException(409, "Só a revisão que está publicada pode ser desfeita.")
    snapshot = revision.previous_content
    if not snapshot:
        raise HTTPException(409, "Esta revisão não guarda o estado anterior.")
    restored = ChapterRevision(
        chapter_id=chapter.id, number=_next_number(db, chapter.id), status="approved", author_id=admin.id,
        content=snapshot["content"], reviewed_by=admin.id, reviewed_at=now(),
        review_note=f"Rollback da revisão {revision.number}.", base_published_revision_id=revision.id,
    )
    # A revisão em aberto (se houver) continua aberta: a restaurada nasce e é publicada na mesma transação.
    restored.status = "published"
    db.add(restored)
    db.flush()
    _publish(db, admin, chapter, restored, parse_content(snapshot["content"]), snapshot.get("meta") or {}, snapshot.get("mosaic_rarity_id"))
    audit(
        db, admin.id, "revision.rollback",
        revision_id=revision.id, restored_revision_id=restored.id, chapter_id=chapter.id, album_id=chapter.album_id,
    )
    return restored


# ---- diff para o painel de revisão


def diff(db: Session, chapter: AlbumChapter, proposed: dict) -> dict:
    """O que a proposta muda em relação ao publicado, com as imagens dos dois lados."""
    current = production_content(db, chapter)["content"]
    assets = chapter_assets(db, chapter.id)
    url = lambda asset_id: assets[asset_id].url if asset_id in assets else None  # noqa: E731
    old = {s["key"]: s for s in current["stickers"]}
    new = {s["key"]: s for s in proposed.get("stickers", [])}

    def view(sticker: dict) -> dict:
        return {"key": sticker["key"], "name": sticker["name"], "image_url": url(sticker.get("asset_id")), "page_no": sticker["page_no"]}

    changed = []
    for key in new.keys() & old.keys():
        fields = {
            field: {"old": old[key].get(field), "new": new[key].get(field)}
            for field in ("name", "size", "tag", "hint", "back_text", "back_links", "page_no", "slot", "active", "suggested_rarity_id")
            if old[key].get(field) != new[key].get(field)
        }
        if old[key].get("asset_id") != new[key].get("asset_id"):
            fields["image"] = {"old": url(old[key].get("asset_id")), "new": url(new[key].get("asset_id"))}
        if fields:
            changed.append({**view(new[key]), "fields": fields})
    old_pages = {p["page_no"]: p for p in current["pages"]}
    new_pages = {p["page_no"]: p for p in proposed.get("pages", [])}
    pages = []
    for page_no in sorted(old_pages.keys() | new_pages.keys()):
        before, after = old_pages.get(page_no), new_pages.get(page_no)
        if before == after:
            continue
        entry: dict[str, Any] = {"page_no": page_no, "kind": "added" if before is None else "removed" if after is None else "changed", "fields": {}}
        for field in ("title", "text", "layout"):
            if (before or {}).get(field) != (after or {}).get(field):
                entry["fields"][field] = {"old": (before or {}).get(field), "new": (after or {}).get(field)}
        if (before or {}).get("background_asset_id") != (after or {}).get("background_asset_id"):
            entry["fields"]["background"] = {
                "old": url((before or {}).get("background_asset_id")), "new": url((after or {}).get("background_asset_id")),
            }
        pages.append(entry)
    old_mosaic, new_mosaic = current.get("mosaic"), proposed.get("mosaic")
    mosaic = None
    if old_mosaic != new_mosaic:
        mosaic = {
            "old": {**old_mosaic, "image_url": url(old_mosaic.get("asset_id"))} if old_mosaic else None,
            "new": {**new_mosaic, "image_url": url(new_mosaic.get("asset_id"))} if new_mosaic else None,
        }
    orientation = None
    if current["layout"] != proposed.get("layout", current["layout"]):
        orientation = {"old": current["layout"]["orientation"], "new": proposed["layout"]["orientation"]}
    old_org, new_org = current.get("org"), proposed.get("org")
    org = None
    if new_org is not None and old_org != new_org:
        org = {
            field: {"old": (old_org or {}).get(field), "new": new_org.get(field)}
            for field in ("name", "description", "color")
            if (old_org or {}).get(field) != new_org.get(field)
        }
        if (old_org or {}).get("logo_asset_id") != new_org.get("logo_asset_id"):
            org["logo"] = {"old": url((old_org or {}).get("logo_asset_id")), "new": url(new_org.get("logo_asset_id"))}
    return {
        "org": org or None,
        "added": [view(new[key]) for key in new.keys() - old.keys()],
        "removed": [view(old[key]) for key in old.keys() - new.keys()],
        "changed": sorted(changed, key=lambda item: item["name"]),
        "pages": pages,
        "mosaic": mosaic,
        "orientation": orientation,
    }
