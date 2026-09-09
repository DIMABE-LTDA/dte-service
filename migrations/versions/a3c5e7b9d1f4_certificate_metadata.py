"""Datos del certificado a la vista: RUT del firmante, titular, emisor y huella

El RUT que firma es el que necesita el atributo «Enviar Doctos» en el SII, y no
es el de la empresa: el certificado se emite a una persona natural. Tenerlo
guardado evita descifrar el .pfx —y derivar su clave— cada vez que se pinta la
ficha, y convierte el rechazo más caro del trámite en algo que se lee de un
vistazo.

La huella es SHA-256 del certificado en DER, no del archivo: identifica al
certificado y no a una exportación concreta, así que detecta el mismo .pfx
reexportado con otra contraseña.

Los certificados ya cargados se rellenan aquí mismo. Cada fila va en su propio
try: uno que no se pueda descifrar —clave Fernet rotada, contraseña cambiada—
no debe impedir la migración, y queda con los campos en NULL, que es
exactamente lo que significan.

Revision ID: a3c5e7b9d1f4
Revises: f2b4d6e8a0c3
Create Date: 2026-09-09 15:10:00.000000

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c5e7b9d1f4"
down_revision: str | None = "f2b4d6e8a0c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def _backfill() -> None:
    """Rellena los certificados ya cargados leyendo su .pfx."""
    from app.core import crypto
    from app.services import certificate_service

    conexion = op.get_bind()
    filas = conexion.execute(
        sa.text("SELECT id, file_base64, password FROM customer_certificate")
    ).fetchall()
    hechos = 0
    for fila in filas:
        try:
            pfx = crypto.decrypt(fila.file_base64)
            datos = certificate_service.describe(pfx, crypto.decrypt_str(fila.password))
            cert = certificate_service.Certificate.from_pfx_bytes(
                pfx, crypto.decrypt_str(fila.password)
            )
        except Exception as ex:  # noqa: BLE001 - un .pfx ilegible no bloquea la migración
            logger.warning("certificado %s: no se pudo leer para rellenar (%s)", fila.id, ex)
            continue
        conexion.execute(
            sa.text(
                "UPDATE customer_certificate SET rut = :rut, holder = :holder,"
                " issuer = :issuer, thumbprint = :thumbprint WHERE id = :id"
            ),
            {
                "id": fila.id,
                "rut": getattr(cert, "rut", None),
                "holder": datos["holder"],
                "issuer": datos["issuer"],
                "thumbprint": datos["thumbprint"],
            },
        )
        hechos += 1
    logger.info("certificados rellenados: %s de %s", hechos, len(filas))


def upgrade() -> None:
    with op.batch_alter_table("customer_certificate") as lote:
        lote.add_column(sa.Column("rut", sa.String(length=20), nullable=True))
        lote.add_column(sa.Column("holder", sa.String(length=200), nullable=True))
        lote.add_column(sa.Column("issuer", sa.String(length=200), nullable=True))
        lote.add_column(sa.Column("thumbprint", sa.String(length=64), nullable=True))
    op.create_index("ix_customer_certificate_thumbprint", "customer_certificate", ["thumbprint"])
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_customer_certificate_thumbprint", table_name="customer_certificate")
    with op.batch_alter_table("customer_certificate") as lote:
        lote.drop_column("thumbprint")
        lote.drop_column("issuer")
        lote.drop_column("holder")
        lote.drop_column("rut")
