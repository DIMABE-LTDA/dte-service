"""Endpoints de administración (datos maestros + RCV de operador).

Acceso vía ``admin_access``: JWT con rol de escritura (operador/superadmin) **o**
la ``X-Admin-Key`` de máquina (Odoo). Las mutaciones quedan en ``AdminAudit``.

Endpoints ``def`` (síncronos): corren en el threadpool, así la BD, el cifrado
y las llamadas al SII no bloquean el event loop.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db.models import Customer, User
from app.db.session import get_db
from app.schemas.admin import (
    CafInfo,
    CafOut,
    CafUpload,
    CertificateInfo,
    CertificateOut,
    CertificateUpload,
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    GrantedServiceOut,
    ServiceGrant,
    ServiceGrantOut,
    ServiceInfo,
    SiiKeyOut,
    SiiKeyStatus,
    SiiKeyUpload,
)
from app.schemas.bhe import BheReceivedOut, BheReceivedRequest, BheReceivedResponse
from app.schemas.rcv import RcvDocumentOut, RcvDocumentsRequest, RcvDocumentsResponse
from app.security.auth import admin_access, admin_read_access
from app.security.service_codes import ALL_SERVICES
from app.services import (
    audit_service,
    bhe_service,
    certificate_service,
    customer_service,
    rcv_service,
    sii_credential_service,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def _get_customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="cliente no encontrado")
    return customer


def _actor_id(actor: User | None) -> int | None:
    return actor.id if actor else None


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[Customer]:
    return customer_service.list_customers(
        db, limit=limit, offset=offset, include_deleted=include_deleted
    )


@router.get("/services", response_model=list[ServiceInfo])
def list_services(actor: User | None = Depends(admin_read_access)) -> list[ServiceInfo]:
    return [ServiceInfo(code=code, name=name) for code, name in ALL_SERVICES.items()]


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> Customer:
    return _get_customer(db, customer_id)


@router.get("/customers/{customer_id}/services", response_model=list[GrantedServiceOut])
def customer_services(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[GrantedServiceOut]:
    customer = _get_customer(db, customer_id)
    return [
        GrantedServiceOut(service_code=s.code, name=s.name)
        for s in customer_service.list_services_for(db, customer)
    ]


@router.get("/customers/{customer_id}/certificates", response_model=list[CertificateInfo])
def customer_certificates(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[CertificateInfo]:
    customer = _get_customer(db, customer_id)
    today = dt.date.today()
    return [
        CertificateInfo(
            id=c.id,
            due_date=c.due_date,
            created_at=c.created_at,
            expired=c.due_date < today,
            rut=c.rut,
            holder=c.holder,
            issuer=c.issuer,
        )
        for c in customer_service.list_certificates(db, customer)
    ]


@router.get("/customers/{customer_id}/cafs", response_model=list[CafInfo])
def customer_cafs(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[CafInfo]:
    customer = _get_customer(db, customer_id)
    pointers = customer_service.folio_pointers(db, customer_id)
    return [
        CafInfo(
            id=c.id,
            doc_type=c.doc_type,
            folio_from=c.folio_from,
            folio_to=c.folio_to,
            exhausted=c.exhausted,
            last_folio=pointers.get(c.doc_type, 0),
        )
        for c in customer_service.list_cafs(db, customer)
    ]


@router.post("/customers", response_model=CustomerOut)
def create_customer(
    data: CustomerCreate,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> Customer:
    customer = customer_service.create_customer(db, data, commit=False)
    audit_service.record_change(
        db, _actor_id(actor), "customer.create", "customer", str(customer.id), customer.name
    )
    return customer


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> Customer:
    customer = _get_customer(db, customer_id)
    customer_service.update_customer(db, customer, data, commit=False)
    audit_service.record_change(
        db, _actor_id(actor), "customer.update", "customer", str(customer.id), customer.name
    )
    return customer


@router.delete("/customers/{customer_id}", response_model=CustomerOut)
def delete_customer(
    customer_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> Customer:
    """Soft delete: archiva el cliente (conserva historial fiscal y de auditoría)."""
    customer = _get_customer(db, customer_id)
    customer_service.soft_delete_customer(db, customer, commit=False)
    audit_service.record_change(
        db, _actor_id(actor), "customer.delete", "customer", str(customer.id), customer.name
    )
    return customer


@router.post("/customers/{customer_id}/restore", response_model=CustomerOut)
def restore_customer(
    customer_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> Customer:
    customer = _get_customer(db, customer_id)
    customer_service.restore_customer(db, customer, commit=False)
    audit_service.record_change(
        db, _actor_id(actor), "customer.restore", "customer", str(customer.id), customer.name
    )
    return customer


@router.post("/customers/{customer_id}/certificate", response_model=CertificateOut)
def upload_certificate(
    customer_id: int,
    data: CertificateUpload,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificateOut:
    customer = _get_customer(db, customer_id)
    row = certificate_service.store_certificate(
        db, customer, data.file_base64, data.password, commit=False
    )
    audit_service.record_change(
        db,
        _actor_id(actor),
        "certificate.upload",
        "customer",
        str(customer.id),
        f"vence {row.due_date}",
    )
    return CertificateOut(id=row.id, rut=customer.rut, due_date=row.due_date)


@router.post("/customers/{customer_id}/sii-key", response_model=SiiKeyOut)
def upload_sii_key(
    customer_id: int,
    data: SiiKeyUpload,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> SiiKeyOut:
    """Guarda (cifrada) la clave tributaria del SII del cliente, usada para
    consultar las Boletas de Honorarios recibidas (login web BHE)."""
    customer = _get_customer(db, customer_id)
    sii_credential_service.store_sii_password(db, customer, data.password, commit=False)
    audit_service.record_change(
        db,
        _actor_id(actor),
        "sii_key.upload",
        "customer",
        str(customer.id),
        "clave tributaria guardada",
    )
    return SiiKeyOut(customer_id=customer.id)


@router.get("/customers/{customer_id}/sii-key", response_model=SiiKeyStatus)
def sii_key_status(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> SiiKeyStatus:
    """Indica si el cliente tiene clave tributaria configurada (no la expone)."""
    customer = _get_customer(db, customer_id)
    return SiiKeyStatus(configured=sii_credential_service.has_sii_password(db, customer))


@router.delete("/customers/{customer_id}/sii-key", response_model=SiiKeyStatus)
def delete_sii_key(
    customer_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> SiiKeyStatus:
    customer = _get_customer(db, customer_id)
    sii_credential_service.clear_sii_password(db, customer, commit=False)
    audit_service.record_change(
        db,
        _actor_id(actor),
        "sii_key.delete",
        "customer",
        str(customer.id),
        "clave tributaria eliminada",
    )
    return SiiKeyStatus(configured=False)


@router.post("/customers/{customer_id}/caf", response_model=CafOut)
def upload_caf(
    customer_id: int,
    data: CafUpload,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CafOut:
    customer = _get_customer(db, customer_id)
    row = customer_service.add_caf(db, customer, data.xml_base64, commit=False)
    audit_service.record_change(
        db,
        _actor_id(actor),
        "caf.upload",
        "customer",
        str(customer.id),
        f"tipo {row.doc_type} folios {row.folio_from}-{row.folio_to}",
    )
    return CafOut(
        id=row.id, doc_type=row.doc_type, folio_from=row.folio_from, folio_to=row.folio_to
    )


@router.delete("/customers/{customer_id}/certificates/{cert_id}", status_code=204)
def delete_certificate(
    customer_id: int,
    cert_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> Response:
    """Retira un certificado de la ficha.

    Hace falta porque se firma con el más reciente: uno de prueba cargado por
    error queda arriba y no hay forma de quitarlo de en medio.
    """
    customer = _get_customer(db, customer_id)
    row = customer_service.delete_certificate(db, customer, cert_id, commit=False)
    audit_service.record_change(
        db,
        _actor_id(actor),
        "certificate.delete",
        "customer",
        str(customer.id),
        f"certificado {row.id} ({row.holder or 's/titular'}, RUT {row.rut or '—'}) eliminado",
    )
    db.commit()
    return Response(status_code=204)


@router.post("/customers/{customer_id}/cafs/{caf_id}/retire", response_model=CafInfo)
def retire_caf(
    customer_id: int,
    caf_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CafInfo:
    """Saca de circulación un CAF que aún tiene folios libres.

    Se usa cuando llega un CAF que debe reemplazar al vigente: el asignador
    siempre toma el rango disponible más bajo, así que sin retirar el viejo
    nunca llegaría a usar el nuevo.
    """
    customer = _get_customer(db, customer_id)
    row = customer_service.retire_caf(db, customer, caf_id, commit=False)
    audit_service.record_change(
        db,
        _actor_id(actor),
        "caf.retire",
        "customer",
        str(customer.id),
        f"tipo {row.doc_type} folios {row.folio_from}-{row.folio_to} fuera de uso",
    )
    pointers = customer_service.folio_pointers(db, customer_id)
    return CafInfo(
        id=row.id,
        doc_type=row.doc_type,
        folio_from=row.folio_from,
        folio_to=row.folio_to,
        exhausted=row.exhausted,
        last_folio=pointers.get(row.doc_type, 0),
    )


@router.post("/customers/{customer_id}/services", response_model=ServiceGrantOut)
def grant_service(
    customer_id: int,
    data: ServiceGrant,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> ServiceGrantOut:
    customer = _get_customer(db, customer_id)
    raw_key = customer_service.grant_service(
        db, customer, data.service_code, data.apikey, commit=False
    )
    audit_service.record_change(
        db, _actor_id(actor), "service.grant", "customer", str(customer.id), data.service_code
    )
    # Devolvemos la apiKey solo si la generó el servidor (el llamador no la envió).
    return ServiceGrantOut(
        service_code=data.service_code,
        granted=True,
        apikey=None if data.apikey else raw_key,
    )


@router.delete("/customers/{customer_id}/services/{service_code}", response_model=ServiceGrantOut)
def revoke_service(
    customer_id: int,
    service_code: str,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> ServiceGrantOut:
    customer = _get_customer(db, customer_id)
    removed = customer_service.revoke_service(db, customer, service_code, commit=False)
    if not removed:
        raise HTTPException(status_code=404, detail="el cliente no tiene ese servicio")
    audit_service.record_change(
        db, _actor_id(actor), "service.revoke", "customer", str(customer.id), service_code
    )
    return ServiceGrantOut(service_code=service_code, granted=False)


@router.post("/customers/{customer_id}/rcv", response_model=RcvDocumentsResponse)
def customer_rcv(
    customer_id: int,
    req: RcvDocumentsRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> RcvDocumentsResponse:
    """RCV de operador: compras/ventas de cualquier cliente usando su cert guardado
    (para Odoo con X-Admin-Key o un operador desde el panel con JWT)."""
    customer = _get_customer(db, customer_id)
    docs = rcv_service.list_documents_for_customer(db, customer, req.period, req.operation)
    return RcvDocumentsResponse(
        issuer_rut=customer.rut,
        period=req.period,
        operation=req.operation,
        count=len(docs),
        documents=[RcvDocumentOut.model_validate(d) for d in docs],
    )


@router.post("/customers/{customer_id}/bhe", response_model=BheReceivedResponse)
def customer_bhe(
    customer_id: int,
    req: BheReceivedRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> BheReceivedResponse:
    """BHE de operador: boletas de honorarios recibidas de cualquier cliente usando
    su clave tributaria guardada (para Odoo con X-Admin-Key o un operador con JWT)."""
    customer = _get_customer(db, customer_id)
    year, month = int(req.period[:4]), int(req.period[4:])
    docs = bhe_service.list_received_for_customer(db, customer, year, month)
    return BheReceivedResponse(
        receiver_rut=customer.rut,
        period=req.period,
        count=len(docs),
        documents=[BheReceivedOut.model_validate(d) for d in docs],
    )
