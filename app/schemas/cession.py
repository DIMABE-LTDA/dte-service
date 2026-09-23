"""Schemas de la cesión electrónica de facturas (AEC)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class PartyIn(BaseModel):
    rut: str
    name: str
    address: str = ""
    email: str = ""


class SignatoryIn(BaseModel):
    """Quien firma la cesión por el cedente: una persona, con su RUT."""

    rut: str
    name: str


class CessionRequest(BaseModel):
    #: Qué factura se cede. Sale del archivo del facturador: no se manda el XML.
    doc_type: int
    folio: int
    assignor: PartyIn  # cedente
    assignee: PartyIn  # cesionario (el factoring)
    signatory: SignatoryIn
    amount: int = Field(gt=0)
    #: Hasta cuándo tiene plazo el deudor para pagar.
    due_date: dt.date
    #: Número de cesión del documento: la segunda vez que se cede va 2.
    sequence: int = Field(default=1, ge=1)
    #: Reemplaza la declaración jurada por omisión (Ley 19.983, art. 3).
    declaration: str = ""
    send: bool = True


class CessionStatusRequest(BaseModel):
    track_id: str


class CessionSubmissionOut(BaseModel):
    track_id: str | None
    status: str
    detail: str = ""
    accepted: bool = False
    in_process: bool = False


class CessionOut(BaseModel):
    doc_type: int
    folio: int
    xml_base64: str
    submission: CessionSubmissionOut | None = None
