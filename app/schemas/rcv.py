"""Schemas del RCV (conciliación con Odoo)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Operation = Literal["COMPRA", "VENTA"]


class RcvDocumentsRequest(BaseModel):
    # El issuer_rut se toma del cliente resuelto (tenant o customer_id), no se pide.
    period: str = Field(pattern=r"^\d{6}$", examples=["202505"])  # AAAAMM
    operation: Operation = "COMPRA"


class RcvDocumentOut(BaseModel):
    """Espejo de ``dte_chile.RcvDocument`` (clave de match: doc_type+rut+folio)."""

    model_config = ConfigDict(from_attributes=True)

    operation: str
    state: str
    doc_type: int
    folio: int
    counterpart_rut: str
    counterpart_name: str
    date: dt.date
    exempt_amount: int
    net_amount: int
    vat_amount: int
    total_amount: int
    reception_date: dt.datetime | None = None


class RcvDocumentsResponse(BaseModel):
    issuer_rut: str
    period: str
    operation: str
    count: int
    documents: list[RcvDocumentOut]


# --------------------------------------------------------------------------- #
#  Conciliación contra el sistema del cliente (Odoo)
# --------------------------------------------------------------------------- #
class SourceDocumentIn(BaseModel):
    """Un documento tal como lo tiene el ERP del cliente.

    La llave de cruce es (doc_type, counterpart_rut, folio): el folio solo no
    basta, porque distintos emisores repiten correlativos.
    """

    doc_type: int
    folio: int
    counterpart_rut: str  # cliente (venta) o proveedor (compra)
    date: dt.date
    exempt_amount: int = 0
    net_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0


class ReconcileRequest(BaseModel):
    period: str = Field(pattern=r"^\d{6}$", examples=["202609"])  # AAAAMM
    operation: Operation = "VENTA"
    documents: list[SourceDocumentIn] = Field(
        default_factory=list, description="Lo que el ERP tiene para ese período."
    )


class AmountDifference(BaseModel):
    sii: int | dt.date
    source: int | dt.date


class DocumentBrief(BaseModel):
    doc_type: int
    folio: int
    counterpart_rut: str
    date: dt.date
    total_amount: int


class MismatchOut(BaseModel):
    doc_type: int
    folio: int
    counterpart_rut: str
    differences: dict[str, AmountDifference]


class ReconcileResponse(BaseModel):
    period: str
    operation: str
    balanced: bool  # true si no hay nada que corregir
    matched: int
    sii_total: int
    source_total: int
    # El SII lo tiene y el ERP no: falta cargarlo (y el libro debe declararlo).
    only_in_sii: list[DocumentBrief]
    # El ERP lo tiene y el SII no: no se envió, o el envío fue rechazado.
    only_in_source: list[DocumentBrief]
    # En ambos con montos distintos: el caso que no salta a la vista.
    mismatched: list[MismatchOut]
