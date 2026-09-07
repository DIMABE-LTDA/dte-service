"""Segundo factor del portal: secreto TOTP y códigos de recuperación

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f7
Create Date: 2026-09-07 17:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d0f2a4c6e9"
down_revision: str | None = "a7c9e1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # El secreto va cifrado con Fernet, como el resto del material sensible.
    op.add_column("app_user", sa.Column("totp_secret", sa.String(), nullable=True))
    # Se separa de tener secreto: entre pedir el alta y confirmar el primer
    # código hay un estado intermedio en el que NO debe exigirse todavía.
    op.add_column(
        "app_user",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_recovery_code_user", "recovery_code", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_recovery_code_user", table_name="recovery_code")
    op.drop_table("recovery_code")
    op.drop_column("app_user", "totp_enabled")
    op.drop_column("app_user", "totp_secret")
