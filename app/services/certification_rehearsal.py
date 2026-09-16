"""Ensayo de una certificación: de los archivos del SII a los sobres, sin enviar.

Carga los sets leídos de la hoja del SII, los emite en orden y deja cada sobre
listo para comparar con lo que el SII aprobó. No habla con el Servicio: da por
aceptado cada sobre que emite, porque los libros de ventas y de guías se arman
con los documentos aceptados.

Lo usan la demo local (``scripts/certificacion_demo.py``) y el test que protege
que un contribuyente nuevo emita lo mismo que el SII ya aprobó.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from app.services import certification_service, certification_sheet

#: Orden de emisión: los libros al final, porque se arman con lo emitido antes.
ORDEN = [
    "basico", "exenta", "guias", "exportacion_1", "exportacion_2", "liquidacion",
    "factura_compra", "boletas", "libro_guias", "libro_compras", "libro_ventas",
]  # fmt: skip

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


@dataclass
class Emitido:
    kind: str
    xml: bytes | None = None
    error: str | None = None
    #: Problemas de forma: esquema del SII o firmas que no verifican.
    forma: list[str] = field(default_factory=list)


def cargar(db, cliente, hoja: certification_sheet.Sheet) -> int:
    from app.routers.certification import _apply_sets

    cargados = _apply_sets(db, cliente, hoja.sets)
    db.commit()
    return cargados


def _forma(xml: bytes) -> list[str]:
    """Que el sobre valide contra el XSD del SII y que sus firmas verifiquen."""
    from dte_chile.signer import verify_signatures
    from dte_chile.validation import Validator
    from lxml import etree

    problemas = []
    try:
        Validator(SCHEMAS).validate(xml)
    except Exception as ex:  # noqa: BLE001 — se reporta, no se interrumpe
        problemas.append(f"esquema: {ex}")
    firmas = verify_signatures(etree.fromstring(xml))
    if not firmas or not all(firmas):
        problemas.append(f"firmas: {sum(bool(f) for f in firmas)} de {len(firmas)} verifican")
    return problemas


def emitir_todo(db, cliente, cert) -> dict[str, Emitido]:
    """Emite cada set cargado, en ``ORDEN``, y lo da por aceptado."""
    from app.db.models import CertificationSet

    salida: dict[str, Emitido] = {}
    for n, kind in enumerate(ORDEN, start=1):
        cert_set = (
            db.query(CertificationSet).filter_by(customer_id=cliente.id, kind=kind).one_or_none()
        )
        if cert_set is None:
            continue
        try:
            envio = certification_service.emit(db, cliente, cert, cert_set)
        except Exception as ex:  # noqa: BLE001 — el ensayo reporta y sigue
            db.rollback()
            salida[kind] = Emitido(kind, error=f"{type(ex).__name__}: {ex}")
            continue
        xml = certification_service.envelope(envio)
        envio.track_id = f"ENSAYO{n:02d}"
        envio.sent_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        envio.sii_state = "LOK" if kind.startswith("libro") else "EPR"
        db.commit()
        salida[kind] = Emitido(kind, xml=xml, forma=_forma(xml))
        if kind == "boletas":
            # El set de boletas se entrega junto a su RCOF: se ensaya también.
            try:
                rcof = certification_service.folio_report(db, cliente, cert, envio)
                rcof_xml = certification_service.envelope(rcof)
                salida["rcof"] = Emitido("rcof", xml=rcof_xml, forma=_forma(rcof_xml))
            except Exception as ex:  # noqa: BLE001
                db.rollback()
                salida["rcof"] = Emitido("rcof", error=f"{type(ex).__name__}: {ex}")
    return salida
