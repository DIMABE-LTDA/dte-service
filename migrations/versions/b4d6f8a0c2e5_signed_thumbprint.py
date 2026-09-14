"""Con qué certificado se firmó cada sobre de certificación

La firma viaja dentro del XML: cambiar el certificado del cliente no la rehace.
Sin este dato no había forma de saber que un sobre iba firmado por un
certificado que ya no era el vigente, y el SII lo devolvía con RFR «error en
firma» —cierto en ese caso— mientras la guía del rechazo decía, con razón en
general, que casi nunca es la firma.

Los sobres anteriores quedan en NULL y no se bloquean: no se sabe con cuál se
firmaron, y un falso positivo impediría un envío legítimo.

Revision ID: b4d6f8a0c2e5
Revises: a3c5e7b9d1f4
Create Date: 2026-09-09 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d6f8a0c2e5"
down_revision: str | None = "a3c5e7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certification_submission",
        sa.Column("signed_thumbprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("certification_submission", "signed_thumbprint")
