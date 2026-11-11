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
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (Index("uq_user_nickname_ci", func.lower(nickname), unique=True),)


class UserSession(Base):
    __tablename__ = "user_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)


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

    org: Mapped[Org] = relationship(lazy="joined")

    __table_args__ = (UniqueConstraint("album_id", "org_id", name="uq_album_chapter_album_org"),)


class AlbumParticipation(Base):
    """Um jogador num álbum: guarda o timer da gaveta e, depois do fim, o resumo final."""

    __tablename__ = "album_participation"

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    album_id: Mapped[int] = mapped_column(ForeignKey("album.id", ondelete="CASCADE"), primary_key=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    next_pack_at: Mapped[datetime | None] = mapped_column(Timestamp)  # fim do ciclo atual; nulo só antes do primeiro acerto
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

    __table_args__ = (UniqueConstraint("album_id", "number", name="uq_sticker_album_number"),)


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
    redeemed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime | None] = mapped_column(Timestamp)

    pack_type: Mapped[PackType] = relationship(lazy="joined")


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
