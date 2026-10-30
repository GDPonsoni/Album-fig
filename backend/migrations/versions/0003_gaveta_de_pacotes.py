"""gaveta de pacotes: entrega por timer no lugar da virada do dia

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("next_pack_at", sa.DateTime(timezone=True)))
    op.execute("DELETE FROM config WHERE key = 'daily.pack_count'")
    op.execute("UPDATE config SET value = '3'::jsonb WHERE key = 'daily.stock_cap'")
    op.execute(
        "INSERT INTO config (key, value) VALUES "
        "('daily.refill_minutes', '720'::jsonb), ('daily.welcome_packs', '2'::jsonb)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM config WHERE key IN ('daily.refill_minutes', 'daily.welcome_packs')")
    op.execute("UPDATE config SET value = '4'::jsonb WHERE key = 'daily.stock_cap'")
    op.execute("INSERT INTO config (key, value) VALUES ('daily.pack_count', '2'::jsonb)")
    op.drop_column("user", "next_pack_at")
