"""Vigencia y ambiente de cada CAF

La Res. Ex. SII N° 58 de 2017 da a los CAF de documentos con derecho a crédito
fiscal seis meses de validez desde su autorización, y el SII rechaza en la
recepción los documentos con folios de un CAF vencido. Y un CAF de
certificación (IDK 100) usado en producción produce el mismo rechazo. Se
guardan la fecha, el vencimiento y el IDK para que el asignador no entregue
esos folios.

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1b3d5f6
Create Date: 2026-09-17 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d0f2a4c6e8"
down_revision: str | None = "a7c9e1b3d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("caf", sa.Column("authorized_on", sa.Date(), nullable=True))
    op.add_column("caf", sa.Column("expires_on", sa.Date(), nullable=True))
    op.add_column("caf", sa.Column("key_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("caf", "key_id")
    op.drop_column("caf", "expires_on")
    op.drop_column("caf", "authorized_on")
