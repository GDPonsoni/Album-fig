"""Área do editor de um capítulo. Tudo aqui escreve só em `chapter_revision` e `asset`:
nenhuma rota deste arquivo altera figurinha, capítulo, pacote, código ou parâmetro."""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from PIL.Image import DecompressionBombError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_chapter_editor, verified_user
from app.db import get_db
from app.models import Album, AlbumChapter, Asset, ChapterRevision, Rarity, RoleGrant, User
from app.services import images, revisions
from app.services.albums import album_status, album_view
from app.services.core import audit, enforce_rate, now

router = APIRouter(prefix="/editor", tags=["editor"])

MAX_ASSETS = 100
MAX_TOTAL_BYTES = 12 * 1024 * 1024  # por capítulo; cada imagem já sai do servidor com no máximo 150 KB
MAX_UPLOAD = 8 * 1024 * 1024
KINDS = {"sticker": 640, "background": 1200, "mosaic": 1200}  # lado maior, em pixels


class DraftIn(BaseModel):
    content: dict[str, Any]


def _asset_view(asset: Asset) -> dict:
    return {"id": asset.id, "url": asset.url, "kind": asset.kind, "status": asset.status, "bytes": asset.bytes}


def _revision_view(revision: ChapterRevision | None) -> dict | None:
    if revision is None:
        return None
    return {
        "id": revision.id,
        "number": revision.number,
        "status": revision.status,
        "updated_at": revision.updated_at,
        "submitted_at": revision.submitted_at,
        "reviewed_at": revision.reviewed_at,
        "review_note": revision.review_note,
    }


def _chapter_view(db: Session, chapter: AlbumChapter) -> dict:
    album = db.get(Album, chapter.album_id)
    return {
        "id": chapter.id,
        "org": {
            "id": chapter.org.id, "name": chapter.org.name, "description": chapter.org.description,
            "logo_url": chapter.org.logo_url, "color": chapter.org.theme_color,
        },
        "album": album_view(album),
        # Em andamento só o cosmético muda; encerrado, nada.
        "frozen": album_status(album) == "active",
        "closed": album_status(album) == "ended",
    }


@router.get("/chapters")
def my_chapters(user: User = Depends(verified_user), db: Session = Depends(get_db)):
    """Só os capítulos em que o usuário é editor agora."""
    rows = db.scalars(
        select(AlbumChapter)
        .join(RoleGrant, RoleGrant.chapter_id == AlbumChapter.id)
        .where(
            RoleGrant.user_id == user.id,
            RoleGrant.role == "editor",
            RoleGrant.revoked_at.is_(None),
            (RoleGrant.expires_at.is_(None)) | (RoleGrant.expires_at > now()),
        )
        .order_by(AlbumChapter.id)
    ).unique().all()
    result = []
    for chapter in rows:
        view = _chapter_view(db, chapter)
        view["revision"] = _revision_view(revisions.open_revision(db, chapter.id))
        result.append(view)
    return result


@router.get("/chapters/{chapter_id}/draft")
def get_draft(chapter: AlbumChapter = Depends(require_chapter_editor), db: Session = Depends(get_db)):
    """O rascunho aberto ou, se não houver, o conteúdo publicado como ponto de partida (ainda não salvo)."""
    revision = revisions.open_revision(db, chapter.id)
    published = revisions.production_content(db, chapter)["content"]
    db.commit()  # `production_content` pode ter registrado como ativo uma imagem que já estava em produção
    return {
        "chapter": _chapter_view(db, chapter),
        "revision": _revision_view(revision),
        "content": revision.content if revision is not None else published,
        "published": published,
        "editable": revision is None or revision.status in revisions.EDITABLE,
        "assets": [_asset_view(a) for a in db.scalars(select(Asset).where(Asset.chapter_id == chapter.id).order_by(Asset.id))],
        "rarities": [
            {"id": r.id, "name": r.name, "rank": r.rank, "color": r.color} for r in db.scalars(select(Rarity).order_by(Rarity.rank))
        ],
    }


@router.put("/chapters/{chapter_id}/draft")
def put_draft(
    body: DraftIn,
    chapter: AlbumChapter = Depends(require_chapter_editor),
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
):
    revision = revisions.save_draft(db, user, chapter, body.content)
    db.commit()
    return {"revision": _revision_view(revision), "content": revision.content}


@router.post("/chapters/{chapter_id}/submit")
def submit(
    chapter: AlbumChapter = Depends(require_chapter_editor),
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
):
    revision = revisions.submit(db, user, chapter)
    db.commit()
    return {"revision": _revision_view(revision)}


@router.get("/chapters/{chapter_id}/assets")
def list_assets(chapter: AlbumChapter = Depends(require_chapter_editor), db: Session = Depends(get_db)):
    return [_asset_view(a) for a in db.scalars(select(Asset).where(Asset.chapter_id == chapter.id).order_by(Asset.id))]


@router.post("/chapters/{chapter_id}/assets", status_code=201)
async def upload_asset(
    file: UploadFile,
    kind: Annotated[str, Form(max_length=20)] = "sticker",
    chapter: AlbumChapter = Depends(require_chapter_editor),
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Toda imagem é reencodada pelo servidor (WebP ≤ 150 KB) e ganha nome aleatório. Fica privada até a publicação."""
    if kind not in KINDS:
        raise HTTPException(400, "Tipo de imagem desconhecido.")
    if album_status(db.get(Album, chapter.album_id)) == "ended":
        raise HTTPException(409, "Álbum encerrado não pode ser alterado.")
    enforce_rate(db, f"asset-upload:{user.id}", "rate.asset_upload_per_hour")
    db.commit()
    count, total = db.execute(
        select(func.count(), func.coalesce(func.sum(Asset.bytes), 0)).where(Asset.chapter_id == chapter.id)
    ).one()
    if count >= MAX_ASSETS or total >= MAX_TOTAL_BYTES:
        raise HTTPException(409, "Este capítulo atingiu o limite de imagens. Apague as que não usa mais.")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Imagem maior que 8 MB.")
    try:
        image = images.open_upload(data)
    except (ValueError, OSError, DecompressionBombError) as exc:
        raise HTTPException(400, "Envie uma imagem PNG, WebP ou JPEG válida.") from exc
    url = images.save_webp(image, f"capitulos/{chapter.id}/{kind}", KINDS[kind])
    stored = images.read_media(url) or b""
    asset = Asset(
        chapter_id=chapter.id, uploaded_by=user.id, key=url.removeprefix("/media/"), url=url, kind=kind,
        bytes=len(stored), status="pending",
    )
    db.add(asset)
    db.flush()
    audit(db, user.id, "asset.upload", asset_id=asset.id, chapter_id=chapter.id, bytes=asset.bytes)
    db.commit()
    return _asset_view(asset)


@router.delete("/chapters/{chapter_id}/assets/{asset_id}", status_code=204)
def delete_asset(
    asset_id: int,
    chapter: AlbumChapter = Depends(require_chapter_editor),
    user: User = Depends(verified_user),
    db: Session = Depends(get_db),
):
    """Só imagem ainda não publicada e que o rascunho aberto não usa."""
    asset = db.get(Asset, asset_id)
    if asset is None or asset.chapter_id != chapter.id:
        raise HTTPException(404, "Imagem não encontrada.")
    if asset.status != "pending":
        raise HTTPException(409, "Imagem já publicada não pode ser apagada por aqui.")
    revision = revisions.open_revision(db, chapter.id)
    if revision is not None and _uses(revision.content, asset_id):
        raise HTTPException(409, "Tire a imagem do rascunho antes de apagá-la.")
    url = asset.url
    db.delete(asset)
    audit(db, user.id, "asset.delete", asset_id=asset_id, chapter_id=chapter.id)
    db.commit()
    images.remove_media(url)


def _uses(content: dict, asset_id: int) -> bool:
    ids = {s.get("asset_id") for s in content.get("stickers", [])} | {p.get("background_asset_id") for p in content.get("pages", [])}
    ids.add((content.get("mosaic") or {}).get("asset_id"))
    return asset_id in ids
