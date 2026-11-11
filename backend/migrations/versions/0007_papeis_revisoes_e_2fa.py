"""papéis de editor, revisões de capítulo, ativos e verificação em duas etapas

O editor só escreve em `chapter_revision` e `asset`. A publicação, feita pelo
superadmin, é o único caminho do conteúdo para as tabelas de produção.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
CONFIG = {"rate.asset_upload_per_hour": 120, "rate.publish_per_hour": 60}


def upgrade() -> None:
    op.add_column("user", sa.Column("totp_secret", sa.String(64)))
    op.add_column("user", sa.Column("totp_enabled_at", TS))
    op.add_column("user", sa.Column("totp_last_step", sa.BigInteger()))
    op.add_column("user_session", sa.Column("mfa_at", TS))
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", TS),
    )
    op.create_index("ix_recovery_code_user_id", "recovery_code", ["user_id"])

    op.create_table(
        "role_grant",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", sa.BigInteger(), sa.ForeignKey("album_chapter.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("granted_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", TS),
        sa.Column("revoked_at", TS),
        sa.CheckConstraint("role IN ('editor')", name="role_grant_role"),
    )
    op.create_index("ix_role_grant_user_id", "role_grant", ["user_id"])
    op.create_index("ix_role_grant_chapter_id", "role_grant", ["chapter_id"])

    op.create_table(
        "chapter_revision",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("chapter_id", sa.BigInteger(), sa.ForeignKey("album_chapter.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("submitted_at", TS),
        sa.Column("reviewed_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", TS),
        sa.Column("review_note", sa.Text(), server_default="", nullable=False),
        sa.Column("base_published_revision_id", sa.BigInteger(), sa.ForeignKey("chapter_revision.id", ondelete="SET NULL")),
        sa.Column("published_at", TS),
        sa.Column("previous_content", JSONB()),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'rejected', 'published', 'superseded')", name="chapter_revision_status"
        ),
        sa.UniqueConstraint("chapter_id", "number", name="uq_chapter_revision_number"),
    )
    op.create_index("ix_chapter_revision_chapter_id", "chapter_revision", ["chapter_id"])
    op.create_index(
        "uq_chapter_revision_open", "chapter_revision", ["chapter_id"], unique=True,
        postgresql_where=sa.text("status IN ('draft', 'submitted', 'approved', 'rejected')"),
    )

    op.create_table(
        "asset",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("chapter_id", sa.BigInteger(), sa.ForeignKey("album_chapter.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(20), server_default="sticker", nullable=False),
        sa.Column("bytes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved')", name="asset_status"),
    )
    op.create_index("ix_asset_chapter_id", "asset", ["chapter_id"])
    op.create_index("ix_asset_url", "asset", ["url"])

    op.add_column("album_chapter", sa.Column("layout", JSONB(), server_default="{}", nullable=False))
    op.add_column("album_chapter", sa.Column("published_revision_id", sa.BigInteger()))
    op.create_foreign_key(
        "album_chapter_published_revision_id_fkey", "album_chapter", "chapter_revision",
        ["published_revision_id"], ["id"], ondelete="SET NULL",
    )

    # Toda figurinha ganha a chave estável que liga o conteúdo do editor à linha de produção.
    op.add_column("sticker", sa.Column("content_key", sa.String(36)))
    op.execute("UPDATE sticker SET content_key = gen_random_uuid()::text")
    op.alter_column("sticker", "content_key", nullable=False)
    op.create_unique_constraint("uq_sticker_album_content_key", "sticker", ["album_id", "content_key"])

    config = sa.table("config", sa.column("key", sa.String), sa.column("value", JSONB))
    op.bulk_insert(config, [{"key": key, "value": value} for key, value in CONFIG.items()])


def downgrade() -> None:
    op.execute("DELETE FROM config WHERE key IN ('rate.asset_upload_per_hour', 'rate.publish_per_hour')")
    op.drop_constraint("uq_sticker_album_content_key", "sticker", type_="unique")
    op.drop_column("sticker", "content_key")
    op.drop_constraint("album_chapter_published_revision_id_fkey", "album_chapter", type_="foreignkey")
    op.drop_column("album_chapter", "published_revision_id")
    op.drop_column("album_chapter", "layout")
    op.drop_table("asset")
    op.drop_table("chapter_revision")
    op.drop_table("role_grant")
    op.drop_table("recovery_code")
    op.drop_column("user_session", "mfa_at")
    op.drop_column("user", "totp_last_step")
    op.drop_column("user", "totp_enabled_at")
    op.drop_column("user", "totp_secret")
