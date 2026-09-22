"""Schemas de boleta electrónica (39/41) y consumo de folios (RCOF)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.dte import IssuerIn, ItemIn, ReceiverIn

# RUT genérico del consumidor final: la boleta no identifica al comprador.
ANONYMOUS_RECEIVER_RUT = "66666666-6"


class ReceiptReferenceIn(BaseModel):
    """Referencia de una boleta.

    No es la misma que la del DTE. El formato de boletas usa ``TpoDocRef`` para
    referenciar un documento tributario, con «un valor Numérico», y ``CodRef``
    como «Código alfanumérico establecido por la Empresa». El set de
    certificación pide el caso así: ``code="SET"`` y ``reason="CASO-1"``, sin
    tipo ni folio. Y el XSD **no define ``FchRef``**: no lleva fecha.
    """

    doc_type: int | str | None = None
    folio: str = ""
    code: int | str | None = None
    reason: str = ""


class ReceiptIn(BaseModel):
    """Una boleta del lote.

    Los precios van **con IVA incluido**, que es como los informa el set de
    pruebas del SII; el neto se despeja del bruto. Si se enviaran netos, hay que
    poner ``prices_include_vat`` en false.
    """

    type: Literal[39, 41] = 39
    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn | None = None  # por omisión, consumidor final
    items: list[ItemIn] = Field(min_length=1)
    references: list[ReceiptReferenceIn] = []
    prices_include_vat: bool = True
    # IndServicio: 3 = boleta de ventas y servicios (el caso normal).
    service_indicator: Literal[1, 2, 3, 4] = 3
    #: Folio ya reservado con ``POST /dte/folios/reserve``. Sirve para que el
    #: ERP numere la boleta con el folio que irá timbrado, antes de emitirla.
    folio: int | None = Field(default=None, ge=1)


class ReceiptBatchRequest(BaseModel):
    """Emite N boletas dentro de UN solo EnvioBOLETA.

    El SII exige que el set de certificación viaje en un único sobre, y el XSD
    admite hasta 500 boletas.
    """

    receipts: list[ReceiptIn] = Field(min_length=1, max_length=500)
    send: bool = True
    validate_xsd: bool = True


class ReceiptOut(BaseModel):
    index: int  # posición en el lote, 1-based
    type: int
    folio: int


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: str | None = None
    status: str
    detail: str = ""


class ReceiptBatchResponse(BaseModel):
    receipts: list[ReceiptOut]
    xml_base64: str  # el sobre completo
    submission: SubmissionOut | None = None


class ReportLineIn(BaseModel):
    """Un folio consumido en el período."""

    doc_type: Literal[39, 41] = 39
    folio: int = Field(ge=1)
    net_amount: int = 0
    vat_amount: int = 0
    exempt_amount: int = 0
    total_amount: int = 0
    vat_rate: int = 19
    voided: bool = False  # folio anulado: cuenta, pero no suma montos


class FolioReportRequest(BaseModel):
    """Reporte de Consumo de Folios (RCOF) de las boletas del período."""

    start_date: dt.date
    end_date: dt.date
    sequence: int = Field(1, ge=1)  # SecEnvio: número de envío del día
    correlative: int | None = Field(None, ge=1)
    lines: list[ReportLineIn] = Field(min_length=1)
    send: bool = True
    validate_xsd: bool = True

    @model_validator(mode="after")
    def _period_is_forward(self) -> FolioReportRequest:
        if self.end_date < self.start_date:
            raise ValueError(
                f"El período termina antes de empezar: {self.start_date} → {self.end_date}."
            )
        return self


class FolioReportResponse(BaseModel):
    start_date: dt.date
    end_date: dt.date
    xml_base64: str
    submission: SubmissionOut | None = None


class ReceiptTotalsOut(BaseModel):
    """Cuadratura de un envío: la suma de todos los tipos."""

    informed: int = 0
    accepted: int = 0
    repaired: int = 0
    rejected: int = 0


class ReceiptSubmissionOut(BaseModel):
    """Un EnvioBOLETA y lo que el SII dijo de él."""

    model_config = ConfigDict(from_attributes=True)

    track_id: str
    document_count: int
    upload_status: str
    sii_state: str | None = None
    #: True mientras el SII no termina de procesarlo: todavía no cuadra.
    in_process: bool = True
    totals: ReceiptTotalsOut = ReceiptTotalsOut()
    sii_stats: list | None = None
    sii_details: list | None = None
    sent_at: dt.datetime
    checked_at: dt.datetime | None = None
