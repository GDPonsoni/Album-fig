"""papel de promotor e rastreio de origem das figurinhas

- `role_grant`: promotor vale para o álbum todo (sem capítulo).
- `redeem_code.created_by` e `pack_grant.origin_user_id`: quem gerou o código/pacote.
- `sticker_copy` e `sticker_copy_event`: cada exemplar de figurinha, de qual pacote saiu e por quem passou.
- `chapter_revision.contributors`: quem salvou o rascunho.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
CONFIG = {"promo.codes_per_day": 20}


def upgrade() -> None:
    op.alter_column("role_grant", "chapter_id", existing_type=sa.BigInteger(), nullable=True)
    op.drop_constraint("role_grant_role", "role_grant", type_="check")
    op.create_check_constraint("role_grant_role", "role_grant", "role IN ('editor', 'reviewer', 'promoter')")
    op.create_check_constraint("ck_role_grant_scope", "role_grant", "(role = 'promoter') = (chapter_id IS NULL)")

    op.add_column("redeem_code", sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")))
    op.add_column("redeem_code", sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False))
    op.create_index("ix_redeem_code_created_by", "redeem_code", ["created_by"])
    op.add_column("pack_grant", sa.Column("origin_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")))
    op.create_index("ix_pack_grant_origin_user_id", "pack_grant", ["origin_user_id"])
    op.add_column("chapter_revision", sa.Column("contributors", JSONB(), server_default="[]", nullable=False))

    op.create_table(
        "sticker_copy",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("sticker_id", sa.BigInteger(), sa.ForeignKey("sticker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("origin_grant_id", sa.BigInteger(), sa.ForeignKey("pack_grant.id", ondelete="SET NULL")),
        sa.Column("origin_position", sa.Integer()),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sticker_copy_sticker_id", "sticker_copy", ["sticker_id"])
    op.create_index("ix_sticker_copy_owner_id", "sticker_copy", ["owner_id"])
    op.create_index("ix_sticker_copy_origin_grant_id", "sticker_copy", ["origin_grant_id"])
    op.create_index("ix_sticker_copy_owner_sticker", "sticker_copy", ["owner_id", "sticker_id"])
    op.create_table(
        "sticker_copy_event",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("copy_id", sa.BigInteger(), sa.ForeignKey("sticker_copy.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "event", sa.Enum("drawn", "traded", "pasted", "legacy", name="copy_event", native_enum=False, create_constraint=True, length=20),
            nullable=False,
        ),
        sa.Column("from_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("to_user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("pack_grant_id", sa.BigInteger(), sa.ForeignKey("pack_grant.id", ondelete="SET NULL")),
        sa.Column("trade_id", sa.BigInteger(), sa.ForeignKey("trade.id", ondelete="SET NULL")),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sticker_copy_event_copy_id", "sticker_copy_event", ["copy_id"])
    op.create_index("ix_sticker_copy_event_to_user_id", "sticker_copy_event", ["to_user_id"])
    op.create_index("ix_sticker_copy_event_trade_id", "sticker_copy_event", ["trade_id"])

    config = sa.table("config", sa.column("key", sa.String), sa.column("value", JSONB))
    op.bulk_insert(config, [{"key": key, "value": value} for key, value in CONFIG.items()])
    _backfill(op.get_bind())


def _backfill(conn) -> None:
    """Cria um exemplar para cada unidade já em inventário. Onde há pacote aberto com aquela figurinha
    pela mesma pessoa, o exemplar herda a origem; o resto fica sem origem ('legacy'): as trocas antigas
    não dizem qual exemplar mudou de mão."""
    rows = conn.execute(sa.text("SELECT user_id, sticker_id, quantity FROM user_sticker WHERE quantity > 0")).all()
    for user_id, sticker_id, quantity in rows:
        origins = conn.execute(
            sa.text(
                "SELECT i.pack_grant_id, i.position FROM pack_opening_item i JOIN pack_grant g ON g.id = i.pack_grant_id "
                "WHERE g.user_id = :u AND i.sticker_id = :s ORDER BY g.opened_at, i.position LIMIT :q"
            ),
            {"u": user_id, "s": sticker_id, "q": quantity},
        ).all()
        for index in range(quantity):
            grant_id, position = origins[index] if index < len(origins) else (None, None)
            copy_id = conn.execute(
                sa.text(
                    "INSERT INTO sticker_copy (sticker_id, owner_id, origin_grant_id, origin_position) "
                    "VALUES (:s, :u, :g, :p) RETURNING id"
                ),
                {"s": sticker_id, "u": user_id, "g": grant_id, "p": position},
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO sticker_copy_event (copy_id, event, to_user_id, pack_grant_id) VALUES (:c, :e, :u, :g)"
                ),
                {"c": copy_id, "e": "drawn" if grant_id else "legacy", "u": user_id, "g": grant_id},
            )


def downgrade() -> None:
    op.execute("DELETE FROM config WHERE key = 'promo.codes_per_day'")
    op.drop_table("sticker_copy_event")
    op.drop_table("sticker_copy")
    op.drop_column("chapter_revision", "contributors")
    op.drop_index("ix_pack_grant_origin_user_id", "pack_grant")
    op.drop_column("pack_grant", "origin_user_id")
    op.drop_index("ix_redeem_code_created_by", "redeem_code")
    op.drop_column("redeem_code", "created_at")
    op.drop_column("redeem_code", "created_by")
    op.execute("DELETE FROM role_grant WHERE role = 'promoter'")
    op.drop_constraint("ck_role_grant_scope", "role_grant", type_="check")
    op.drop_constraint("role_grant_role", "role_grant", type_="check")
    op.create_check_constraint("role_grant_role", "role_grant", "role IN ('editor', 'reviewer')")
    op.alter_column("role_grant", "chapter_id", existing_type=sa.BigInteger(), nullable=False)
