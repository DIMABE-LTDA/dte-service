"""Datos del emisor y receptores de prueba en la ficha del cliente

Hasta ahora el emisor —razón social, giro, ACTECO, dirección, comuna, sucursal—
iba escrito a mano en cada documento de cada definición de set. Es dato del
contribuyente, no del caso: repetido en siete sets, y al clonar las definiciones
a otro cliente se emitía con el emisor del anterior.

Los receptores de prueba son clientes reales del contribuyente: el instructivo
del SII pide RUT existentes y distintos por factura, y las definiciones mandaban
todo al propio SII.

El perfil se rellena desde las definiciones ya cargadas cuando traen un emisor
con el RUT del cliente: es el único sitio donde ese dato existía, y no debe
perderse. Las definiciones NO se tocan: el sistema ignora lo que traigan de
emisor y fechas, así que no hace falta reescribirlas para que funcionen.

Revision ID: d6f8b0c2e4a7
Revises: c5e7a9b1d3f6
Create Date: 2026-09-10 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6f8b0c2e4a7"
down_revision: str | None = "c5e7a9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNAS = (
    ("issuer_legal_name", sa.String(length=100)),
    ("issuer_activity", sa.String(length=80)),
    ("issuer_economic_activity", sa.Integer()),
    ("issuer_address", sa.String(length=70)),
    ("issuer_commune", sa.String(length=20)),
    ("issuer_city", sa.String(length=20)),
    ("issuer_branch_name", sa.String(length=20)),
    ("issuer_branch_code", sa.Integer()),
)


def _rellenar_perfil() -> None:
    """Toma el emisor de las definiciones cargadas, si es del propio cliente."""
    import json

    conexion = op.get_bind()
    clientes = conexion.execute(
        sa.text("SELECT id, rut FROM customer WHERE issuer_legal_name IS NULL")
    ).fetchall()
    for cliente in clientes:
        filas = conexion.execute(
            sa.text(
                "SELECT d.payload FROM certification_definition d"
                " JOIN certification_set s ON s.id = d.set_id WHERE s.customer_id = :c"
            ),
            {"c": cliente.id},
        ).fetchall()
        for fila in filas:
            payload = fila.payload if isinstance(fila.payload, dict) else json.loads(fila.payload)
            emisor = next(
                (
                    d.get("issuer")
                    for d in (payload or {}).get("documents", [])
                    if (d.get("issuer") or {}).get("rut") == cliente.rut
                ),
                None,
            )
            if not emisor:
                continue
            conexion.execute(
                sa.text(
                    "UPDATE customer SET issuer_legal_name = :n, issuer_activity = :a,"
                    " issuer_economic_activity = :e, issuer_address = :d,"
                    " issuer_commune = :m, issuer_city = :ci, issuer_branch_code = :b"
                    " WHERE id = :id"
                ),
                {
                    "id": cliente.id,
                    "n": (emisor.get("business_name") or "")[:100] or None,
                    "a": (emisor.get("activity") or "")[:80] or None,
                    "e": emisor.get("economic_activity"),
                    "d": (emisor.get("address") or "")[:70] or None,
                    "m": (emisor.get("commune") or "")[:20] or None,
                    "ci": (emisor.get("city") or "")[:20] or None,
                    "b": emisor.get("branch_code"),
                },
            )
            break


def upgrade() -> None:
    with op.batch_alter_table("customer") as lote:
        for nombre, tipo in _COLUMNAS:
            lote.add_column(sa.Column(nombre, tipo, nullable=True))
        lote.add_column(sa.Column("cert_receivers", sa.JSON(), nullable=True))
    _rellenar_perfil()


def downgrade() -> None:
    with op.batch_alter_table("customer") as lote:
        lote.drop_column("cert_receivers")
        for nombre, _tipo in reversed(_COLUMNAS):
            lote.drop_column(nombre)
