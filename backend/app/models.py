import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")
Timestamp = DateTime(timezone=True)


def _enum(*values: str, name: str) -> Enum:
    return Enum(*values, name=name, native_enum=False, create_constraint=True, length=20)


class Base(DeclarativeBase):
    pass


class University(Base):
    __tablename__ = "university"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Album(Base):
    """Álbum sazonal de uma universidade. Só `draft|ready` é armazenado: agendado,
    ativo e encerrado saem das datas (ver services/albums.album_status)."""

    __tablename__ = "album"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("university.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    starts_at: Mapped[datetime] = mapped_column(Timestamp)
    ends_at: Mapped[datetime] = mapped_column(Timestamp)
    state: Mapped[str] = mapped_column(_enum("draft", "ready", name="album_state"), default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    # A exclusão "um álbum pronto por vez por universidade" (ex_album_ready_overlap) é criada na migration 0004.
    __table_args__ = (CheckConstraint("ends_at > starts_at", name="ck_album_dates"),)


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("university.id", ondelete="RESTRICT"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(Timestamp)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(20))
    phone: Mapped[str | None] = mapped_column(String(30))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Verificação em duas etapas (TOTP). Só vale depois de confirmada (`totp_enabled_at`).
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled_at: Mapped[datetime | None] = mapped_column(Timestamp)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)  # último passo aceito: código não vale duas vezes
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (Index("uq_user_nickname_ci", func.lower(nickname), unique=True),)


class UserSession(Base):
    __tablename__ = "user_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    mfa_at: Mapped[datetime | None] = mapped_column(Timestamp)  # quando esta sessão confirmou o segundo fator


class RecoveryCode(Base):
    """Código de recuperação do 2FA. Só o hash é guardado; cada um vale uma vez."""

    __tablename__ = "recovery_code"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(Timestamp)


class EmailToken(Base):
    __tablename__ = "email_token"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(_enum("confirm", "reset", name="email_token_kind"))
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    used_at: Mapped[datetime | None] = mapped_column(Timestamp)


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (Index("ix_rate_limit_key_time", "key", "created_at"),)


class Org(Base):
    __tablename__ = "org"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("university.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    logo_url: Mapped[str | None] = mapped_column(String(500))
    theme_color: Mapped[str] = mapped_column(String(9), default="#1d4ed8", server_default="#1d4ed8")


class AlbumChapter(Base):
    """O capítulo de uma org dentro de um álbum. Ordem e mosaico são do capítulo, não da org."""

    __tablename__ = "album_chapter"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("org.id", ondelete="RESTRICT"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    mosaic_image_url: Mapped[str | None] = mapped_column(String(500))
    mosaic_cols: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    mosaic_rows: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    # Publicado por revisão: {"orientation": "portrait|landscape", "pages": [{page_no, title, text, background_url, layout}]}
    layout: Mapped[dict[str, Any]] = mapped_column(Json, default=dict, server_default="{}")
    published_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapter_revision.id", ondelete="SET NULL", use_alter=True, name="album_chapter_published_revision_id_fkey")
    )

    org: Mapped[Org] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("album_id", "org_id", name="uq_album_chapter_album_org"),)


class AlbumParticipation(Base):
    """Um jogador num álbum: guarda o timer da gaveta e, depois do fim, o resumo final."""

    __tablename__ = "album_participation"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="CASCADE"), primary_key=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    next_pack_at: Mapped[datetime | None] = mapped_column(Timestamp)  # fim do ciclo atual; nulo antes do primeiro acerto e com a gaveta cheia (timer parado)
    last_daily_grant_on: Mapped[date | None] = mapped_column(Date)
    final_stats: Mapped[dict[str, Any] | None] = mapped_column(Json)  # preenchido uma vez, no fechamento


class AlbumConfig(Base):
    """Sobrescrita de um parâmetro do `Config` global para um álbum."""

    __tablename__ = "album_config"

    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(Json)


class Rarity(Base):
    __tablename__ = "rarity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    rank: Mapped[int] = mapped_column(Integer, unique=True)  # maior = mais rara
    weight: Mapped[float] = mapped_column(default=1)
    color: Mapped[str] = mapped_column(String(9), default="#888888", server_default="#888888")


class Sticker(Base):
    __tablename__ = "sticker"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="RESTRICT"), index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("org.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # único dentro do álbum
    # Identidade estável da figurinha no conteúdo do editor: liga `stickers[].key` da revisão à linha de produção.
    content_key: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120))
    image_url: Mapped[str | None] = mapped_column(String(500))
    size: Mapped[str] = mapped_column(_enum("P", "M", "G", name="sticker_size"), default="P")
    rarity_id: Mapped[int] = mapped_column(ForeignKey("rarity.id", ondelete="RESTRICT"))
    tag: Mapped[str] = mapped_column(String(40), default="membro", server_default="membro")
    hint: Mapped[str] = mapped_column(String(160), default="", server_default="")
    back_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    back_links: Mapped[list[dict[str, str]]] = mapped_column(Json, default=list)
    page_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    slot_layout: Mapped[dict[str, float]] = mapped_column(Json, default=dict)  # x, y, w, h em %
    is_mosaic_piece: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    mosaic_index: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    rarity: Mapped[Rarity] = relationship(lazy="joined")
    org: Mapped[Org] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("album_id", "number", name="uq_sticker_album_number"),
        UniqueConstraint("album_id", "content_key", name="uq_sticker_album_content_key"),
    )


class PackType(Base):
    __tablename__ = "pack_type"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Nulo = modelo global, copiado (com os pesos explícitos) para cada álbum novo.
    album_id: Mapped[int | None] = mapped_column(ForeignKey("album.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    stickers_per_pack: Mapped[int] = mapped_column(Integer, default=6)
    # {"<rarity_id>": peso}. Nos tipos de um álbum vem completo; raridade ausente usa rarity.weight.
    rarity_weights: Mapped[dict[str, float]] = mapped_column(Json, default=dict)
    guaranteed_min_rarity: Mapped[int | None] = mapped_column(ForeignKey("rarity.id", ondelete="SET NULL"))
    org_filter: Mapped[int | None] = mapped_column(ForeignKey("org.id", ondelete="SET NULL"))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    __table_args__ = (
        Index("uq_pack_type_album_slug", "album_id", "slug", unique=True, postgresql_where=text("album_id IS NOT NULL")),
        Index("uq_pack_type_template_slug", "slug", unique=True, postgresql_where=text("album_id IS NULL")),
    )


class PackGrant(Base):
    __tablename__ = "pack_grant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="RESTRICT"), index=True)
    pack_type_id: Mapped[int] = mapped_column(ForeignKey("pack_type.id", ondelete="RESTRICT"))
    source: Mapped[str] = mapped_column(_enum("daily", "code", "admin", name="pack_source"))
    source_ref: Mapped[str | None] = mapped_column(String(120))
    # Quem gerou o pacote: quem criou o código ou quem concedeu. Nulo = sistema (gaveta diária).
    origin_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    granted_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    opened_at: Mapped[datetime | None] = mapped_column(Timestamp)

    pack_type: Mapped[PackType] = relationship(lazy="joined")

    __table_args__ = (Index("ix_pack_grant_user_open", "user_id", "opened_at"),)


class UserSticker(Base):
    __tablename__ = "user_sticker"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    sticker_id: Mapped[int] = mapped_column(ForeignKey("sticker.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    pasted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pasted_at: Mapped[datetime | None] = mapped_column(Timestamp)

    sticker: Mapped[Sticker] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_user_sticker_quantity"),)


class PackOpeningItem(Base):
    __tablename__ = "pack_opening_item"

    pack_grant_id: Mapped[int] = mapped_column(ForeignKey("pack_grant.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    sticker_id: Mapped[int] = mapped_column(ForeignKey("sticker.id", ondelete="RESTRICT"))
    is_new: Mapped[bool] = mapped_column(Boolean)


class RedeemCode(Base):
    __tablename__ = "redeem_code"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="RESTRICT"), index=True)
    pack_type_id: Mapped[int] = mapped_column(ForeignKey("pack_type.id", ondelete="RESTRICT"))
    label: Mapped[str] = mapped_column(String(80), default="", server_default="")
    expires_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    redeemed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime | None] = mapped_column(Timestamp)

    pack_type: Mapped[PackType] = relationship(lazy="joined")


class StickerCopy(Base):
    """Um exemplar físico de figurinha, do sorteio até onde está hoje. `user_sticker` segue sendo o
    inventário do jogo (quantidade); aqui fica a origem de cada exemplar e por quem ele passou."""

    __tablename__ = "sticker_copy"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sticker_id: Mapped[int] = mapped_column(ForeignKey("sticker.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    # Pacote e posição em que saiu. Nulo = exemplar anterior ao rastreio ou conta/pacote apagado.
    origin_grant_id: Mapped[int | None] = mapped_column(ForeignKey("pack_grant.id", ondelete="SET NULL"), index=True)
    origin_position: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (Index("ix_sticker_copy_owner_sticker", "owner_id", "sticker_id"),)


class StickerCopyEvent(Base):
    """Cadeia de custódia de um exemplar: sorteio, trocas e colagem. Só se acrescenta."""

    __tablename__ = "sticker_copy_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey("sticker_copy.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(_enum("drawn", "traded", "pasted", "legacy", name="copy_event"))
    from_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    to_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    pack_grant_id: Mapped[int | None] = mapped_column(ForeignKey("pack_grant.id", ondelete="SET NULL"))
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trade.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Trade(Base):
    __tablename__ = "trade"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="RESTRICT"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(
        _enum("open", "active", "done", "cancelled", "expired", name="trade_state"), default="open", index=True
    )
    offer_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    creator_accepted_version: Mapped[int | None] = mapped_column(Integer)
    partner_accepted_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    joined_at: Mapped[datetime | None] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)

    creator: Mapped[User] = relationship(foreign_keys=[creator_id], lazy="joined")
    partner: Mapped[User | None] = relationship(foreign_keys=[partner_id], lazy="joined")


class TradeOffer(Base):
    __tablename__ = "trade_offer"

    trade_id: Mapped[int] = mapped_column(ForeignKey("trade.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    sticker_id: Mapped[int] = mapped_column(ForeignKey("sticker.id", ondelete="RESTRICT"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)

    sticker: Mapped[Sticker] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("quantity > 0", name="ck_trade_offer_quantity"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(Json)


class RoleGrant(Base):
    """Papel (editor, revisor ou promotor) numa pessoa. Conferido no banco a cada requisição: revogar vale na hora."""

    __tablename__ = "role_grant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    # Nulo só no promotor, cujo papel vale para o álbum todo e não para um capítulo.
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("album_chapter.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(_enum("editor", "reviewer", "promoter", name="role_grant_role"), default="editor")
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(Timestamp)
    revoked_at: Mapped[datetime | None] = mapped_column(Timestamp)

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="joined")


class ChapterRevision(Base):
    """Proposta de conteúdo de um capítulo. É a única coisa (com `asset`) que o editor escreve;
    só a publicação pelo superadmin leva o `content` para as tabelas de produção."""

    __tablename__ = "chapter_revision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("album_chapter.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        _enum("draft", "submitted", "approved", "rejected", "published", "superseded", name="chapter_revision_status"),
        default="draft",
    )
    content: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(Timestamp)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    review_note: Mapped[str] = mapped_column(Text, default="", server_default="")
    base_published_revision_id: Mapped[int | None] = mapped_column(ForeignKey("chapter_revision.id", ondelete="SET NULL"))
    published_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # O que estava em produção logo antes desta publicação: é o que o rollback reaplica.
    previous_content: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # Ids de quem salvou este rascunho (editores distintos, na ordem em que apareceram).
    contributors: Mapped[list[int]] = mapped_column(Json, default=list)

    __table_args__ = (
        UniqueConstraint("chapter_id", "number", name="uq_chapter_revision_number"),
        # Uma revisão em aberto por capítulo (rascunho, enviada, aprovada ou rejeitada aguardando ajuste).
        Index(
            "uq_chapter_revision_open", "chapter_id", unique=True,
            postgresql_where=text("status IN ('draft', 'submitted', 'approved', 'rejected')"),
        ),
    )


class Asset(Base):
    """Imagem enviada por um editor. Fica privada (`pending`) até a publicação."""

    __tablename__ = "asset"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("album_chapter.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    key: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(500), index=True)  # álbum duplicado compartilha a imagem: não é único
    kind: Mapped[str] = mapped_column(String(20), default="sticker", server_default="sticker")
    bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(_enum("pending", "approved", name="asset_status"), default="pending")
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
