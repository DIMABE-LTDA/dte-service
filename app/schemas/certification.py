"""Schemas del expediente de certificación."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class DocStatsOut(BaseModel):
    """Cuántos documentos de un tipo aceptó y rechazó el SII."""

    doc_type: int
    informed: int = 0
    accepted: int = 0
    rejected: int = 0
    flagged: int = 0


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
    #: Desglose del SII por tipo de documento. Vacío en libros y en envíos que
    #: aún no se han consultado.
    stats: list[DocStatsOut] = []


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


class PreviewOut(BaseModel):
    """Qué se va a emitir, en cristiano.

    ``note`` existe para que nadie lea la suma de las líneas como el total del
    documento: ese lo calcula el motor y sólo se conoce tras emitir.
    """

    kind: str
    summary: str
    detail: str
    note: str
    documents: list[dict]
    #: Qué completó el sistema o qué falta para poder hacerlo: datos del emisor
    #: sin configurar, sets todavía sin aceptar para armar un libro...
    system_notes: list[str] = []


class ContentsOut(BaseModel):
    """Qué contiene de verdad un sobre ya emitido, leído de su XML firmado."""

    submission_id: int
    track_id: str | None
    documents: list[dict]


class CertificationCustomerOut(BaseModel):
    """Un contribuyente en certificación, con su avance, para el índice.

    Existe porque quien opera esto lleva varias certificaciones a la vez y
    necesita ver cuál está atascada sin abrir las diez fichas una por una.
    """

    customer_id: int
    name: str
    rut: str
    key: str
    progress: ProgressOut
    #: Fecha del último envío al SII. None si todavía no se ha enviado nada.
    last_activity: dt.datetime | None = None


class ImportRequest(BaseModel):
    """Carga en bloque las definiciones de los sets de un contribuyente."""

    # kind → {code, endpoint, payload}
    sets: dict[str, dict]


class TemplateSetOut(BaseModel):
    """Un set de la plantilla: qué es y qué hay que transcribir de él."""

    kind: str
    label: str
    endpoint: str
    help: str
    #: Falso en los libros de ventas y de guías, cuyas líneas arma el sistema:
    #: al operador sólo le piden su número de atención.
    transcribe: bool


class TemplateRequest(BaseModel):
    """Crea los sets desde la plantilla con los números de atención del PDF.

    kind → número de atención. Los que vengan vacíos se omiten: el SII no
    siempre asigna los diez de una vez.
    """

    codes: dict[str, str]


class CheckOut(BaseModel):
    """Una comprobación: qué pasa y qué hacer."""

    key: str
    label: str
    state: Literal["ok", "atencion", "error"]
    detail: str
    fix: str = ""


class CheckGroupOut(BaseModel):
    key: str
    label: str
    state: Literal["ok", "atencion", "error"]
    checks: list[CheckOut]


class ReadinessOut(BaseModel):
    """Si el cliente puede emitir los sets, y si no, qué falta.

    `ready` es falso ante cualquier error. Los avisos no bloquean: dicen cosas
    que conviene saber antes de gastar folios.
    """

    ready: bool
    errors: int
    warnings: int
    checked_at: dt.datetime
    groups: list[CheckGroupOut]


class ReceiverOut(BaseModel):
    """Un cliente real del contribuyente, para recibir documentos del set."""

    rut: str
    business_name: str = Field(max_length=100)
    activity: str = Field(max_length=40)  # GiroRecep
    address: str = Field(max_length=70)
    commune: str = Field(max_length=20)
    city: str = Field("", max_length=20)

    @field_validator("rut")
    @classmethod
    def _rut(cls, v: str) -> str:
        from app.schemas.validators import normalize_rut

        return normalize_rut(v)


class ReceiversRequest(BaseModel):
    receivers: list[ReceiverOut] = Field(default_factory=list, max_length=50)
