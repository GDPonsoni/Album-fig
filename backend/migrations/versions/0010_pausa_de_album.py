"""pausa manual do álbum

- `album.paused_at`: quando o superadmin pausou. Nulo = não pausado.

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("album", sa.Column("paused_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("album", "paused_at")
