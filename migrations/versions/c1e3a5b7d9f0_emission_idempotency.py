"""Idempotencia de la emisión y archivo de los DTE emitidos

Un corte de red después de emitir dejaba el folio consumido, el documento en
el SII y al ERP sin respuesta: al reintentar se emitía un duplicado. La clave
de idempotencia permite que el reintento devuelva lo ya emitido.

Y el sobre sólo se guardaba en certificación: en producción la única copia
viajaba en la respuesta HTTP.

Revision ID: c1e3a5b7d9f0
Revises: b8d0f2a4c6e8
Create Date: 2026-09-21 12:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1e3a5b7d9f0"
down_revision: str | None = "b8d0f2a4c6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emission_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(40), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("response_encrypted", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("customer_id", "key"),
    )
    op.create_table(
        "issued_document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.Integer(), nullable=False),
        sa.Column("folio", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.String(32), nullable=True),
        sa.Column("xml_encrypted", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("customer_id", "doc_type", "folio"),
    )


def downgrade() -> None:
    op.drop_table("issued_document")
    op.drop_table("emission_request")
