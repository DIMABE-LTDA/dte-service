"""Servicio del Libro de Compras y Ventas (IECV)."""

from __future__ import annotations

import base64
import datetime as dt
from zoneinfo import ZoneInfo

from dte_chile.book import BookCover, BookLine, NonRecoverableVat, build_book, serialize
from dte_chile.certificate import Certificate
from dte_chile.document_types import TransferType
from dte_chile.guide_book import (
    GuideBookCover,
    GuideBookLine,
    VoidStatus,
    build_guide_book,
)
from dte_chile.guide_book import serialize as serialize_guide_book
from dte_chile.validation import Validator

from app.core.config import get_settings
from app.db.models import Customer
from app.services import sii_upload

_CL_TZ = ZoneInfo("America/Santiago")  # el SII fecha el libro en hora chilena


def _send(customer: Customer, cert: Certificate, xml: bytes):
    """Sube el libro por el mismo canal que los sobres de documentos."""
    return sii_upload.upload(customer, cert, xml, customer.rut, get_settings().request_timeout_s)


def _book_line(line) -> BookLine:
    data = line.model_dump()
    non_recoverable = data.pop("non_recoverable_vat")
    return BookLine(
        **data,
        non_recoverable_vat=[NonRecoverableVat(**entry) for entry in non_recoverable],
    )


def build(customer: Customer, cert: Certificate, req) -> dict:
    cover = BookCover(
        issuer_rut=customer.rut,
        sender_rut=cert.rut or customer.rut,
        period=req.period,
        operation_type=req.operation_type,
        resolution_number=customer.resolution_number,
        resolution_date=customer.resolution_date,
        proportionality_factor=req.proportionality_factor,
        book_type=req.book_type,
        notification_folio=req.notification_folio,
        lines=[_book_line(line) for line in req.lines],
    )
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)
    xml = serialize(build_book(cover, cert, ts))
    if req.validate_xsd:
        Validator(get_settings().schemas_dir).validate(xml)
    return {
        "period": req.period,
        "operation_type": req.operation_type,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": _send(customer, cert, xml) if req.send else None,
    }


def _guide_line(line) -> GuideBookLine:
    data = line.model_dump()
    transfer_type = data.pop("transfer_type")
    voided = data.pop("voided")
    return GuideBookLine(
        **data,
        transfer_type=TransferType(transfer_type) if transfer_type else None,
        voided=VoidStatus(voided) if voided else None,
    )


def build_guides(customer: Customer, cert: Certificate, req) -> dict:
    """Libro de Guías de Despacho (LibroGuia).

    Además del set de certificación, es el registro que la Res. Ex. N°154 exige
    llevar mientras el SII no ponga en marcha su Registro de Guías de Despacho.
    """
    cover = GuideBookCover(
        issuer_rut=customer.rut,
        sender_rut=cert.rut or customer.rut,
        period=req.period,
        resolution_number=customer.resolution_number,
        resolution_date=customer.resolution_date,
        submission_type=req.submission_type,
        notification_folio=req.notification_folio,
        lines=[_guide_line(line) for line in req.lines],
    )
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)
    xml = serialize_guide_book(build_guide_book(cover, cert, ts))
    if req.validate_xsd:
        Validator(get_settings().schemas_dir).validate(xml)
    return {
        "period": req.period,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": _send(customer, cert, xml) if req.send else None,
    }
