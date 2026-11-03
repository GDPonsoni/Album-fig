import io
import re
import secrets
import unicodedata
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from PIL import Image

from app.settings import settings

MAX_BYTES = 150 * 1024


@lru_cache
def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def _r2_key(url: str | None) -> str | None:
    """Só URLs nossas viram chave: o admin também pode informar imagens externas."""
    if not url or not settings.r2_enabled or not url.startswith("/media/"):
        return None
    return url.removeprefix("/media/")


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")


def media_key(name: str) -> str:
    """"orgs/Atlética/logo" vira "orgs/atletica/logo-<aleatório>.webp". O sufixo
    impede que um jogador adivinhe a URL de uma figurinha que ainda não tem."""
    *folders, stem = name.split("/")
    path = [slug for slug in map(_slug, folders[:4]) if slug] + [_slug(stem) or "imagem"]
    return "/".join(path) + f"-{secrets.token_hex(5)}.webp"


def save_webp(image: Image.Image, name: str = "uploads/imagem", max_side: int = 900) -> str:
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
    key = media_key(name)
    if settings.r2_enabled:
        _r2_client().put_object(Bucket=settings.r2_bucket, Key=key, Body=data, ContentType="image/webp")
    else:
        target = settings.media_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return f"/media/{key}"


def open_upload(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    if image.format not in {"PNG", "WEBP", "JPEG"}:
        raise ValueError("Formato não aceito")
    image.load()
    return image


def _local_path(url: str | None) -> Path | None:
    if not url or not url.startswith("/media/"):
        return None
    path = (settings.media_dir / url.removeprefix("/media/")).resolve()
    return path if path.is_relative_to(settings.media_dir.resolve()) else None


def read_media(url: str | None) -> bytes | None:
    """Conteúdo de uma imagem enviada ao servidor; None se for externa ou não existir."""
    key = _r2_key(url)
    if key is not None:
        try:
            return _r2_client().get_object(Bucket=settings.r2_bucket, Key=key)["Body"].read()
        except ClientError:
            return None
    path = _local_path(url)
    return path.read_bytes() if path is not None and path.is_file() else None


def remove_media(url: str | None) -> None:
    key = _r2_key(url)
    if key is not None:
        _r2_client().delete_object(Bucket=settings.r2_bucket, Key=key)
        return
    path = _local_path(url)
    if path is not None:
        path.unlink(missing_ok=True)
