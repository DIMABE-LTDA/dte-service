"""TOTP: recordar el último paso usado para que un código no se reutilice

Revision ID: c9e1a3b5d7f0
Revises: b8d0f2a4c6e9
Create Date: 2026-09-07 19:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e1a3b5d7f0"
down_revision: str | None = "b8d0f2a4c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Un TOTP es de un solo uso. Sin recordar el paso ya gastado, el mismo código
    # sirve durante toda su ventana de validez y el segundo factor deja de ser
    # "un uso" para ser "minuto y medio de barra libre".
    op.add_column("app_user", sa.Column("totp_last_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("app_user", "totp_last_step")
