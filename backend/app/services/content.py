"""Operações de conteúdo compartilhadas entre o admin e o seed."""
from PIL import Image
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Org, Sticker, Trade, TradeOffer
from app.services.images import local_path, remove_media, save_webp


def next_number(db: Session) -> int:
    return (db.scalar(select(func.max(Sticker.number))) or 0) + 1


def generate_mosaic(db: Session, org: Org, cols: int, rows: int, rarity_id: int) -> list[Sticker]:
    """Corta a imagem do mosaico em peças e substitui as peças antigas da org."""
    path = local_path(org.mosaic_image_url)
    if path is None or not path.exists():
        raise ValueError("A org precisa de uma imagem de mosaico enviada ao servidor.")
    for old in db.scalars(select(Sticker).where(Sticker.org_id == org.id, Sticker.is_mosaic_piece.is_(True))):
        delete_sticker(db, old)
    db.flush()

    org.mosaic_cols, org.mosaic_rows = cols, rows
    number = next_number(db)
    pieces = []
    with Image.open(path) as image:
        image = image.convert("RGBA")
        width, height = image.width / cols, image.height / rows
        for index in range(cols * rows):
            col, row = index % cols, index // cols
            crop = image.crop(
                (round(col * width), round(row * height), round((col + 1) * width), round((row + 1) * height))
            )
            piece = Sticker(
                org_id=org.id,
                number=number + index,
                name=f"{org.name}: peça {index + 1} de {cols * rows}",
                image_url=save_webp(crop, "mosaic", 500),
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


def delete_sticker(db: Session, sticker: Sticker) -> list[int]:
    """Remove de verdade: imagem, inventários e ofertas. Trocas em curso
    ganham nova versão de oferta, para ninguém concluir sobre dados antigos."""
    affected = list(
        db.scalars(
            select(Trade.id)
            .join(TradeOffer, TradeOffer.trade_id == Trade.id)
            .where(TradeOffer.sticker_id == sticker.id, Trade.state == "active")
            .distinct()
        )
    )
    if affected:
        db.execute(update(Trade).where(Trade.id.in_(affected)).values(offer_version=Trade.offer_version + 1))
    remove_media(sticker.image_url)
    db.delete(sticker)
    return affected
