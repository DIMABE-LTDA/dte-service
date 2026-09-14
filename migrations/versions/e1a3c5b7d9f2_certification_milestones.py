"""Hitos de la postulación: los pasos que ocurren fuera del servicio

Revision ID: e1a3c5b7d9f2
Revises: d0f2a4c6e8b1
Create Date: 2026-09-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a3c5b7d9f2"
down_revision: str | None = "d0f2a4c6e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # De los seis pasos del trámite el servicio sólo conoce el primero; los otros
    # cinco ocurren en el sitio del SII o por correo y los confirma el operador.
    op.create_table(
        "certification_milestone",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.String(30), nullable=False),
        sa.Column("done_at", sa.Date(), nullable=True),
        sa.Column("note", sa.String(), nullable=False, server_default=""),
        sa.UniqueConstraint("customer_id", "step"),
    )


def downgrade() -> None:
    op.drop_table("certification_milestone")
