"""papel de revisor por capítulo

Revisor aprova, rejeita, publica e desfaz as revisões dos capítulos em que tem o papel
(com 2FA), sem ser superadmin. Quem concede o papel continua sendo só o superadmin.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("role_grant_role", "role_grant", type_="check")
    op.create_check_constraint("role_grant_role", "role_grant", "role IN ('editor', 'reviewer')")


def downgrade() -> None:
    op.execute("DELETE FROM role_grant WHERE role = 'reviewer'")
    op.drop_constraint("role_grant_role", "role_grant", type_="check")
    op.create_check_constraint("role_grant_role", "role_grant", "role IN ('editor')")
