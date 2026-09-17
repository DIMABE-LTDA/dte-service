"""Administración de clientes, servicios habilitados y CAF (operaciones de BD)."""

from __future__ import annotations

import base64
import binascii
import secrets

from dte_chile.caf import load_caf_bytes
from dte_chile.rut import format_rut
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import (
    Caf,
    Customer,
    CustomerCertificate,
    CustomerService,
    FolioPointer,
    Service,
    SiiEnvironment,
)
from app.errors.exceptions import DomainError
from app.security.apikeys import generate_apikey, hash_apikey
from app.security.service_codes import ALL_SERVICES
from app.services import folio_service


def generate_customer_key(db: Session) -> str:
    """customerCode opaco: token aleatorio URL-safe único (no revela el nombre)."""
    for _ in range(10):
        candidate = secrets.token_urlsafe(8)  # ~11 caracteres
        if db.query(Customer).filter(Customer.key == candidate).first() is None:
            return candidate
    return secrets.token_urlsafe(16)  # improbable: 10 colisiones seguidas


def list_customers(
    db: Session, *, limit: int = 100, offset: int = 0, include_deleted: bool = False
) -> list[Customer]:
    stmt = select(Customer).order_by(Customer.id)
    if not include_deleted:
        stmt = stmt.where(Customer.deleted_at.is_(None))
    return list(db.execute(stmt.limit(limit).offset(offset)).scalars())


def soft_delete_customer(db: Session, customer: Customer, *, commit: bool = True) -> Customer:
    """Archiva el cliente (soft delete). Conserva su historial fiscal y de auditoría."""
    if customer.deleted_at is None:
        customer.deleted_at = func.now()
        db.flush()
        db.refresh(customer)
        if commit:
            db.commit()
    return customer


def restore_customer(db: Session, customer: Customer, *, commit: bool = True) -> Customer:
    """Reactiva un cliente archivado."""
    if customer.deleted_at is not None:
        customer.deleted_at = None
        db.flush()
        db.refresh(customer)
        if commit:
            db.commit()
    return customer


def get_customer(db: Session, customer_id: int) -> Customer | None:
    return db.get(Customer, customer_id)


def list_services_for(db: Session, customer: Customer) -> list[Service]:
    return list(
        db.execute(
            select(Service)
            .join(CustomerService, CustomerService.service_id == Service.id)
            .where(CustomerService.customer_id == customer.id)
            .order_by(Service.name)
        ).scalars()
    )


def list_certificates(db: Session, customer: Customer) -> list[CustomerCertificate]:
    return list(
        db.execute(
            select(CustomerCertificate)
            .where(CustomerCertificate.customer_id == customer.id)
            .order_by(CustomerCertificate.created_at.desc())
        ).scalars()
    )


def list_cafs(db: Session, customer: Customer) -> list[Caf]:
    return list(
        db.execute(
            select(Caf).where(Caf.customer_id == customer.id).order_by(Caf.doc_type, Caf.folio_from)
        ).scalars()
    )


def folio_pointers(db: Session, customer_id: int) -> dict[int, int]:
    rows = db.execute(select(FolioPointer).where(FolioPointer.customer_id == customer_id)).scalars()
    return {r.doc_type: r.last_folio for r in rows}


def create_customer(db: Session, data, *, commit: bool = True) -> Customer:
    key = data.key or generate_customer_key(db)
    customer = Customer(
        name=data.name,
        key=key,
        rut=data.rut,
        environment=SiiEnvironment(data.environment),
        resolution_number=data.resolution_number,
        resolution_date=data.resolution_date,
    )
    db.add(customer)
    db.flush()
    db.refresh(customer)
    if commit:
        db.commit()
    return customer


#: Campo del perfil → columna del cliente.
_ISSUER_COLUMNS = {
    "legal_name": "issuer_legal_name",
    "activity": "issuer_activity",
    "economic_activity": "issuer_economic_activity",
    "address": "issuer_address",
    "commune": "issuer_commune",
    "city": "issuer_city",
    "branch_name": "issuer_branch_name",
    "branch_code": "issuer_branch_code",
    "sii_office": "issuer_sii_office",
}

#: Sin estos el SII no acepta el encabezado del documento.
ISSUER_REQUIRED = {
    "legal_name": "razón social",
    "activity": "giro",
    "economic_activity": "código ACTECO",
    "address": "dirección",
    "commune": "comuna",
}


def issuer_profile(customer: Customer) -> dict:
    """El perfil del emisor tal como está guardado."""
    return {campo: getattr(customer, col) for campo, col in _ISSUER_COLUMNS.items()}


def issuer_missing(customer: Customer) -> list[str]:
    """Qué dato obligatorio falta, en palabras de quien lo va a completar."""
    perfil = issuer_profile(customer)
    return [nombre for campo, nombre in ISSUER_REQUIRED.items() if not perfil.get(campo)]


def set_issuer(customer: Customer, data) -> None:
    """Reemplaza el perfil entero. Un texto en blanco queda vacío, no como ""."""
    valores = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    for campo, col in _ISSUER_COLUMNS.items():
        valor = valores.get(campo)
        if isinstance(valor, str):
            valor = valor.strip() or None
        setattr(customer, col, valor)


def issuer_block(customer: Customer) -> dict:
    """El bloque ``issuer`` de un documento, armado desde la ficha.

    Es lo que usa la emisión en vez de lo que viniera escrito en la definición:
    el emisor siempre es el cliente, y su RUT es el de la ficha.
    """
    perfil = issuer_profile(customer)
    bloque = {
        "rut": customer.rut,
        "business_name": perfil["legal_name"],
        "activity": perfil["activity"],
        "economic_activity": perfil["economic_activity"],
        "address": perfil["address"],
        "commune": perfil["commune"],
        "city": perfil["city"] or "",
    }
    if perfil["branch_name"]:
        bloque["branch_name"] = perfil["branch_name"]
    if perfil["branch_code"]:
        bloque["branch_code"] = perfil["branch_code"]
    return bloque


def update_customer(db: Session, customer: Customer, data, *, commit: bool = True) -> Customer:
    """Edición parcial: solo aplica los campos enviados (no nulos)."""
    if data.name is not None:
        customer.name = data.name
    if data.rut is not None:
        customer.rut = data.rut
    if data.environment is not None:
        customer.environment = SiiEnvironment(data.environment)
    if data.resolution_number is not None:
        customer.resolution_number = data.resolution_number
    if data.resolution_date is not None:
        customer.resolution_date = data.resolution_date
    if getattr(data, "issuer", None) is not None:
        set_issuer(customer, data.issuer)
    db.flush()
    db.refresh(customer)
    if commit:
        db.commit()
    return customer


def grant_service(
    db: Session,
    customer: Customer,
    service_code: str,
    apikey: str | None = None,
    *,
    commit: bool = True,
) -> str:
    """Habilita el servicio o, si ya estaba, **rota** su apiKey (idempotente).

    Si ``apikey`` es ``None``, se genera una aleatoria. Devuelve la apiKey en
    claro (el llamador la muestra UNA vez; en BD solo va el hash).
    """
    if service_code not in ALL_SERVICES:
        raise DomainError(f"service_code desconocido: {service_code}")
    raw_key = apikey or generate_apikey()
    service = db.query(Service).filter(Service.code == service_code).first()
    if service is None:
        service = Service(code=service_code, name=ALL_SERVICES[service_code])
        db.add(service)
        db.flush()
    existing = (
        db.query(CustomerService).filter_by(customer_id=customer.id, service_id=service.id).first()
    )
    if existing is not None:
        existing.apikey_hash = hash_apikey(raw_key)  # rotación
    else:
        db.add(
            CustomerService(
                customer_id=customer.id, service_id=service.id, apikey_hash=hash_apikey(raw_key)
            )
        )
    if commit:
        db.commit()
    return raw_key


def revoke_service(
    db: Session, customer: Customer, service_code: str, *, commit: bool = True
) -> bool:
    service = db.query(Service).filter(Service.code == service_code).first()
    if service is None:
        return False
    cs = db.query(CustomerService).filter_by(customer_id=customer.id, service_id=service.id).first()
    if cs is None:
        return False
    db.delete(cs)
    if commit:
        db.commit()
    return True


def _same_rut(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # sin RUT en el CAF: no se puede comparar, no se bloquea
    try:
        return format_rut(a) == format_rut(b)
    except ValueError:
        return a.strip() == b.strip()


def delete_certificate(
    db: Session, customer: Customer, cert_id: int, *, commit: bool = True
) -> CustomerCertificate:
    """Borra un certificado del cliente.

    Se borra de verdad y no se marca: guarda una clave privada cifrada, y un
    certificado que ya no se usa es material que no hay razón para seguir
    custodiando. Los documentos ya firmados con él siguen siendo válidos —la
    firma viaja dentro del XML—, así que no se pierde nada emitido.

    Borrar el último no se impide: dejar al cliente sin certificado es
    reversible subiendo otro, y bloquearlo obligaría a subir uno falso para
    poder limpiar. Quien llama avisa de lo que implica.
    """
    row = db.get(CustomerCertificate, cert_id)
    if row is None or row.customer_id != customer.id:
        raise DomainError(f"El certificado {cert_id} no existe o no pertenece a este cliente.")
    db.delete(row)
    if commit:
        db.commit()
    return row


def retire_caf(db: Session, customer: Customer, caf_id: int, *, commit: bool = True) -> Caf:
    """Deja de usar un CAF aunque le queden folios libres.

    Hace falta cuando el SII entrega un CAF nuevo que debe reemplazar al que
    está en uso —el caso típico es la certificación de boletas, que exige emitir
    con un CAF recién pedido—. Sin esto, el asignador seguiría entregando folios
    del CAF viejo, porque siempre toma el rango vigente más bajo.

    Marcarlo agotado basta: ``next_folio`` salta al siguiente rango con
    ``max(objetivo, folio_from)``, así que el próximo folio sale del CAF nuevo.
    Los folios ya emitidos con el CAF retirado siguen siendo válidos; lo que se
    corta es que se emitan más.
    """
    row = db.get(Caf, caf_id)
    if row is None or row.customer_id != customer.id:
        raise DomainError(f"El CAF {caf_id} no existe o no pertenece a este cliente.")
    if row.exhausted:
        raise DomainError(f"El CAF {caf_id} ya estaba fuera de uso.")
    row.exhausted = True
    if commit:
        db.commit()
    else:
        db.flush()
    return row


def add_caf(db: Session, customer: Customer, xml_base64: str, *, commit: bool = True) -> Caf:
    try:
        raw = base64.b64decode(xml_base64, validate=True)
    except (binascii.Error, ValueError) as ex:
        raise DomainError("xml_base64 no es base64 válido") from ex
    parsed = load_caf_bytes(raw)  # valida + extrae rango/tipo

    # El CAF pertenece al cliente: su RUT emisor (<RE>) debe ser el del cliente.
    if not _same_rut(parsed.issuer_rut, customer.rut):
        raise DomainError(
            f"El RUT del CAF ({parsed.issuer_rut}) no coincide con el del cliente ({customer.rut})."
        )

    # Rechazar rangos solapados con un CAF ya cargado del mismo tipo.
    overlap = (
        db.query(Caf)
        .filter(
            Caf.customer_id == customer.id,
            Caf.doc_type == parsed.doc_type,
            Caf.folio_from <= parsed.folio_to,
            Caf.folio_to >= parsed.folio_from,
        )
        .first()
    )
    if overlap is not None:
        raise DomainError(
            f"El rango {parsed.folio_from}-{parsed.folio_to} (tipo {parsed.doc_type}) se solapa "
            f"con un CAF ya cargado ({overlap.folio_from}-{overlap.folio_to})."
        )

    row = Caf(
        customer_id=customer.id,
        doc_type=parsed.doc_type,
        folio_from=parsed.folio_from,
        folio_to=parsed.folio_to,
        xml_encrypted=crypto.encrypt(raw),
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    # CAF y puntero en la misma transacción (atómicos con la auditoría del router).
    folio_service.ensure_pointer(db, customer.id, parsed.doc_type, commit=False)
    if commit:
        db.commit()
    return row
