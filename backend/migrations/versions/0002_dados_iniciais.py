"""parâmetros, raridades e tipos de pacote iniciais (seção 7 dos requisitos)

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CONFIG = {
    "daily.timezone": "America/Sao_Paulo",
    "daily.pack_count": 2,
    "daily.common_pack_slug": "comum",
    "daily.premium_pack_slug": "premium",
    "daily.premium_every_n_days": 2,
    "daily.stock_cap": 4,
    "trade.link_ttl_seconds": 600,
    "trade.session_ttl_seconds": 300,
    "code.max_errors_per_hour": 10,
    "rate.login_per_hour": 20,
    "rate.register_per_hour": 300,  # por IP: o Wi-Fi do campus costuma sair por um IP só
    "rate.email_per_hour": 5,
    "rate.trade_create_per_hour": 30,
}


def upgrade() -> None:
    config = sa.table("config", sa.column("key", sa.String), sa.column("value", JSONB))
    op.bulk_insert(config, [{"key": key, "value": value} for key, value in CONFIG.items()])

    rarity = sa.table(
        "rarity",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("rank", sa.Integer),
        sa.column("weight", sa.Float),
        sa.column("color", sa.String),
    )
    op.bulk_insert(
        rarity,
        [
            {"id": 1, "name": "comum", "rank": 1, "weight": 80, "color": "#6b7280"},
            {"id": 2, "name": "rara", "rank": 2, "weight": 17, "color": "#2563eb"},
            {"id": 3, "name": "lendária", "rank": 3, "weight": 3, "color": "#d97706"},
        ],
    )
    op.execute("SELECT setval(pg_get_serial_sequence('rarity', 'id'), 3)")

    pack_type = sa.table(
        "pack_type",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("stickers_per_pack", sa.Integer),
        sa.column("rarity_weights", JSONB),
        sa.column("guaranteed_min_rarity", sa.BigInteger),
        sa.column("config", JSONB),
    )
    op.bulk_insert(
        pack_type,
        [
            {
                "slug": "comum",
                "name": "Pacote comum",
                "stickers_per_pack": 6,
                "rarity_weights": {},
                "guaranteed_min_rarity": None,
                "config": {},
            },
            {
                "slug": "premium",
                "name": "Pacote premium",
                "stickers_per_pack": 7,
                "rarity_weights": {"1": 60, "2": 32, "3": 8},
                "guaranteed_min_rarity": 2,
                "config": {},
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM pack_type")
    op.execute("DELETE FROM rarity")
    op.execute("DELETE FROM config")
