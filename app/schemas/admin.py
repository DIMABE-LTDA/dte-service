"""Schemas de administración (clientes, certificados, CAF, servicios)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.validators import normalize_rut


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1)
    # customerCode (opaco). Opcional: si no se envía, el servidor lo genera a
    # partir del nombre + sufijo aleatorio.
    key: str | None = Field(default=None, min_length=1)
    rut: str
    environment: Literal["CERTIFICATION", "PRODUCTION"] = "CERTIFICATION"
    resolution_number: int = 0
    resolution_date: dt.date = dt.date(2014, 8, 22)

    @field_validator("rut")
    @classmethod
    def _rut(cls, v: str) -> str:
        return normalize_rut(v)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key: str
    rut: str
    environment: str
    #: Van en la carátula de cada DTE. Se devuelven porque el portal los
    #: guardaba sin mostrarlos nunca: si están mal, todos los documentos del
    #: cliente salen mal y no había dónde verlo ni forma de corregirlo sin
    #: escribirlos de memoria.
    resolution_number: int
    resolution_date: dt.date
    deleted_at: dt.datetime | None = None


class CustomerUpdate(BaseModel):
    """Edición de cliente. Todos los campos opcionales (parcial). El customerCode
    (key) no se edita: es el identificador del tenant."""

    name: str | None = Field(default=None, min_length=1)
    rut: str | None = None
    environment: Literal["CERTIFICATION", "PRODUCTION"] | None = None
    resolution_number: int | None = None
    resolution_date: dt.date | None = None

    @field_validator("rut")
    @classmethod
    def _rut(cls, v: str | None) -> str | None:
        return normalize_rut(v) if v else v


class CertificateUpload(BaseModel):
    file_base64: str  # .pfx en base64
    password: str


class CertificateOut(BaseModel):
    id: int
    rut: str | None
    due_date: dt.date


class SiiKeyUpload(BaseModel):
    password: str = Field(min_length=1)  # clave tributaria del SII (login web BHE)


class SiiKeyOut(BaseModel):
    customer_id: int
    configured: bool = True


class SiiKeyStatus(BaseModel):
    configured: bool


class CafUpload(BaseModel):
    xml_base64: str  # archivo CAF (AUTORIZACION) en base64


class CafOut(BaseModel):
    id: int
    doc_type: int
    folio_from: int
    folio_to: int


class ServiceInfo(BaseModel):
    code: str
    name: str


class ServiceGrant(BaseModel):
    service_code: str
    # apiKey opcional: si no se envía, el servidor genera una y la devuelve UNA
    # vez (en BD siempre se guarda hasheada).
    apikey: str | None = None


class ServiceGrantOut(BaseModel):
    service_code: str
    granted: bool
    # Devuelta solo cuando el servidor la generó (mostrar una vez al usuario).
    apikey: str | None = None


class GrantedServiceOut(BaseModel):
    service_code: str
    name: str


class CertificateInfo(BaseModel):
    id: int
    due_date: dt.date
    created_at: dt.datetime
    expired: bool
    #: RUT del firmante. Es el que necesita «Enviar Doctos» en el SII, y no es
    #: el de la empresa: el certificado se emite a una persona natural.
    rut: str | None = None
    holder: str | None = None
    issuer: str | None = None


class CafInfo(BaseModel):
    id: int
    doc_type: int
    folio_from: int
    folio_to: int
    exhausted: bool
    last_folio: int
