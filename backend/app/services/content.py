"""Operações de conteúdo compartilhadas entre o admin e o seed."""
import io

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Album, AlbumChapter, Asset, Org, PackOpeningItem, Sticker, TradeOffer, UserSticker
from app.services.albums import album_status
from app.services.images import read_media, remove_media, save_webp

FROZEN = "Com o álbum em andamento a estrutura fica congelada: só texto, imagem e layout mudam."
CLOSED = "Álbum encerrado não pode ser alterado."


def require_editable(album: Album, structural: bool = False) -> None:
    """Guarda de conteúdo. Álbum encerrado é imutável; em andamento, só o cosmético muda
    (`structural` marca o que adiciona/remove figurinha ou mexe em número e raridade)."""
    status = album_status(album)
    if status == "ended":
        raise HTTPException(409, CLOSED)
    if structural and status in ("active", "paused"):
        raise HTTPException(409, FROZEN)


def next_number(db: Session, album_id: int) -> int:
    """Próximo número livre dentro do álbum."""
    return (db.scalar(select(func.max(Sticker.number)).where(Sticker.album_id == album_id)) or 0) + 1


def media_in_use(db: Session, url: str | None) -> int:
    """Quantos registros apontam para a imagem. Álbum duplicado compartilha os arquivos do original."""
    if not url:
        return 0
    return sum(
        db.scalar(select(func.count()).select_from(model).where(column == url)) or 0
        for model, column in (
            (Sticker, Sticker.image_url),
            (AlbumChapter, AlbumChapter.mosaic_image_url),
            (Org, Org.logo_url),
            (Album, Album.cover_url),
            (Asset, Asset.url),  # rascunho, revisão guardada ou rollback ainda podem precisar dela
        )
    )


def remove_media_if_unused(db: Session, url: str | None) -> None:
    """Chamar depois do flush que tirou a referência: só apaga o arquivo que ninguém mais usa."""
    if url and media_in_use(db, url) == 0:
        remove_media(url)


def has_history(db: Session, sticker_id: int) -> bool:
    """Figurinha que alguém já teve, recebeu num pacote ou ofereceu numa troca."""
    return any(
        db.scalar(select(model.sticker_id).where(model.sticker_id == sticker_id).limit(1)) is not None
        for model in (UserSticker, PackOpeningItem, TradeOffer)
    )


def delete_sticker(db: Session, sticker: Sticker) -> None:
    """Só apaga figurinha sem histórico: inventário, abertura de pacote e oferta de troca
    são permanentes (o banco também recusa, com ON DELETE RESTRICT)."""
    if has_history(db, sticker.id):
        raise HTTPException(409, "Esta figurinha já está no inventário ou no histórico de alguém e não pode ser apagada.")
    image = sticker.image_url
    db.delete(sticker)
    db.flush()
    remove_media_if_unused(db, image)


def _mosaic_tiles(image: Image.Image, cols: int, rows: int) -> list[Image.Image]:
    """Recorta a imagem em cols x rows peças, todas na proporção 3:4 das figurinhas normais.
    Se a imagem não tem a proporção (3*cols):(4*rows), corta as sobras do centro antes de dividir."""
    image = image.convert("RGBA")
    target = (3 * cols) / (4 * rows)
    if image.width / image.height > target:
        width = round(image.height * target)
        left = (image.width - width) // 2
        image = image.crop((left, 0, left + width, image.height))
    else:
        height = round(image.width / target)
        top = (image.height - height) // 2
        image = image.crop((0, top, image.width, top + height))
    width, height = image.width / cols, image.height / rows
    return [
        image.crop((round(c * width), round(r * height), round((c + 1) * width), round((r + 1) * height)))
        for r in range(rows)
        for c in range(cols)
    ]


def generate_mosaic(db: Session, chapter: AlbumChapter, cols: int, rows: int, rarity_id: int) -> list[Sticker]:
    """Corta a imagem do mosaico em peças e substitui as peças antigas do capítulo."""
    source = read_media(chapter.mosaic_image_url)
    if source is None:
        raise ValueError("O capítulo precisa de uma imagem de mosaico enviada ao servidor.")
    org = chapter.org
    for old in db.scalars(
        select(Sticker).where(
            Sticker.album_id == chapter.album_id, Sticker.org_id == org.id, Sticker.is_mosaic_piece.is_(True)
        )
    ).all():
        delete_sticker(db, old)
    db.flush()

    chapter.mosaic_cols, chapter.mosaic_rows = cols, rows
    number = next_number(db, chapter.album_id)
    pieces = []
    with Image.open(io.BytesIO(source)) as image:
        for index, crop in enumerate(_mosaic_tiles(image, cols, rows)):
            piece = Sticker(
                album_id=chapter.album_id,
                org_id=org.id,
                number=number + index,
                name=f"{org.name}: peça {index + 1} de {cols * rows}",
                image_url=save_webp(crop, f"orgs/{org.name}/pecas/peca {index + 1:02d}", 500),
                size="P",
                rarity_id=rarity_id,
                tag="mosaico",
                hint=f"Peça {index + 1} do mosaico",
                back_text=org.description,
                back_links=[],
                page_no=0,
                slot_layout={},
                is_mosaic_piece=True,
                mosaic_index=index,
            )
            db.add(piece)
            pieces.append(piece)
    db.flush()
    return pieces


def recut_mosaic(db: Session, chapter: AlbumChapter) -> None:
    """A imagem do mosaico mudou, a grade não: as mesmas peças ganham recortes novos.
    É a única troca de mosaico permitida com o álbum em andamento (nenhuma figurinha nasce ou some)."""
    source = read_media(chapter.mosaic_image_url)
    if source is None:
        raise ValueError("O capítulo precisa de uma imagem de mosaico enviada ao servidor.")
    pieces = db.scalars(
        select(Sticker).where(
            Sticker.album_id == chapter.album_id, Sticker.org_id == chapter.org_id, Sticker.is_mosaic_piece.is_(True)
        )
    ).all()
    cols, rows = chapter.mosaic_cols, chapter.mosaic_rows
    old_images = []
    with Image.open(io.BytesIO(source)) as image:
        tiles = _mosaic_tiles(image, cols, rows)
        for piece in pieces:
            index = piece.mosaic_index or 0
            crop = tiles[index]
            old_images.append(piece.image_url)
            piece.image_url = save_webp(crop, f"orgs/{chapter.org.name}/pecas/peca {index + 1:02d}", 500)
    db.flush()
    for url in old_images:
        remove_media_if_unused(db, url)
