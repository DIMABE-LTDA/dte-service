"""Envíos de boleta: TrackID y estado, para la cuadratura

La declaración de cumplimiento de boleta electrónica pide «cuadratura de envíos
aceptados, rechazados y aceptados con reparos por el SII». El TrackID del
EnvioBOLETA se devolvía y no se guardaba.

Revision ID: a7c9e1b3d5f6
Revises: f4b6d8a0c2e5
Create Date: 2026-09-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c9e1b3d5f6"
down_revision: str | None = "f4b6d8a0c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "receipt_submission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("track_id", sa.String(32), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("upload_status", sa.String(40), nullable=False),
        sa.Column("sii_state", sa.String(20), nullable=True),
        sa.Column("sii_stats", sa.JSON(), nullable=True),
        sa.Column("sii_details", sa.JSON(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("customer_id", "track_id"),
    )
    op.add_column("issued_receipt", sa.Column("submission_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_issued_receipt_submission",
        "issued_receipt",
        "receipt_submission",
        ["submission_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_issued_receipt_submission_id", "issued_receipt", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_issued_receipt_submission_id", table_name="issued_receipt")
    op.drop_constraint("fk_issued_receipt_submission", "issued_receipt", type_="foreignkey")
    op.drop_column("issued_receipt", "submission_id")
    op.drop_table("receipt_submission")
