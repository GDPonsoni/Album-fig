import io
import uuid
from pathlib import Path

from PIL import Image

from app.settings import settings

MAX_BYTES = 150 * 1024


def save_webp(image: Image.Image, folder: str = "uploads", max_side: int = 900) -> str:
    """Grava em WebP abaixo do limite, reduzindo qualidade e depois tamanho."""
    image = image.convert("RGBA")
    image.thumbnail((max_side, max_side))
    data = b""
    for _ in range(4):
        for quality in (85, 70, 55, 40):
            buffer = io.BytesIO()
            image.save(buffer, "WEBP", quality=quality, method=4)
            data = buffer.getvalue()
            if len(data) <= MAX_BYTES:
                break
        if len(data) <= MAX_BYTES:
            break
        image = image.resize((int(image.width * 0.8), int(image.height * 0.8)))
    name = f"{uuid.uuid4().hex}.webp"
    target = settings.media_dir / folder
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_bytes(data)
    return f"/media/{folder}/{name}"


def open_upload(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    if image.format not in {"PNG", "WEBP", "JPEG"}:
        raise ValueError("Formato não aceito")
    image.load()
    return image


def local_path(url: str | None) -> Path | None:
    if not url or not url.startswith("/media/"):
        return None
    path = (settings.media_dir / url.removeprefix("/media/")).resolve()
    return path if path.is_relative_to(settings.media_dir.resolve()) else None


def remove_media(url: str | None) -> None:
    path = local_path(url)
    if path is not None:
        path.unlink(missing_ok=True)
