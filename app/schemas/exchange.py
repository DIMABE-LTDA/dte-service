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
