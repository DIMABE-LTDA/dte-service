"""Endpoints de acuses de intercambio (responder un EnvioDTE recibido)."""

from __future__ import annotations

from dte_chile.certificate import Certificate
from fastapi import APIRouter, Depends

from app.core.concurrency import run_blocking
from app.db.models import Customer
from app.deps.auth import require_exchange
from app.deps.certificate import cert_exchange
from app.schemas.exchange import (
    AcknowledgmentRequest,
    ClaimQuery,
    ClaimRequest,
    ClaimResultOut,
    ExchangeResponse,
    InspectRequest,
    InspectResponse,
    ReceiptsRequest,
    ResultRequest,
)
from app.services import exchange_service

router = APIRouter(prefix="/exchange", tags=["Intercambio"])


@router.post("/inspect", response_model=InspectResponse)
async def inspect(
    req: InspectRequest, customer: Customer = Depends(require_exchange)
) -> InspectResponse:
    """Lee el sobre recibido y dice qué documentos trae y para quién."""
    datos = await run_blocking(exchange_service.inspect, req.envelope_base64, customer.rut)
    return InspectResponse(**datos)


@router.post("/ack", response_model=ExchangeResponse)
async def acknowledgment(
    req: AcknowledgmentRequest, cert: Certificate = Depends(cert_exchange)
) -> ExchangeResponse:
    xml_b64 = await run_blocking(exchange_service.acknowledgment, cert, req.envelope_base64)
    return ExchangeResponse(xml_base64=xml_b64)


@router.post("/result", response_model=ExchangeResponse)
async def result(
    req: ResultRequest, cert: Certificate = Depends(cert_exchange)
) -> ExchangeResponse:
    xml_b64 = await run_blocking(
        exchange_service.result, cert, req.envelope_base64, req.accept, req.rejection_label
    )
    return ExchangeResponse(xml_base64=xml_b64)


@router.post("/receipts", response_model=ExchangeResponse)
async def receipts(
    req: ReceiptsRequest, cert: Certificate = Depends(cert_exchange)
) -> ExchangeResponse:
    xml_b64 = await run_blocking(exchange_service.receipts, cert, req.envelope_base64, req.location)
    return ExchangeResponse(xml_base64=xml_b64)


@router.post("/claim", response_model=ClaimResultOut)
async def claim(
    req: ClaimRequest,
    customer: Customer = Depends(require_exchange),
    cert: Certificate = Depends(cert_exchange),
) -> ClaimResultOut:
    """Registra en el SII la aceptación o el reclamo de un documento recibido.

    Distinto de ``/exchange/result``, que responde al proveedor: esto es lo
    que corre el plazo de ocho días de la Ley 19.983.
    """
    datos = await run_blocking(
        exchange_service.register_claim,
        customer,
        cert,
        req.issuer_rut,
        req.doc_type,
        req.folio,
        req.action,
    )
    return ClaimResultOut(**datos)


@router.post("/claim/history", response_model=ClaimResultOut)
async def claim_history(
    req: ClaimQuery,
    customer: Customer = Depends(require_exchange),
    cert: Certificate = Depends(cert_exchange),
) -> ClaimResultOut:
    """El historial del documento en el SII: qué se registró y cuándo."""
    datos = await run_blocking(
        exchange_service.claim_history, customer, cert, req.issuer_rut, req.doc_type, req.folio
    )
    return ClaimResultOut(**datos)
