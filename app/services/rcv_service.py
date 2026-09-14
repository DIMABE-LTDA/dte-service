"""Servicio RCV: descarga normalizada para conciliar contra Odoo."""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from typing import Protocol

from dte_chile.certificate import Certificate
from dte_chile.rcv import RCVClient, RcvDocument
from sqlalchemy.orm import Session

from app.db.models import Customer
from app.errors.exceptions import CertificateUnavailable
from app.schemas.validators import normalize_rut
from app.services import certificate_service


def list_documents(
    cert: Certificate, issuer_rut: str, period: str, operation: str
) -> list[RcvDocument]:
    with RCVClient(cert) as rcv:
        return rcv.documents(issuer_rut, period, operation)


def list_documents_for_customer(
    db: Session, customer: Customer, period: str, operation: str
) -> list[RcvDocument]:
    """Versión de operador: resuelve el cert guardado del cliente y consulta su RCV.

    El ``issuer_rut`` se toma del propio cliente (no se pide en el request).
    """
    cert = certificate_service.resolve_certificate(db, customer)
    if cert is None:
        raise CertificateUnavailable(customer.id)
    return list_documents(cert, customer.rut, period, operation)


# --------------------------------------------------------------------------- #
#  Conciliación contra el sistema del cliente (Odoo)
# --------------------------------------------------------------------------- #
# Un documento se identifica por (tipo, RUT de la contraparte, folio). El folio
# solo no basta: distintos emisores usan los mismos correlativos.
MatchKey = tuple[int, str, int]


def _key(doc_type: int, rut: str, folio: int) -> MatchKey:
    return (doc_type, normalize_rut(rut), folio)


# Campos que se contrastan monto a monto. La fecha se compara aparte porque una
# diferencia de fecha no descuadra el IVA, pero sí cambia el período en que el
# documento debe declararse.
_AMOUNTS = ("exempt_amount", "net_amount", "vat_amount", "total_amount")


class _Comparable(Protocol):
    """Lo mínimo que necesita un documento para entrar en la conciliación.

    Se define como protocolo para que sirva igual un RcvDocument del SII que el
    modelo Pydantic que llega desde el ERP.
    """

    doc_type: int
    folio: int
    counterpart_rut: str
    date: _dt.date
    exempt_amount: int
    net_amount: int
    vat_amount: int
    total_amount: int


def reconcile(
    sii_documents: Sequence[RcvDocument], source_documents: Sequence[_Comparable]
) -> dict:
    """Cruza lo que el SII tiene registrado contra lo que trae el sistema del cliente.

    Devuelve tres grupos, que son tres problemas distintos:

    - ``only_in_sii``: el SII lo tiene y el ERP no. Suele ser un documento
      emitido fuera del ERP, o uno que el ERP perdió: hay que cargarlo, porque
      el Libro de Ventas del período debe declararlo.
    - ``only_in_source``: el ERP lo tiene y el SII no. O nunca se envió, o el
      envío fue rechazado y nadie lo notó.
    - ``mismatched``: están en ambos con montos distintos. Es el caso más
      peligroso, porque no salta a la vista y descuadra la declaración.
    """
    by_sii: dict[MatchKey, RcvDocument] = {
        _key(d.doc_type, d.counterpart_rut, d.folio): d for d in sii_documents
    }
    by_source: dict[MatchKey, _Comparable] = {
        _key(d.doc_type, d.counterpart_rut, d.folio): d for d in source_documents
    }

    mismatched = []
    for key in by_sii.keys() & by_source.keys():
        sii, source = by_sii[key], by_source[key]
        differences = {
            field: {"sii": getattr(sii, field), "source": getattr(source, field)}
            for field in _AMOUNTS
            if getattr(sii, field) != getattr(source, field)
        }
        if sii.date != source.date:
            differences["date"] = {"sii": sii.date, "source": source.date}
        if differences:
            mismatched.append(
                {
                    "doc_type": sii.doc_type,
                    "folio": sii.folio,
                    "counterpart_rut": sii.counterpart_rut,
                    "differences": differences,
                }
            )

    def _brief(doc: _Comparable) -> dict:
        return {
            "doc_type": doc.doc_type,
            "folio": doc.folio,
            "counterpart_rut": doc.counterpart_rut,
            "date": doc.date,
            "total_amount": doc.total_amount,
        }

    only_in_sii = [by_sii[k] for k in by_sii.keys() - by_source.keys()]
    only_in_source = [by_source[k] for k in by_source.keys() - by_sii.keys()]
    matched = len(by_sii.keys() & by_source.keys())

    return {
        "matched": matched,
        "only_in_sii": sorted(
            (_brief(d) for d in only_in_sii), key=lambda d: (d["doc_type"], d["folio"])
        ),
        "only_in_source": sorted(
            (_brief(d) for d in only_in_source), key=lambda d: (d["doc_type"], d["folio"])
        ),
        "mismatched": sorted(mismatched, key=lambda d: (d["doc_type"], d["folio"])),
        "balanced": not (only_in_sii or only_in_source or mismatched),
        "sii_total": sum(d.total_amount for d in sii_documents),
        "source_total": sum(d.total_amount for d in source_documents),
    }
