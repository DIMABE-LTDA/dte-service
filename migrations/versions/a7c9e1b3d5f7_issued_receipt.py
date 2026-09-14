"""issued_receipt: boletas guardadas para la consulta pública del consumidor

El SII exige que la boleta impresa indique un sitio donde el comprador pueda
recuperarla. Es el único tipo de documento que el servicio almacena.

Revision ID: a7c9e1b3d5f7
Revises: f6a8b0d2c4e5
Create Date: 2026-09-01 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c9e1b3d5f7"
down_revision: str | None = "f6a8b0d2c4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issued_receipt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.Integer(), nullable=False),
        sa.Column("folio", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("xml_encrypted", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id", "doc_type", "folio"),
    )
    # La consulta pública filtra por folio + fecha; el monto se compara aparte.
    op.create_index(
        "ix_issued_receipt_lookup",
        "issued_receipt",
        ["customer_id", "folio", "issue_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_issued_receipt_lookup", table_name="issued_receipt")
    op.drop_table("issued_receipt")
