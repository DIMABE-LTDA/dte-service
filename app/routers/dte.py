"""Endpoints de emisión de DTE."""

from __future__ import annotations

from dte_chile.certificate import Certificate
from dte_chile.sii_client import Environment, SIIClient
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_dte
from app.deps.certificate import cert_dte
from app.schemas.dte import (
    DteBatchDocumentOut,
    DteBatchRequest,
    DteBatchResponse,
    DteIssueRequest,
    DteIssueResponse,
    ExportBatchRequest,
    ExportIssueRequest,
    PrintedDocumentOut,
    PrintRequest,
    PrintResponse,
    SettlementBatchRequest,
    SettlementIssueRequest,
    SubmissionResultOut,
)
from app.services import dte_service

router = APIRouter(prefix="/dte", tags=["DTE"])


@router.post("/issue", response_model=DteIssueResponse)
async def issue(
    req: DteIssueRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    result = await run_blocking(dte_service.issue, db, customer, cert, req)
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
    )


@router.post("/issue-batch", response_model=DteBatchResponse)
async def issue_batch(
    req: DteBatchRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N documentos dentro de un único EnvioDTE (un set de certificación)."""
    result = await run_blocking(dte_service.issue_batch, db, customer, cert, req)
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
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
    return SubmissionResultOut.model_validate(res)


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
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    """Emite una Liquidación Factura Electrónica (tipo 43)."""
    result = await run_blocking(dte_service.issue_settlement, db, customer, cert, req)
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
    )


@router.post("/issue-export", response_model=DteIssueResponse)
async def issue_export(
    req: ExportIssueRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteIssueResponse:
    """Emite factura (110) o nota (111/112) de exportación."""
    result = await run_blocking(dte_service.issue_export, db, customer, cert, req)
    submission = result["submission"]
    return DteIssueResponse(
        type=result["type"],
        folio=result["folio"],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
    )


@router.post("/issue-export-batch", response_model=DteBatchResponse)
async def issue_export_batch(
    req: ExportBatchRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N documentos de exportación dentro de un único sobre."""
    result = await run_blocking(dte_service.issue_export_batch, db, customer, cert, req)
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
    )


@router.post("/issue-settlement-batch", response_model=DteBatchResponse)
async def issue_settlement_batch(
    req: SettlementBatchRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> DteBatchResponse:
    """Emite N liquidaciones dentro de un único sobre."""
    result = await run_blocking(dte_service.issue_settlement_batch, db, customer, cert, req)
    submission = result["submission"]
    return DteBatchResponse(
        documents=[DteBatchDocumentOut(**d) for d in result["documents"]],
        xml_base64=result["xml_base64"],
        submission=SubmissionResultOut.model_validate(submission) if submission else None,
    )
