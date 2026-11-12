"""Conteúdo fictício para desenvolvimento: o álbum "Calourada", com 5 orgs e 125 figurinhas de imagens geradas."""
import random
import secrets
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from app.api.auth import hasher
from app.db import SessionLocal
from app.models import AlbumChapter, Org, Rarity, RedeemCode, RoleGrant, Sticker, User
from app.services import albums, content
from app.services.codes import generate_code
from app.services.images import save_webp
from app.services.packs import album_pack_type
from app.settings import settings

ORGS = [
    ("Tubarões da Engenharia", "#0f6e8c", "Atlética fictícia de exemplo. Treinos de natação às terças e a melhor bateria do campus."),
    ("Raposas do Direito", "#b4451f", "Atlética fictícia de exemplo. Handebol, debates acalorados e churrasco de calourada."),
    ("Corujas da Medicina", "#3f6b3a", "Atlética fictícia de exemplo. Plantão de estudos de dia, vôlei à noite."),
    ("Capivaras da Computação", "#6a3fa0", "Atlética fictícia de exemplo. E-sports, futsal e um mascote que nunca se estressa."),
    ("Lobos da Arquitetura", "#a8326a", "Atlética fictícia de exemplo. Basquete, maquetes de madrugada e a festa mais bonita do ano."),
]
FIRST = "Alice Bruno Carla Davi Elisa Felipe Gabriela Heitor Iara João Kauê Lara Miguel Nina Otávio Paula Rafael Sofia Tiago Vitória".split()
LAST = "Almeida Barros Costa Dias Farias Gomes Lima Moura Nunes Pires Ramos Souza Teixeira Vieira".split()
ROLES = ["Bateria", "Natação", "Futsal", "Vôlei", "Gestão", "Handebol", "Basquete", "Eventos", "Marketing", "Torcida"]

# Espaços por página, em % da página. Todas as figurinhas têm proporção 3:4.
PAGE_TEMPLATES = [
    [("G", 5, 15, 42, 40), ("M", 52, 15, 28, 27), ("P", 52, 44, 21, 20), ("P", 75, 44, 21, 20),
     ("M", 5, 58, 28, 27), ("M", 36, 66, 28, 27), ("M", 67, 66, 28, 27)],
    [("M", 5, 15, 28, 27), ("M", 36, 15, 28, 27), ("M", 67, 15, 28, 27),
     ("G", 5, 45, 42, 40), ("P", 52, 55, 21, 20), ("P", 75, 55, 21, 20)],
    [("P", 5, 15, 21, 20), ("P", 28, 15, 21, 20), ("G", 53, 15, 42, 40),
     ("M", 5, 38, 28, 27), ("M", 5, 68, 28, 27), ("M", 36, 68, 28, 27)],
]
SIZE_PX = {"P": (300, 400), "M": (360, 480), "G": (480, 640)}


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _shade(color: str, factor: float) -> tuple[int, int, int]:
    rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
    if factor >= 0:
        return tuple(round(c + (255 - c) * factor) for c in rgb)
    return tuple(round(c * (1 + factor)) for c in rgb)


def _portrait(name: str, color: str, size: str, rng: random.Random) -> Image.Image:
    """Marcador de posição: uma silhueta colorida com as iniciais, não uma pessoa real."""
    width, height = SIZE_PX[size]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=width // 14, fill=_shade(color, rng.uniform(0.55, 0.75)))
    tone = _shade(color, rng.uniform(-0.35, -0.05))
    head = width * 0.2
    cx, cy = width / 2, height * 0.38
    draw.ellipse((cx - width * 0.4, height * 0.6, cx + width * 0.4, height * 1.2), fill=tone)
    draw.ellipse((cx - head, cy - head, cx + head, cy + head), fill=tone)
    initials = "".join(part[0] for part in name.split()[:2])
    draw.text((cx, cy), initials, font=_font(int(width * 0.16)), fill="white", anchor="mm")
    return image


def _poster(name: str, color: str, rng: random.Random) -> Image.Image:
    width, height = 800, 1200
    image = Image.new("RGBA", (width, height), _shade(color, -0.25))
    draw = ImageDraw.Draw(image)
    for _ in range(26):
        radius = rng.randint(50, 240)
        x, y = rng.randint(0, width), rng.randint(0, height)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=_shade(color, rng.uniform(-0.45, 0.55)))
    words = name.split()
    y = height / 2 - (len(words) - 1) * 65
    for word in words:
        draw.text((width / 2, y), word.upper(), font=_font(86), fill="white", anchor="mm", stroke_width=6, stroke_fill=_shade(color, -0.6))
        y += 130
    return image


def _logo(name: str, color: str) -> Image.Image:
    image = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 236, 236), fill=color, outline="white", width=8)
    draw.text((120, 120), name[0], font=_font(120), fill="white", anchor="mm")
    return image


def seed_demo() -> None:
    rng = random.Random(2026)
    with SessionLocal() as db:
        if db.scalar(select(Org.id).limit(1)) is not None:
            print("Já há conteúdo no banco; nada a fazer.")
            return
        rarities = {r.name: r for r in db.scalars(select(Rarity))}
        university = albums.default_university(db)
        # O álbum de exemplo já nasce em andamento: começou ontem e dura 90 dias.
        today = datetime.now(UTC)
        album = albums.create_album(
            db, university.id, "Calourada", today - timedelta(days=1), today + timedelta(days=90), state="ready"
        )
        number = 1
        chapters: list[AlbumChapter] = []
        for order, (name, color, description) in enumerate(ORGS):
            org = Org(
                university_id=university.id,
                name=name,
                description=description,
                theme_color=color,
                logo_url=save_webp(_logo(name, color), f"orgs/{name}/logo", 240),
            )
            db.add(org)
            db.flush()
            chapter = AlbumChapter(
                album_id=album.id,
                org_id=org.id,
                sort_order=order,
                mosaic_image_url=save_webp(_poster(name, color, rng), f"orgs/{name}/mosaico", 1200),
            )
            db.add(chapter)
            db.flush()
            chapters.append(chapter)
            for page_no, template in enumerate(PAGE_TEMPLATES, start=1):
                for size, x, y, w, h in template:
                    person = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                    role = rng.choice(ROLES)
                    rarity = "lendária" if size == "G" and page_no == 1 else "rara" if size == "G" or rng.random() < 0.2 else "comum"
                    db.add(
                        Sticker(
                            album_id=album.id,
                            org_id=org.id,
                            number=number,
                            name=person,
                            image_url=save_webp(_portrait(person, color, size, rng), f"orgs/{name}/figurinhas/{number:03d} {person}", 640),
                            size=size,
                            rarity_id=rarities[rarity].id,
                            tag="gestão" if role == "Gestão" else "membro",
                            hint=f"Procure alguém de {role.lower()}",
                            back_text=f"{person} faz parte de {role.lower()} em {name}. Dado fictício para teste.",
                            back_links=[{"label": "Instagram da org", "url": "https://example.com/org"}],
                            page_no=page_no,
                            slot_layout={"x": x, "y": y, "w": w, "h": h},
                        )
                    )
                    number += 1
            db.flush()
            pieces = content.generate_mosaic(db, chapter, 2, 3, rarities["comum"].id)
            number += len(pieces)

        # A senha de dev está no README; online ela seria pública, e o admin é criado à parte.
        plain = secrets.token_urlsafe(9) if settings.is_prod else "album12345"
        accounts = [("ana@example.com", "Ana", False), ("bia@example.com", "Bia", False)]
        if not settings.is_prod:
            accounts.insert(0, ("admin@example.com", "Admin", True))
            accounts.insert(1, ("editor@example.com", "Editor", False))
        password = hasher.hash(plain)
        verified = datetime.now(UTC)
        for email, nickname, is_admin in accounts:
            db.add(
                User(
                    email=email, nickname=nickname, password_hash=password, is_admin=is_admin,
                    email_verified_at=verified, university_id=university.id,
                )
            )

        db.flush()
        if not settings.is_prod:
            editor = db.scalar(select(User).where(User.email == "editor@example.com"))
            db.add_all(RoleGrant(user_id=editor.id, chapter_id=chapter.id) for chapter in chapters[:2])

        premium = album_pack_type(db, album.id, "premium")
        codes = [generate_code() for _ in range(3)]
        db.add_all(RedeemCode(code=code, album_id=album.id, pack_type_id=premium.id, label="biblioteca") for code in codes)
        db.commit()
    print(f"Conteúdo de teste criado. Contas (senha {plain}):", ", ".join(email for email, *_ in accounts))
    print("Códigos:", ", ".join(codes))
