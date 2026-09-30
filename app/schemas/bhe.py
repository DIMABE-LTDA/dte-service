"""Schemas de BHE recibidas y de BTE emitidas (Boletas de Prestación de Servicios
de Terceros): las dos se leen del portal del SII con la clave tributaria."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class BheReceivedRequest(BaseModel):
    # El RUT receptor se toma del cliente resuelto (tenant), no se pide.
    period: str = Field(pattern=r"^\d{6}$", examples=["202505"])  # AAAAMM


class BheReceivedOut(BaseModel):
    """Espejo de ``dte_chile.BheDocument``."""

    model_config = ConfigDict(from_attributes=True)

    issuer_rut: str
    issuer_name: str
    folio: int
    issue_date: dt.date | None = None
    gross_amount: int
    retention_amount: int
    net_amount: int
    status: str  # "vigente" | "anulada"
    cancel_date: dt.date | None = None


class BheReceivedResponse(BaseModel):
    receiver_rut: str
    period: str
    count: int
    documents: list[BheReceivedOut]


class BteIssuedRequest(BaseModel):
    # El RUT emisor de las BTE es el del cliente resuelto (tenant), no se pide.
    period: str = Field(pattern=r"^\d{6}$", examples=["202605"])  # AAAAMM


class BteIssuedOut(BaseModel):
    """Espejo de ``dte_chile.BteDocument``: el prestador es quien recibe el pago."""

    model_config = ConfigDict(from_attributes=True)

    provider_rut: str
    provider_name: str
    folio: int
    issue_date: dt.date | None = None
    gross_amount: int
    retention_amount: int  # la retiene el cliente, que emitió la BTE
    net_amount: int
    status: str  # "vigente" | "anulada"


class BteIssuedResponse(BaseModel):
    issuer_rut: str
    period: str
    count: int
    documents: list[BteIssuedOut]
