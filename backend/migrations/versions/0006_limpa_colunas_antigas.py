"""remove as colunas que a 0004 deixou sem uso

O timer da gaveta mora na participação do álbum e a ordem e o mosaico moram no
capítulo. As colunas antigas de `user` e `org` só existiam para a 0004 poder ser
revertida sem perda enquanto a Fase 1 era validada.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user", "next_pack_at")
    op.drop_column("user", "last_daily_grant_on")
    for column in ("sort_order", "mosaic_image_url", "mosaic_cols", "mosaic_rows"):
        op.drop_column("org", column)


def downgrade() -> None:
    op.add_column("user", sa.Column("last_daily_grant_on", sa.Date()))
    op.add_column("user", sa.Column("next_pack_at", sa.DateTime(timezone=True)))
    op.add_column("org", sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False))
    op.add_column("org", sa.Column("mosaic_image_url", sa.String(500)))
    op.add_column("org", sa.Column("mosaic_cols", sa.Integer(), server_default="2", nullable=False))
    op.add_column("org", sa.Column("mosaic_rows", sa.Integer(), server_default="3", nullable=False))
    # Os valores voltam do álbum mais recente de cada usuário e de cada org.
    op.execute(
        """
        UPDATE "user" u SET next_pack_at = p.next_pack_at, last_daily_grant_on = p.last_daily_grant_on
        FROM (
            SELECT DISTINCT ON (p.user_id) p.user_id, p.next_pack_at, p.last_daily_grant_on
            FROM album_participation p JOIN album a ON a.id = p.album_id
            ORDER BY p.user_id, a.starts_at DESC
        ) p WHERE p.user_id = u.id
        """
    )
    op.execute(
        """
        UPDATE org o SET sort_order = c.sort_order, mosaic_image_url = c.mosaic_image_url,
                         mosaic_cols = c.mosaic_cols, mosaic_rows = c.mosaic_rows
        FROM (
            SELECT DISTINCT ON (c.org_id) c.org_id, c.sort_order, c.mosaic_image_url, c.mosaic_cols, c.mosaic_rows
            FROM album_chapter c JOIN album a ON a.id = c.album_id
            ORDER BY c.org_id, a.starts_at DESC
        ) c WHERE c.org_id = o.id
        """
    )
