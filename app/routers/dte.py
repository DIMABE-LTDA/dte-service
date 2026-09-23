"""Endpoints de emisión de DTE."""

from __future__ import annotations

from typing import Annotated, Literal

from dte_chile.certificate import Certificate
from dte_chile.sii_client import Environment, SIIClient
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.concurrency import run_blocking
from app.core.config import get_settings
from app.core.logging import request_id_var
from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_dte
from app.deps.certificate import cert_dte
from app.schemas.admin import FolioTypeReportOut
from app.schemas.dte import (
    CustomsCodeOut,
    CustomsTablesOut,
    DteBatchDocumentOut,
    DteBatchRequest,
    DteBatchResponse,
    DteIssueRequest,
    DteIssueResponse,
    ExportBatchRequest,
    ExportIssueRequest,
    FolioReservationOut,
    FolioReservationRequest,
    PrintedDocumentOut,
    PrintRequest,
    PrintResponse,
    SettlementBatchRequest,
    SettlementIssueRequest,
    SubmissionResultOut,
)
from app.services import dte_service, folio_service, idempotency, pdf_service

#: Clave que el ERP deriva de su propio documento (p. ej. `odoo:<bd>:<id>`).
#: Sin ella se emite como antes: cada petición, un documento.
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description=(
            "Clave estable del documento en el sistema que llama. Con ella, "
            "reintentar devuelve lo ya emitido en vez de emitir otro documento."
        ),
        max_length=100,
    ),
]


async def _emitir(db, customer, key: str | None, endpoint: str, fn, *args) -> dict:
    """Emite cuidando que un reintento no emita dos veces."""
    try:
        return await run_blocking(idempotency.run, db, customer, key, endpoint, lambda: fn(*args))
    except idempotency.EmissionInProgress as ex:
        raise HTTPException(status_code=409, detail=str(ex)) from ex
    except idempotency.EmissionAlreadyFailed as ex:
        raise HTTPException(status_code=409, detail=str(ex)) from ex


router = APIRouter(prefix="/dte", tags=["DTE"])


def _estado(submission) -> SubmissionResultOut | None:
    """La respuesta del SII, diciendo además si todavía está en proceso.

    Un sobre «EPR – envío procesado» no significa documento aceptado: eso lo
    dice el desglose. Publicarlo evita que quien integra lea un envío recibido
    como un documento aceptado.
    """
    if submission is None:
        return None
    salida = SubmissionResultOut.model_validate(submission)
    salida.in_process = dte_service.in_process(salida.status)
    return salida


@router.post("/issue", response_model=DteIssueResponse)
async def issue(
    req: DteIssueRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    result = await _emitir(
        db, customer, idempotency_key, "dte.issue", dte_service.issue, db, customer, cert, req
    )
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.post("/issue-batch", response_model=DteBatchResponse)
async def issue_batch(
    req: DteBatchRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N documentos dentro de un único EnvioDTE (un set de certificación)."""
    result = await _emitir(
        db,
        customer,
        idempotency_key,
        "dte.issue-batch",
        dte_service.issue_batch,
        db,
        customer,
        cert,
        req,
    )
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.get("/status/{track_id}", response_model=SubmissionResultOut)
async def status(
    track_id: str,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
) -> SubmissionResultOut:
    client = SIIClient(cert, Environment[customer.environment.name])
    try:
        res = await run_blocking(client.query_status, track_id, customer.rut)
    finally:
        client.session.close()  # liberar la sesión HTTP
    salida = _estado(res)
    assert salida is not None
    return salida


@router.post("/print", response_model=PrintResponse)
async def print_documents(
    req: PrintRequest,
    customer: Customer = Depends(require_dte),
) -> PrintResponse:
    """Representación impresa (ejemplar tributario y cedible) de un sobre emitido."""
    result = await run_blocking(dte_service.print_documents, customer, req)
    return PrintResponse(documents=[PrintedDocumentOut(**d) for d in result["documents"]])


@router.post("/issue-settlement", response_model=DteIssueResponse)
async def issue_settlement(
    req: SettlementIssueRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    """Emite una Liquidación Factura Electrónica (tipo 43)."""
    result = await _emitir(
        db,
        customer,
        idempotency_key,
        "dte.issue-settlement",
        dte_service.issue_settlement,
        db,
        customer,
        cert,
        req,
    )
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.get("/customs", response_model=CustomsTablesOut)
def customs_tables(_customer: Customer = Depends(require_dte)) -> CustomsTablesOut:
    """Las tablas de Aduana que pide un documento de exportación.

    Van desde el facturador y no copiadas en el ERP: los códigos los fija el
    Compendio de Aduana, y una tabla vieja en el cliente significa documentos
    rechazados con folio ya gastado.
    """
    from dte_chile import customs_codes as tablas

    def _filas(tabla: dict[str, int]) -> list[CustomsCodeOut]:
        return [CustomsCodeOut(code=code, name=name) for name, code in tabla.items()]

    return CustomsTablesOut(
        countries=_filas(tablas.COUNTRIES),
        ports=_filas(tablas.PORTS),
        transport_routes=_filas(tablas.TRANSPORT_ROUTES),
        sale_clauses=_filas(tablas.SALE_CLAUSES),
        sale_modes=_filas(tablas.SALE_MODES),
        payment_modes=_filas(tablas.PAYMENT_MODES),
        package_kinds=_filas(tablas.PACKAGE_TYPES),
        measure_units=_filas(tablas.MEASURE_UNITS),
        currencies=sorted(tablas.EXPORT_CURRENCIES),
        currency_by_iso=dict(tablas.CURRENCY_BY_ISO),
    )


@router.post("/issue-export", response_model=DteIssueResponse)
async def issue_export(
    req: ExportIssueRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    """Emite factura (110) o nota (111/112) de exportación."""
    result = await _emitir(
        db,
        customer,
        idempotency_key,
        "dte.issue-export",
        dte_service.issue_export,
        db,
        customer,
        cert,
        req,
    )
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.post("/issue-export-batch", response_model=DteBatchResponse)
async def issue_export_batch(
    req: ExportBatchRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N documentos de exportación dentro de un único sobre."""
    result = await _emitir(
        db,
        customer,
        idempotency_key,
        "dte.issue-export-batch",
        dte_service.issue_export_batch,
        db,
        customer,
        cert,
        req,
    )
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.post("/issue-settlement-batch", response_model=DteBatchResponse)
async def issue_settlement_batch(
    req: SettlementBatchRequest,
    idempotency_key: IdempotencyKey = None,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N liquidaciones dentro de un único sobre."""
    result = await _emitir(
        db,
        customer,
        idempotency_key,
        "dte.issue-settlement-batch",
        dte_service.issue_settlement_batch,
        db,
        customer,
        cert,
        req,
    )
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=_estado(submission),
    )


@router.get("/folios", response_model=list[FolioTypeReportOut])
def folios(
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> list[FolioTypeReportOut]:
    """Inventario de CAF y folios del propio cliente, con los que hay que revisar."""
    return [FolioTypeReportOut(**r) for r in folio_service.folio_report(db, customer)]


@router.get("/{doc_type}/{folio}/xml", responses={200: {"content": {"application/xml": {}}}})
def document_xml(
    doc_type: int,
    folio: int,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> Response:
    """El XML firmado de un documento ya emitido.

    El emisor sigue siendo quien debe conservar sus documentos; esto es la red
    de seguridad para cuando la respuesta de la emisión se perdió por el camino.
    """
    try:
        xml = idempotency.stored_xml(db, customer, doc_type, folio)
    except LookupError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="dte_{doc_type}_{folio}.xml"'},
    )


@router.post("/folios/reserve", response_model=FolioReservationOut)
async def reserve_folio(
    req: FolioReservationRequest,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> FolioReservationOut:
    """Reserva el siguiente folio de un tipo, sin emitir todavía.

    Existe para que un ERP numere su documento con el folio **antes** de
    emitirlo: si numera con su propia secuencia y el folio llega después, el
    número impreso y el del timbre acaban siendo distintos. El folio queda
    asignado; si no se usa, aparece como pendiente de revisión en el inventario.
    """
    folio, _caf = await run_blocking(
        folio_service.next_folio, db, customer.id, req.type, request_id_var.get()
    )
    return FolioReservationOut(
        type=req.type,
        folio=folio,
        environment=customer.environment.value
        if hasattr(customer.environment, "value")
        else str(customer.environment),
    )


@router.get(
    "/{doc_type}/{folio}/print",
    responses={200: {"content": {"application/pdf": {}, "text/html": {}}}},
)
def print_stored_document(
    doc_type: int,
    folio: int,
    copies: Literal["both", "tax", "transferable"] = "both",
    format: Literal["pdf", "html"] = "pdf",
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> Response:
    """La representación impresa de un documento ya emitido.

    En PDF por omisión: es lo que el ERP adjunta y lo que se manda al receptor.
    El impreso lleva el timbre del XML firmado, el recuadro con el folio y la
    unidad del SII, y la copia cedible cuando el documento la tiene.
    """
    try:
        html = dte_service.print_stored(
            db,
            customer,
            doc_type,
            folio,
            copies,
            verification_url=get_settings().receipt_verification_url,
        )
    except LookupError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex

    if format == "html":
        return Response(content=html, media_type="text/html; charset=utf-8")
    try:
        pdf = pdf_service.html_to_pdf(html)
    except pdf_service.PdfUnavailableError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="dte_{doc_type}_{folio}.pdf"'},
    )
