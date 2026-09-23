"""Cesión electrónica de facturas (factoring).

El facturador ya guarda cada documento emitido, así que ceder una factura no
obliga a que el ERP conserve ni vuelva a mandar el XML firmado: basta con decir
qué folio se cede y a quién. Eso evita el error que más caro sale aquí, que es
ceder un documento que no es exactamente el que se emitió.
"""

from __future__ import annotations

import base64
import datetime as dt
from zoneinfo import ZoneInfo

from dte_chile.cession import (
    Cession,
    CessionClient,
    Party,
    Signatory,
    build_aec,
    serialize,
)
from dte_chile.sii_client import Environment
from lxml import etree
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Customer
from app.errors.exceptions import DomainError
from app.services import idempotency

_CL_TZ = ZoneInfo("America/Santiago")


def _party(data) -> Party:
    return Party(rut=data.rut, name=data.name, address=data.address or "", email=data.email or "")


def build(db: Session, customer: Customer, cert, req) -> dict:
    """Arma el AEC de la cesión y, si se pide, lo manda al RPETC.

    El documento cedido sale del archivo del propio facturador: el ERP dice
    qué folio cede, no manda el XML. Un AEC que lleva un documento distinto
    del emitido lo rechaza el SII, y hasta que lo rechaza el factoring ya
    pagó.
    """
    try:
        xml = idempotency.stored_xml(db, customer, req.doc_type, req.folio)
    except LookupError as ex:
        raise DomainError(
            f"{ex} Sólo se puede ceder un documento emitido por este contribuyente."
        ) from ex

    dte = etree.fromstring(xml)
    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    cession = Cession(
        dte=dte,
        assignor=_party(req.assignor),
        assignee=_party(req.assignee),
        signatory=Signatory(rut=req.signatory.rut, name=req.signatory.name),
        amount=req.amount,
        due_date=req.due_date,
        sequence=req.sequence,
        declaration=req.declaration or "",
    )
    if cession.assignor.rut != customer.rut:
        raise DomainError(
            f"El cedente ({cession.assignor.rut}) no es este contribuyente ({customer.rut}): "
            "sólo se ceden facturas propias."
        )

    try:
        aec = build_aec(cession, cert, ts)
    except ValueError as ex:
        raise DomainError(str(ex)) from ex
    aec_xml = serialize(aec)

    submission = None
    if req.send:
        client = CessionClient(
            cert, Environment[customer.environment.name], settings.request_timeout_s
        )
        try:
            resultado = client.send(
                aec_xml,
                cession.assignor.rut,
                cert.rut or cession.assignor.rut,
                notify_email=cession.assignor.email,
            )
        finally:
            client.close()
        submission = _submission_out(resultado)

    return {
        "doc_type": req.doc_type,
        "folio": req.folio,
        "xml_base64": base64.b64encode(aec_xml).decode("ascii"),
        "submission": submission,
    }


def status(customer: Customer, cert, track_id: str) -> dict:
    """Estado del AEC en el RPETC."""
    settings = get_settings()
    client = CessionClient(cert, Environment[customer.environment.name], settings.request_timeout_s)
    try:
        return _submission_out(client.status(track_id))
    finally:
        client.close()


def _submission_out(resultado) -> dict:
    return {
        "track_id": resultado.track_id,
        "status": resultado.status,
        "detail": resultado.detail,
        "accepted": resultado.accepted,
        "in_process": resultado.in_process,
    }
