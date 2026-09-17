"""Unidad del SII del emisor, para el impreso

El «Manual de Muestras Impresas» del SII pide, bajo el recuadro del tipo de
documento, «la Dirección Regional o Unidad del SII a la que pertenece el
emisor». No va en el XML, así que no se podía sacar de ningún sitio: todos los
impresos decían SANTIAGO, y CONSTRUCTORA DIMABE SPA pertenece a RANCAGUA.

Revision ID: f4b6d8a0c2e5
Revises: e3a5c7e9b1d4
Create Date: 2026-09-17 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b6d8a0c2e5"
down_revision: str | None = "e3a5c7e9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customer", sa.Column("issuer_sii_office", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("customer", "issuer_sii_office")
