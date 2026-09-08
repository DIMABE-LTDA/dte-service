"""Definiciones de set por cliente: qué emitir en cada uno

Revision ID: f2b4d6e8a0c3
Revises: e1a3c5b7d9f2
Create Date: 2026-09-08 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b4d6e8a0c3"
down_revision: str | None = "e1a3c5b7d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # El contenido del set es dato del contribuyente, no del repositorio: el SII
    # asigna a cada RUT sus propios casos.
    op.create_table(
        "certification_definition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("certification_set.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("set_id"),
    )
    op.create_index("ix_cert_definition_set", "certification_definition", ["set_id"])

    # Un sobre puede estar emitido y todavía sin enviar. Separar las dos cosas es
    # lo que permite reenviar sin volver a quemar folios.
    op.alter_column("certification_submission", "track_id", nullable=True)
    op.alter_column("certification_submission", "sent_at", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM certification_submission WHERE track_id IS NULL")
    op.alter_column("certification_submission", "sent_at", nullable=False)
    op.alter_column("certification_submission", "track_id", nullable=False)
    op.drop_index("ix_cert_definition_set", table_name="certification_definition")
    op.drop_table("certification_definition")
