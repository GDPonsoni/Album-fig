"""álbuns sazonais: universidade, álbum, capítulo, participação e config por álbum

O que existia (uma campanha global) vira o álbum "Calourada" da universidade
padrão. Nenhum dado é apagado: as colunas antigas de `user` (timer da gaveta) e
de `org` (ordem e mosaico) ficam sem uso até a migration 0006.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
ALBUM_TABLES = ("sticker", "pack_grant", "redeem_code", "trade")


def upgrade() -> None:
    # A exclusão por universidade + intervalo precisa de igualdade em bigint dentro de um índice GiST.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "university",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_table(
        "album",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("university_id", sa.BigInteger(), sa.ForeignKey("university.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("cover_url", sa.String(500)),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("ends_at", TS, nullable=False),
        sa.Column("state", sa.String(20), server_default="draft", nullable=False),
        sa.Column("created_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state IN ('draft', 'ready')", name="album_state"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_album_dates"),
    )
    op.create_index("ix_album_university_id", "album", ["university_id"])
    # Um álbum pronto por vez por universidade: intervalos [início, fim) não podem se sobrepor.
    op.execute(
        "ALTER TABLE album ADD CONSTRAINT ex_album_ready_overlap "
        "EXCLUDE USING gist (university_id WITH =, tstzrange(starts_at, ends_at) WITH &&) WHERE (state = 'ready')"
    )
    op.create_table(
        "album_chapter",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("album_id", sa.BigInteger(), sa.ForeignKey("album.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("org.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("mosaic_image_url", sa.String(500)),
        sa.Column("mosaic_cols", sa.Integer(), server_default="2", nullable=False),
        sa.Column("mosaic_rows", sa.Integer(), server_default="3", nullable=False),
        sa.UniqueConstraint("album_id", "org_id", name="uq_album_chapter_album_org"),
    )
    op.create_index("ix_album_chapter_album_id", "album_chapter", ["album_id"])
    op.create_index("ix_album_chapter_org_id", "album_chapter", ["org_id"])
    op.create_table(
        "album_participation",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("album_id", sa.BigInteger(), sa.ForeignKey("album.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("joined_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("next_pack_at", TS),
        sa.Column("last_daily_grant_on", sa.Date()),
        sa.Column("final_stats", JSONB()),
    )
    op.create_index("ix_album_participation_album_id", "album_participation", ["album_id"])
    op.create_table(
        "album_config",
        sa.Column("album_id", sa.BigInteger(), sa.ForeignKey("album.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
    )

    op.add_column("user", sa.Column("university_id", sa.BigInteger()))
    op.add_column("org", sa.Column("university_id", sa.BigInteger()))
    op.add_column("pack_type", sa.Column("album_id", sa.BigInteger()))
    for table in ALBUM_TABLES:
        op.add_column(table, sa.Column("album_id", sa.BigInteger()))

    # ---- Backfill
    op.execute("INSERT INTO university (name, slug) VALUES ('Universidade Padrão', 'padrao')")
    op.execute('UPDATE "user" SET university_id = (SELECT id FROM university WHERE slug = \'padrao\')')
    op.execute("UPDATE org SET university_id = (SELECT id FROM university WHERE slug = 'padrao')")

    # Os pesos passam a ser explícitos e completos: mudar rarity.weight depois não altera pacote nenhum.
    op.execute(
        """
        UPDATE pack_type p SET rarity_weights = (
            SELECT COALESCE(jsonb_object_agg(r.id::text, COALESCE((p.rarity_weights -> r.id::text), to_jsonb(r.weight))), '{}'::jsonb)
            FROM rarity r
        )
        """
    )

    has_data = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM org) OR EXISTS (SELECT 1 FROM sticker) OR EXISTS (SELECT 1 FROM pack_grant) "
            "OR EXISTS (SELECT 1 FROM redeem_code) OR EXISTS (SELECT 1 FROM trade)"
        )
    )
    if has_data:
        op.execute(
            "INSERT INTO album (university_id, title, starts_at, ends_at, state) "
            "SELECT id, 'Calourada', now(), now() + interval '90 days', 'ready' FROM university WHERE slug = 'padrao'"
        )
        op.execute(
            "INSERT INTO album_chapter (album_id, org_id, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows) "
            "SELECT (SELECT id FROM album), id, sort_order, mosaic_image_url, mosaic_cols, mosaic_rows FROM org"
        )
        for table in ALBUM_TABLES:
            op.execute(f"UPDATE {table} SET album_id = (SELECT id FROM album)")
        # Os tipos de pacote existentes ficam com o álbum (os pacotes e códigos já apontam para eles)
        # e uma cópia permanece como modelo global para os próximos álbuns.
        op.execute("UPDATE pack_type SET album_id = (SELECT id FROM album)")
        # Todo usuário existente vira participante, levando o timer da gaveta.
        op.execute(
            "INSERT INTO album_participation (user_id, album_id, joined_at, next_pack_at, last_daily_grant_on) "
            'SELECT u.id, (SELECT id FROM album), u.created_at, u.next_pack_at, u.last_daily_grant_on FROM "user" u'
        )

    # ---- Unicidade por álbum
    op.drop_constraint("pack_type_slug_key", "pack_type", type_="unique")
    op.create_index(
        "uq_pack_type_album_slug", "pack_type", ["album_id", "slug"], unique=True, postgresql_where=sa.text("album_id IS NOT NULL")
    )
    op.create_index(
        "uq_pack_type_template_slug", "pack_type", ["slug"], unique=True, postgresql_where=sa.text("album_id IS NULL")
    )
    if has_data:
        op.execute(
            "INSERT INTO pack_type (album_id, slug, name, stickers_per_pack, rarity_weights, guaranteed_min_rarity, org_filter, config) "
            "SELECT NULL, slug, name, stickers_per_pack, rarity_weights, guaranteed_min_rarity, NULL, config "
            "FROM pack_type WHERE album_id IS NOT NULL"
        )
    op.drop_constraint("sticker_number_key", "sticker", type_="unique")
    op.create_unique_constraint("uq_sticker_album_number", "sticker", ["album_id", "number"])

    # ---- Só agora NOT NULL, chaves estrangeiras e índices
    op.alter_column("user", "university_id", nullable=False)
    op.alter_column("org", "university_id", nullable=False)
    op.create_foreign_key("user_university_id_fkey", "user", "university", ["university_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("org_university_id_fkey", "org", "university", ["university_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_user_university_id", "user", ["university_id"])
    op.create_index("ix_org_university_id", "org", ["university_id"])
    op.create_foreign_key("pack_type_album_id_fkey", "pack_type", "album", ["album_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_pack_type_album_id", "pack_type", ["album_id"])
    for table in ALBUM_TABLES:
        op.alter_column(table, "album_id", nullable=False)
        op.create_foreign_key(f"{table}_album_id_fkey", table, "album", ["album_id"], ["id"], ondelete="RESTRICT")
        op.create_index(f"ix_{table}_album_id", table, ["album_id"])


def downgrade() -> None:
    """Volta ao esquema de campanha única. Só é possível com no máximo um álbum:
    dois não cabem no modelo antigo (número de figurinha e slug de pacote únicos globais)."""
    if (op.get_bind().scalar(sa.text("SELECT count(*) FROM album")) or 0) > 1:
        raise RuntimeError("Há mais de um álbum; o esquema antigo só comporta uma campanha.")

    # O timer da gaveta e o mosaico voltam para as colunas antigas, que nunca foram apagadas.
    op.execute(
        'UPDATE "user" u SET next_pack_at = p.next_pack_at, last_daily_grant_on = p.last_daily_grant_on '
        "FROM album_participation p WHERE p.user_id = u.id"
    )
    op.execute(
        "UPDATE org o SET sort_order = c.sort_order, mosaic_image_url = c.mosaic_image_url, "
        "mosaic_cols = c.mosaic_cols, mosaic_rows = c.mosaic_rows FROM album_chapter c WHERE c.org_id = o.id"
    )

    for table in ALBUM_TABLES:
        op.drop_index(f"ix_{table}_album_id", table_name=table)
        op.drop_constraint(f"{table}_album_id_fkey", table, type_="foreignkey")
    op.drop_constraint("uq_sticker_album_number", "sticker", type_="unique")
    op.create_unique_constraint("sticker_number_key", "sticker", ["number"])

    # Sobra um tipo de pacote por slug: o do álbum (referenciado por pacotes e códigos) ou, sem álbum, o modelo.
    op.execute(
        "DELETE FROM pack_type t WHERE t.album_id IS NULL "
        "AND EXISTS (SELECT 1 FROM pack_type a WHERE a.slug = t.slug AND a.album_id IS NOT NULL)"
    )
    op.drop_index("ix_pack_type_album_id", table_name="pack_type")
    op.drop_constraint("pack_type_album_id_fkey", "pack_type", type_="foreignkey")
    op.drop_index("uq_pack_type_template_slug", table_name="pack_type")
    op.drop_index("uq_pack_type_album_slug", table_name="pack_type")
    op.create_unique_constraint("pack_type_slug_key", "pack_type", ["slug"])

    op.drop_index("ix_org_university_id", table_name="org")
    op.drop_index("ix_user_university_id", table_name="user")
    op.drop_constraint("org_university_id_fkey", "org", type_="foreignkey")
    op.drop_constraint("user_university_id_fkey", "user", type_="foreignkey")
    for table in ALBUM_TABLES:
        op.drop_column(table, "album_id")
    op.drop_column("pack_type", "album_id")
    op.drop_column("org", "university_id")
    op.drop_column("user", "university_id")

    op.drop_table("album_config")
    op.drop_table("album_participation")
    op.drop_table("album_chapter")
    op.drop_table("album")
    op.drop_table("university")
    # A extensão btree_gist fica: outras coisas no banco podem depender dela.
