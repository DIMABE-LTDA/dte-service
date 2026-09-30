"""Endpoints de BTE per-tenant: las Boletas de Prestación de Servicios de Terceros
que el cliente emitió en el portal del SII.

La empresa emite una BTE cuando le paga a alguien que no emite su propia boleta,
y es ella la que retiene. Se consultan por el mismo canal y con la misma clave
tributaria que las BHE recibidas, así que usan el mismo servicio (``bhe``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.deps.auth import require_bhe
from app.deps.sii_credential import check_sii_portal_quota, sii_pwd_bhe
from app.schemas.bhe import BteIssuedOut, BteIssuedRequest, BteIssuedResponse
from app.services import bhe_service

router = APIRouter(prefix="/bte", tags=["BHE"])


@router.post("/issued", response_model=BteIssuedResponse)
async def issued(
    req: BteIssuedRequest,
    customer: Customer = Depends(require_bhe),
    password: str = Depends(sii_pwd_bhe),
) -> BteIssuedResponse:
    check_sii_portal_quota(customer.id)
    year, month = int(req.period[:4]), int(req.period[4:])
    docs = await run_blocking(bhe_service.list_issued_bte, customer.rut, password, year, month)
    return BteIssuedResponse(
        issuer_rut=customer.rut,
        period=req.period,
        count=len(docs),
        documents=[BteIssuedOut.model_validate(d) for d in docs],
    )
