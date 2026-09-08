"""Schemas del expediente de certificación."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CertificationDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_type: int
    folio: int


class CauseOut(BaseModel):
    """Qué significa la respuesta del SII y qué revisar.

    Sale de un catálogo del repositorio, no de la base: es conocimiento del
    dominio y no depende del cliente.
    """

    label: str
    meaning: str
    usually: str = ""
    check: list[str] = []
    ok: bool = False


class CertificationSubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    set_id: int | None
    # Nulos mientras el sobre está emitido y sin enviar.
    track_id: str | None
    sent_at: dt.datetime | None
    envelope_kind: str
    sii_state: str | None
    sii_detail: str | None
    checked_at: dt.datetime | None
    documents: list[CertificationDocumentOut] = []
    cause: CauseOut | None = None


class StageOut(BaseModel):
    """Una etapa del set con su semáforo.

    ``state`` es lo que pinta el color: ``ok`` verde, ``pendiente`` gris,
    ``atencion`` ámbar y ``error`` rojo. ``detail`` dice por qué, que es lo que
    convierte un semáforo en algo accionable.
    """

    key: Literal["requisitos", "emision", "envio", "estado", "declaracion"]
    label: str
    state: Literal["ok", "pendiente", "atencion", "error"]
    detail: str = ""


class CertificationSetOut(BaseModel):
    # None en un set esperado que todavía no existe en la base: se da de alta
    # solo cuando llega su primer envío o cuando el operador le pone su número.
    id: int | None = None
    code: str
    kind: str
    state: str
    declared_at: dt.datetime | None
    stages: list[StageOut]
    submissions: list[CertificationSubmissionOut]


class StepOut(BaseModel):
    """Un paso del trámite. El primero lo lleva el sistema; el resto, el operador."""

    key: str
    label: str
    detail: str
    automatic: bool
    state: Literal["ok", "pendiente", "atencion"]
    done_at: dt.date | None = None
    note: str = ""


class ProgressOut(BaseModel):
    """Cuántos sets van, de los que el trámite pide."""

    sets_total: int
    sets_declared: int
    sets_accepted: int
    sets_pending: int


class CertificationDossierOut(BaseModel):
    """Todo el expediente de un cliente."""

    customer_id: int
    progress: ProgressOut
    steps: list[StepOut]
    sets: list[CertificationSetOut]
    # Envíos capturados que todavía no se atribuyeron a ningún set. Existen
    # porque la captura no exige declarar el set: preferimos guardar el envío
    # sin clasificar antes que perderlo.
    unassigned: list[CertificationSubmissionOut]


class AssignSetRequest(BaseModel):
    """Atribuye un envío suelto a un set. Si el set no existe, se crea."""

    code: str = Field(min_length=1, max_length=20, examples=["5038170"])
    kind: str = Field("", max_length=30)


class SetupRequest(BaseModel):
    """Da de alta los sets que el SII asignó a este contribuyente.

    El número de atención lo asigna el Servicio y se copia una vez desde Mi SII.
    Los que se dejen vacíos quedan sin dar de alta y siguen apareciendo como
    pendientes: es preferible verlos que ocultarlos.
    """

    # kind del catálogo → número de atención.
    codes: dict[str, str]


class StepRequest(BaseModel):
    done_at: dt.date | None = None
    note: str = ""


class DeclareRequest(BaseModel):
    """Marca que el avance del set se declaró en Mi SII.

    Lleva fecha porque puede declararse días después del envío, y es la fecha
    que el SII pide informar.
    """

    declared_at: dt.date


class NoteRequest(BaseModel):
    set_id: int
    text: str = Field(min_length=1)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    set_id: int
    author: str
    text: str
    created_at: dt.datetime


class DefinitionRequest(BaseModel):
    """Qué emitir para este set: el cuerpo tal como lo espera el endpoint.

    Se guarda literal para que lo que se revisa y lo que se envía sean lo mismo,
    sin traducción intermedia donde perder un campo.
    """

    endpoint: Literal[
        "issue-batch", "issue-export-batch", "issue-settlement-batch", "books", "books/guides"
    ]
    payload: dict


class DefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    set_id: int
    endpoint: str
    payload: dict
    updated_at: dt.datetime


class CloneRequest(BaseModel):
    """Copia la definición del mismo tipo de set desde otro cliente ya probado.

    Es lo que hace barato el segundo contribuyente: partir de un set que el SII
    ya aceptó y ajustar montos, en vez de transcribir desde cero.
    """

    from_customer_id: int


class EnvelopeOut(BaseModel):
    """El sobre tal como se subió, para reimprimir o reenviar."""

    submission_id: int
    track_id: str | None
    filename: str
    xml_base64: str


class PrintSampleOut(BaseModel):
    """Un documento impreso dentro de las muestras."""

    track_id: str | None = None
    type: int | None = None
    folio: int | None = None
    html: str | None = None


class PrintSamplesOut(BaseModel):
    """Las muestras de impresión del expediente.

    ``skipped`` es tan importante como ``documents``: si un sobre no se pudo
    imprimir hay que saberlo antes de mandarle el PDF al SII, no después.
    """

    documents: list[dict]
    skipped: list[dict]
