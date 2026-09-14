"""Servicio del Libro de Compras y Ventas (IECV)."""

from __future__ import annotations

import base64
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
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


# Campos monetarios de la línea. En moneda extranjera se convierten todos: si
# uno se quedara sin convertir, la línea no cerraría y el libro saldría
# descuadrado, que es el error más caro de diagnosticar contra el SII.
_MONEY_FIELDS = (
    "exempt_amount",
    "net_amount",
    "vat_amount",
    "total_amount",
    "common_use_vat",
    "retained_total_vat",
    "non_billable_amount",
    "commission_net",
    "commission_exempt",
    "commission_vat",
)


def _to_clp(amount: Decimal, rate: Decimal) -> int:
    """Convierte a pesos enteros, redondeando medio hacia arriba.

    No se usa ``round()``: redondea al par ("banker's rounding") y 0,5 pesos
    caería unas veces arriba y otras abajo, que no es lo que hace el SII ni lo
    que espera quien cuadra el libro a mano.
    """
    return int((Decimal(amount) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _book_line(line) -> BookLine:
    """Adapta la línea de la petición a la del motor, en pesos.

    El IECV se declara **siempre en pesos**. Un documento de exportación se
    emite en su moneda —una factura de USD 15,40 es 15,40 dólares— y sin
    convertir entraba al libro como 15 pesos: el monto del documento leído como
    si fuera nacional. Cada línea se convierte al tipo de cambio de su propia
    fecha, que es el que corresponde al documento.
    """
    data = line.model_dump()
    non_recoverable = data.pop("non_recoverable_vat")
    currency = data.pop("currency", None)
    rate = data.pop("exchange_rate", None)

    if currency and rate is not None:
        # ¿La línea cerraba antes de convertir? Si sí, tiene que seguir
        # cerrando después: redondear cada parte por su cuenta puede dejar el
        # total a un peso de la suma, y el SII cuadra el libro sumando.
        cerraba = (
            data["total_amount"] == data["exempt_amount"] + data["net_amount"] + data["vat_amount"]
        )
        for field in _MONEY_FIELDS:
            data[field] = _to_clp(data[field], rate)
        non_recoverable = [
            {**entry, "amount": _to_clp(entry["amount"], rate)} for entry in non_recoverable
        ]
        if cerraba:
            data["total_amount"] = data["exempt_amount"] + data["net_amount"] + data["vat_amount"]
    else:
        # Sin moneda ya son pesos; el schema garantiza que son enteros.
        for field in _MONEY_FIELDS:
            data[field] = int(data[field])
        non_recoverable = [{**entry, "amount": int(entry["amount"])} for entry in non_recoverable]

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
