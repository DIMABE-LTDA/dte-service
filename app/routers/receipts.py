"""Endpoints de boleta electrónica (39/41) y consumo de folios (RCOF)."""

from __future__ import annotations

from dte_chile.certificate import Certificate
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_dte
from app.deps.certificate import cert_dte
from app.schemas.receipt import (
    FolioReportRequest,
    FolioReportResponse,
    ReceiptBatchRequest,
    ReceiptBatchResponse,
    ReceiptOut,
    SubmissionOut,
)
from app.services import receipt_service

router = APIRouter(prefix="/boletas", tags=["Boleta electrónica"])


def _submission(raw) -> SubmissionOut | None:
    return SubmissionOut.model_validate(raw) if raw else None


@router.post("/issue-batch", response_model=ReceiptBatchResponse)
async def issue_batch(
    req: ReceiptBatchRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> ReceiptBatchResponse:
    """Emite N boletas en un único EnvioBOLETA y lo sube por la API REST del SII."""
    result = await run_blocking(receipt_service.issue_batch, db, customer, cert, req)
    return ReceiptBatchResponse(
        receipts=[ReceiptOut(**r) for r in result["receipts"]],
        xml_base64=result["xml_base64"],
        submission=_submission(result["submission"]),
    )


@router.post("/folio-report", response_model=FolioReportResponse)
async def folio_report(
    req: FolioReportRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
) -> FolioReportResponse:
    """Reporte de Consumo de Folios (RCOF) del período."""
    result = await run_blocking(receipt_service.send_folio_report, customer, cert, req)
    return FolioReportResponse(
        start_date=result["start_date"],
        end_date=result["end_date"],
        xml_base64=result["xml_base64"],
        submission=_submission(result["submission"]),
    )
