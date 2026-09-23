"""Cesión electrónica de facturas: armar el AEC y anotarlo en el RPETC."""

from __future__ import annotations

from dte_chile.certificate import Certificate
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_dte
from app.deps.certificate import cert_dte
from app.schemas.cession import (
    CessionOut,
    CessionRequest,
    CessionStatusRequest,
    CessionSubmissionOut,
)
from app.services import cession_service

router = APIRouter(prefix="/cession", tags=["Cesión (factoring)"])


@router.post("", response_model=CessionOut)
async def build_cession(
    req: CessionRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
    db: Session = Depends(get_db),
) -> CessionOut:
    """Cede una factura emitida: arma el AEC firmado y lo anota en el SII.

    La factura sale del archivo del facturador —basta su tipo y folio—, así
    que el AEC lleva exactamente el documento que se emitió.
    """
    datos = await run_blocking(cession_service.build, db, customer, cert, req)
    return CessionOut(**datos)


@router.post("/status", response_model=CessionSubmissionOut)
async def cession_status(
    req: CessionStatusRequest,
    customer: Customer = Depends(require_dte),
    cert: Certificate = Depends(cert_dte),
) -> CessionSubmissionOut:
    """Estado del AEC en el Registro Público de Transferencia de Créditos."""
    datos = await run_blocking(cession_service.status, customer, cert, req.track_id)
    return CessionSubmissionOut(**datos)
