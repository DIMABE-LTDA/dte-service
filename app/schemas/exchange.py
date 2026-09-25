"""Schemas de los acuses de intercambio (responder a un EnvioDTE recibido)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class AcknowledgmentRequest(BaseModel):
    envelope_base64: str  # EnvioDTE recibido, en base64


class ResultRequest(BaseModel):
    envelope_base64: str
    accept: bool = True
    rejection_label: str = ""


class ReceiptsRequest(BaseModel):
    envelope_base64: str
    location: str = "Bodega"


class ExchangeResponse(BaseModel):
    xml_base64: str


class InspectRequest(BaseModel):
    envelope_base64: str


class ItemCodeOut(BaseModel):
    #: TpoCodigo: la codificación (INT1, EAN13...). El código es del proveedor.
    type: str
    value: str


class ReceivedLineOut(BaseModel):
    """Una línea tal como la escribió el proveedor, sin recalcular."""

    number: int
    name: str
    description: str
    codes: list[ItemCodeOut]
    quantity: float | None
    #: Texto libre (UnmdItem): no hay tabla del SII que lo normalice.
    unit: str
    unit_price: float | None
    discount_amount: float
    surcharge_amount: float
    #: MontoItem, con descuentos y recargos ya aplicados.
    amount: float
    #: IndExe: 1 exento, 2 no facturable...; None si la línea es afecta.
    exempt_indicator: int | None
    tax_codes: list[int]


class ReceivedReferenceOut(BaseModel):
    #: Texto: 801 es la orden de compra, pero también existen códigos como «HES».
    doc_type: str
    folio: str
    date: dt.date | None
    #: 1 anula, 2 corrige texto, 3 corrige montos (sólo en notas).
    code: int | None
    reason: str
    is_global: bool


class ReceivedAdjustmentOut(BaseModel):
    kind: str
    value_type: str
    value: float
    exempt_indicator: int | None
    reason: str


class ReceivedTaxOut(BaseModel):
    code: int
    amount: int
    rate: float | None


class ReceivedDocumentOut(BaseModel):
    """Un DTE del sobre recibido, con lo que hace falta para registrarlo."""

    doc_type: int
    folio: int
    issue_date: dt.date
    issuer_rut: str
    receiver_rut: str
    total_amount: int
    #: ¿Va dirigido a quien recibe? Un sobre puede traer documentos de otro
    #: receptor: esos se rechazan y no llevan recibo de mercaderías.
    addressed_to_me: bool
    # Lo que hace falta para registrar la compra en el ERP.
    issuer_name: str = ""
    due_date: dt.date | None = None
    #: Los precios de las líneas vienen con IVA incluido (MntBruto).
    prices_include_vat: bool = False
    net_amount: int = 0
    exempt_amount: int = 0
    vat_rate: float | None = None
    vat_amount: int = 0
    taxes: list[ReceivedTaxOut] = []
    lines: list[ReceivedLineOut] = []
    references: list[ReceivedReferenceOut] = []
    adjustments: list[ReceivedAdjustmentOut] = []


class InspectResponse(BaseModel):
    issuer_rut: str
    receiver_rut: str
    documents: list[ReceivedDocumentOut]


class ClaimRequest(BaseModel):
    """Aceptar o reclamar en el SII un documento que nos mandaron."""

    #: RUT de quien EMITIÓ el documento (el proveedor), no el propio.
    issuer_rut: str
    doc_type: int
    folio: int
    #: ACD aceptar contenido · RCD reclamar contenido · ERM recibo de
    #: mercaderías · RFP falta parcial · RFT falta total.
    action: str


class ClaimQuery(BaseModel):
    issuer_rut: str
    doc_type: int
    folio: int


class ClaimEventOut(BaseModel):
    code: str
    label: str
    date: str
    responder_rut: str


class ClaimResultOut(BaseModel):
    ok: bool
    code: int | None
    detail: str
    events: list[ClaimEventOut] = []
