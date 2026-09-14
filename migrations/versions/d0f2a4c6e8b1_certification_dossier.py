"""Expediente de certificación: sets, envíos, documentos y bitácora

Revision ID: d0f2a4c6e8b1
Revises: c9e1a3b5d7f0
Create Date: 2026-09-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0f2a4c6e8b1"
down_revision: str | None = "c9e1a3b5d7f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certification_set",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False, server_default=""),
        sa.Column("state", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("declared_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("customer_id", "code"),
    )
    op.create_index("ix_certification_set_code", "certification_set", ["code"])

    op.create_table(
        "certification_submission",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Nullable a propósito: la captura es automática y no siempre sabe a qué
        # set pertenece el envío. Se asocia después, sin perder nada mientras.
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("certification_set.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customer.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("track_id", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("envelope_kind", sa.String(30), nullable=False, server_default=""),
        sa.Column("envelope_encrypted", sa.String(), nullable=False),
        sa.Column("sii_state", sa.String(20), nullable=True),
        sa.Column("sii_detail", sa.String(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cert_submission_track", "certification_submission", ["track_id"])
    op.create_index("ix_cert_submission_set", "certification_submission", ["set_id"])

    op.create_table(
        "certification_document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("certification_submission.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_type", sa.Integer(), nullable=False),
        sa.Column("folio", sa.Integer(), nullable=False),
    )
    op.create_index("ix_cert_document_submission", "certification_document", ["submission_id"])

    op.create_table(
        "certification_note",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("certification_set.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author", sa.String(200), nullable=False, server_default=""),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cert_note_set", "certification_note", ["set_id"])


def downgrade() -> None:
    op.drop_index("ix_cert_note_set", table_name="certification_note")
    op.drop_table("certification_note")
    op.drop_index("ix_cert_document_submission", table_name="certification_document")
    op.drop_table("certification_document")
    op.drop_index("ix_cert_submission_set", table_name="certification_submission")
    op.drop_index("ix_cert_submission_track", table_name="certification_submission")
    op.drop_table("certification_submission")
    op.drop_index("ix_certification_set_code", table_name="certification_set")
    op.drop_table("certification_set")
