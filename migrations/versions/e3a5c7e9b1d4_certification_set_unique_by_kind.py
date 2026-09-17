"""Un set sin número de atención se identifica por su tipo

La unicidad era (cliente, código). Los sets que el SII no numera —el de boletas
y la simulación— entran con código vacío, así que el segundo chocaba con el
primero: cargar la simulación de un cliente que ya tenía el set de boletas
respondía 409. Pasa a ser (cliente, código, tipo).

El nombre de la restricción original lo puso PostgreSQL (no se nombró al
crearla), así que se busca en vez de suponerlo.

Revision ID: e3a5c7e9b1d4
Revises: d6f8b0c2e4a7
Create Date: 2026-09-16 21:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a5c7e9b1d4"
down_revision: str | None = "d6f8b0c2e4a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLA = "certification_set"
_NUEVA = "uq_certification_set_customer_code_kind"


def _unica(columnas: list[str]) -> str | None:
    for restriccion in sa.inspect(op.get_bind()).get_unique_constraints(_TABLA):
        if sorted(restriccion["column_names"]) == sorted(columnas):
            return restriccion["name"]
    return None


def upgrade() -> None:
    vieja = _unica(["customer_id", "code"])
    with op.batch_alter_table(_TABLA) as batch:
        if vieja:
            batch.drop_constraint(vieja, type_="unique")
        batch.create_unique_constraint(_NUEVA, ["customer_id", "code", "kind"])


def downgrade() -> None:
    with op.batch_alter_table(_TABLA) as batch:
        batch.drop_constraint(_NUEVA, type_="unique")
        batch.create_unique_constraint(
            "certification_set_customer_id_code_key", ["customer_id", "code"]
        )
