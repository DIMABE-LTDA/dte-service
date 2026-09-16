"""Consulta pública de boletas, para el consumidor final.

El SII exige que la boleta impresa indique un sitio donde el comprador pueda
recuperarla. Ese sitio es anónimo por naturaleza —quien tiene el papel no
tiene credenciales— así que este router vive aparte del resto:

- **Sin autenticación**, y sólo de lectura.
- **Sin acceso a nada más**: no expone clientes, certificados ni CAF.
- La búsqueda pide **folio + fecha + monto**. Los folios son correlativos: con
  sólo el folio cualquiera enumeraría todas las ventas del emisor. Esos tres
  datos los tiene quien recibió la boleta, y nadie más.
- **Límite de tasa por IP**, para que no se pueda tantear el monto a fuerza
  bruta.
"""

from __future__ import annotations

import base64
import datetime as dt

from dte_chile import parser
from dte_chile.representation import ResolutionInfo, generate_html
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.config import get_settings
from app.db.models import Customer, IssuedReceipt
from app.db.session import get_db
from app.schemas.validators import normalize_rut
from app.security.ratelimit import make_limiter

router = APIRouter(prefix="/public", tags=["Consulta pública"])

# Suficiente para quien busca su boleta y erra un par de veces; muy poco para
# tantear montos por fuerza bruta.
_lookup_limiter = make_limiter("publookup", get_settings().public_lookup_per_minute, 60.0)


class ReceiptLookupRequest(BaseModel):
    """Lo que el comprador tiene impreso en su boleta."""

    rut: str = Field(examples=["77262159-0"])  # RUT del emisor
    folio: int = Field(ge=1)
    issue_date: dt.date
    total_amount: int = Field(ge=0)


class ReceiptLookupResponse(BaseModel):
    folio: int
    doc_type: int
    issue_date: dt.date
    total_amount: int
    issuer_name: str
    html_base64: str  # la boleta lista para ver o imprimir


class IssuerInfo(BaseModel):
    """Datos públicos del emisor, para que la página se identifique."""

    rut: str
    name: str


def _limit(request: Request) -> None:
    client = request.client.host if request.client else "-"
    if _lookup_limiter.hit(client):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas consultas. Espera un minuto antes de reintentar.",
        )


@router.get("/issuers", response_model=list[IssuerInfo])
def issuers(db: Session = Depends(get_db)) -> list[IssuerInfo]:
    """Emisores que el comprador puede elegir en el sitio público.

    Sólo los que tienen al menos una boleta emitida: elegir a los demás no
    llevaría a ninguna parte, y así la lista no revela la cartera completa de
    clientes del servicio. La razón social y el RUT de un emisor de boletas son
    públicos de todos modos —van impresos en cada boleta que entrega—, así que
    no hay nada que proteger en los que sí aparecen.
    """
    # EXISTS y no JOIN + DISTINCT: Customer tiene columnas JSON, y PostgreSQL
    # no sabe comparar `json` para deduplicar (SQLite sí, por eso los tests
    # no lo veían).
    tiene_boletas = (
        select(IssuedReceipt.id).where(IssuedReceipt.customer_id == Customer.id).exists()
    )
    rows = db.execute(
        select(Customer).where(Customer.deleted_at.is_(None), tiene_boletas).order_by(Customer.name)
    ).scalars()
    return [IssuerInfo(rut=c.rut, name=c.name) for c in rows]


@router.get("/issuer/{rut}", response_model=IssuerInfo)
def issuer(rut: str, db: Session = Depends(get_db)) -> IssuerInfo:
    """Razón social del emisor. La página la usa para identificarse."""
    customer = _customer(db, rut)
    return IssuerInfo(rut=customer.rut, name=customer.name)


@router.post("/boletas/lookup", response_model=ReceiptLookupResponse)
def lookup(
    body: ReceiptLookupRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ReceiptLookupResponse:
    """Devuelve la boleta impresa si los cuatro datos calzan."""
    _limit(request)
    customer = _customer(db, body.rut)

    row = db.execute(
        select(IssuedReceipt).where(
            IssuedReceipt.customer_id == customer.id,
            IssuedReceipt.folio == body.folio,
            IssuedReceipt.issue_date == body.issue_date,
            IssuedReceipt.total_amount == body.total_amount,
        )
    ).scalar_one_or_none()

    # Mismo mensaje para "no existe" y "los datos no calzan": distinguirlos
    # permitiría confirmar qué folios existen.
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No encontramos una boleta con esos datos. Revisa el folio, "
            "la fecha y el monto tal como aparecen en tu comprobante.",
        )

    xml = crypto.decrypt(row.xml_encrypted)
    document = parser.parse_documents(xml)[0]
    html = generate_html(
        document.dte,
        document.element,
        ResolutionInfo(number=customer.resolution_number, date=customer.resolution_date),
        verification_url=get_settings().receipt_verification_url,
    )
    return ReceiptLookupResponse(
        folio=row.folio,
        doc_type=row.doc_type,
        issue_date=row.issue_date,
        total_amount=row.total_amount,
        issuer_name=customer.name,
        html_base64=base64.b64encode(html.encode("utf-8")).decode("ascii"),
    )


def _customer(db: Session, rut: str) -> Customer:
    customer = db.execute(
        select(Customer).where(Customer.rut == normalize_rut(rut), Customer.deleted_at.is_(None))
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Emisor no encontrado.")
    return customer
