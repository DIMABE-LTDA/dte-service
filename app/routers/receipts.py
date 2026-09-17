"""Endpoints de boleta electrónica (39/41) y consumo de folios (RCOF)."""

from __future__ import annotations

from dte_chile.certificate import Certificate
from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    ReceiptSubmissionOut,
    ReceiptTotalsOut,
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


# --------------------------------------------------------------------------- #
#  Cuadratura de envíos y recuperación de boletas
# --------------------------------------------------------------------------- #
def _submission_out(row) -> ReceiptSubmissionOut:
    out = ReceiptSubmissionOut.model_validate(row)
    out.totals = ReceiptTotalsOut(**receipt_service.summarize(row.sii_stats))
    out.in_process = row.sii_state is None or row.sii_state in receipt_service.IN_PROCESS_STATES
    return out


@router.get("/submissions", response_model=list[ReceiptSubmissionOut])
def submissions(
    limit: int = Query(100, ge=1, le=500),
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> list[ReceiptSubmissionOut]:
    """Envíos de boletas, del más reciente al más antiguo, con su cuadratura."""
    return [_submission_out(r) for r in receipt_service.list_submissions(db, customer, limit)]


@router.post("/submissions/{track_id}/refresh", response_model=ReceiptSubmissionOut)
async def refresh_submission(
    track_id: str,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> ReceiptSubmissionOut:
    """Consulta al SII el estado del envío: aceptadas, con reparos y rechazadas."""
    try:
        row = await run_blocking(receipt_service.refresh_submission, db, customer, cert, track_id)
    except LookupError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    return _submission_out(row)


@router.get("/{doc_type}/{folio}/xml", responses={200: {"content": {"application/xml": {}}}})
def receipt_xml(
    doc_type: int,
    folio: int,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> Response:
    """El XML firmado de una boleta emitida, para entregarlo cuando el SII lo pida."""
    try:
        xml = receipt_service.stored_receipt_xml(db, customer, doc_type, folio)
    except LookupError as ex:
        raise HTTPException(status_code=404, detail=str(ex)) from ex
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="boleta_{doc_type}_{folio}.xml"'},
    )
