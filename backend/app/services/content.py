"""Operações de conteúdo compartilhadas entre o admin e o seed."""
import io

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Album, AlbumChapter, Org, PackOpeningItem, Sticker, TradeOffer, UserSticker
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
    if structural and status == "active":
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
        image = image.convert("RGBA")
        width, height = image.width / cols, image.height / rows
        for index in range(cols * rows):
            col, row = index % cols, index // cols
            crop = image.crop(
                (round(col * width), round(row * height), round((col + 1) * width), round((row + 1) * height))
            )
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
