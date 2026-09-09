"""Desglose del SII por tipo de documento en cada envío

`EPR` significa que el sobre se procesó, no que sus documentos se aceptaran. El
expediente pintaba EPR en verde, y un set con sus 28 documentos rechazados
dentro se leyó como aceptado durante una semana. El desglose que el SII ya
devolvía —informados, aceptados, rechazados, con reparos, por tipo— se estaba
descartando al parsear la respuesta.

Revision ID: c5e7a9b1d3f6
Revises: b4d6f8a0c2e5
Create Date: 2026-09-09 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "b4d6f8a0c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("certification_submission", sa.Column("sii_stats", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("certification_submission", "sii_stats")
