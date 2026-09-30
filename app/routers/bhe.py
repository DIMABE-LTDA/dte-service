"""Endpoints de BHE per-tenant (cada cliente consulta SUS boletas recibidas).

A diferencia del RCV, la credencial es la **clave tributaria** del SII (login web),
no el certificado: las Boletas de Honorarios recibidas se consultan por ese canal.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.deps.auth import require_bhe
from app.deps.sii_credential import check_sii_portal_quota, sii_pwd_bhe
from app.schemas.bhe import BheReceivedOut, BheReceivedRequest, BheReceivedResponse
from app.services import bhe_service

router = APIRouter(prefix="/bhe", tags=["BHE"])


@router.post("/received", response_model=BheReceivedResponse)
async def received(
    req: BheReceivedRequest,
    customer: Customer = Depends(require_bhe),
    password: str = Depends(sii_pwd_bhe),
) -> BheReceivedResponse:
    check_sii_portal_quota(customer.id)
    # El RUT receptor es el del propio cliente (resuelto por el tenant).
    year, month = int(req.period[:4]), int(req.period[4:])
    docs = await run_blocking(bhe_service.list_received, customer.rut, password, year, month)
    return BheReceivedResponse(
        receiver_rut=customer.rut,
        period=req.period,
        count=len(docs),
        documents=[BheReceivedOut.model_validate(d) for d in docs],
    )
