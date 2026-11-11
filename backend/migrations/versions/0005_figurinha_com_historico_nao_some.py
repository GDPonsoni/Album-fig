"""figurinha com inventário, abertura ou oferta não pode ser apagada

Depois que um álbum começa, o histórico dele é permanente: apagar uma figurinha
não pode mais levar junto o inventário e as aberturas de pacote de todo mundo.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ("user_sticker", "pack_opening_item", "trade_offer")


def _swap(ondelete: str) -> None:
    for table in TABLES:
        op.drop_constraint(f"{table}_sticker_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(f"{table}_sticker_id_fkey", table, "sticker", ["sticker_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _swap("RESTRICT")


def downgrade() -> None:
    _swap("CASCADE")
