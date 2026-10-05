"""Claves API por cliente: una clave, con los servicios que tiene permitidos.

Reemplaza el esquema «una apiKey por servicio» (``app/services/customer_service.py
::grant_service``, que sigue funcionando pero queda deprecado). El formato de la
clave en claro es ``<key_id>.<secret>``, igual que ``MachineKey``
(``app/services/machine_key_service.py``): ``key_id`` es un prefijo público e
indexado que permite ubicar la fila y verificar un único hash argon2.

Los servicios de una clave son siempre un subconjunto de los que el cliente
tiene **contratados**: una fila en ``CustomerService`` (sin importar si su
``apikey_hash`` se sigue usando) es la señal de "servicio habilitado para este
cliente". Pedir un servicio no contratado es un ``DomainError`` (400).
"""

from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Customer, CustomerApiKey, CustomerService, Service
from app.errors.exceptions import DomainError
from app.security.apikeys import hash_apikey, verify_apikey
from app.security.service_codes import ALL_SERVICES

# Granularidad de ``last_used_at``: no se escribe en cada request, solo cuando
# cambió el minuto. A volumen normal de tráfico, saber el minuto del último uso
# basta para operar (revisar si una clave sigue viva) y evita convertir un
# endpoint que hoy no escribe nada en un UPDATE por llamada.
_LAST_USED_GRANULARITY = dt.timedelta(minutes=1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def contracted_codes(db: Session, customer: Customer) -> set[str]:
    """Códigos de servicio que el cliente tiene **contratados** (habilitados)."""
    rows = db.execute(
        select(Service.code)
        .join(CustomerService, CustomerService.service_id == Service.id)
        .where(CustomerService.customer_id == customer.id)
    ).scalars()
    return set(rows)


def _resolve_services(db: Session, customer: Customer, service_codes: list[str]) -> list[Service]:
    codes = set(service_codes)
    if not codes:
        raise DomainError("una clave necesita al menos un servicio")
    unknown = codes - set(ALL_SERVICES)
    if unknown:
        raise DomainError(f"service_code desconocido: {', '.join(sorted(unknown))}")
    not_contracted = codes - contracted_codes(db, customer)
    if not_contracted:
        raise DomainError(
            "el cliente no tiene contratados estos servicios (hay que habilitarlos primero): "
            + ", ".join(sorted(not_contracted))
        )
    return list(db.execute(select(Service).where(Service.code.in_(codes))).scalars())


def create_key(
    db: Session,
    customer: Customer,
    name: str,
    service_codes: list[str],
    *,
    expires_at: dt.datetime | None = None,
    commit: bool = True,
) -> tuple[CustomerApiKey, str]:
    """Crea una clave con los servicios dados. Devuelve ``(fila, clave_en_claro)``.

    La clave en claro (``key_id.secret``) se ve UNA vez; en BD solo queda el hash.
    """
    nombre = name.strip()
    if not nombre:
        raise DomainError("el nombre de la clave no puede estar vacío")
    services = _resolve_services(db, customer, service_codes)
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    row = CustomerApiKey(
        customer_id=customer.id,
        name=nombre,
        key_id=key_id,
        secret_hash=hash_apikey(secret),
        expires_at=expires_at,
    )
    row.services = services
    db.add(row)
    db.flush()
    db.refresh(row)
    if commit:
        db.commit()
    return row, f"{key_id}.{secret}"


def list_keys_for(
    db: Session, customer: Customer, *, include_deleted: bool = False
) -> list[CustomerApiKey]:
    stmt = (
        select(CustomerApiKey)
        .where(CustomerApiKey.customer_id == customer.id)
        .order_by(CustomerApiKey.id)
    )
    if not include_deleted:
        stmt = stmt.where(CustomerApiKey.deleted_at.is_(None))
    return list(db.execute(stmt).scalars())


def get_key(db: Session, customer: Customer, api_key_id: int) -> CustomerApiKey | None:
    """La fila, pero solo si pertenece a este cliente (aislamiento entre tenants)."""
    row = db.get(CustomerApiKey, api_key_id)
    if row is None or row.customer_id != customer.id:
        return None
    return row


def set_services(
    db: Session,
    customer: Customer,
    key: CustomerApiKey,
    service_codes: list[str],
    *,
    commit: bool = True,
) -> CustomerApiKey:
    """Reemplaza los servicios permitidos de la clave (siempre un subconjunto de
    los contratados por el cliente)."""
    key.services = _resolve_services(db, customer, service_codes)
    db.flush()
    db.refresh(key)
    if commit:
        db.commit()
    return key


def revoke_key(db: Session, key: CustomerApiKey, *, commit: bool = True) -> CustomerApiKey:
    """Revoca la clave (soft delete: preserva trazabilidad en la auditoría)."""
    if key.deleted_at is None:
        key.deleted_at = func.now()
        db.flush()
        db.refresh(key)
        if commit:
            db.commit()
    return key


def authenticate(db: Session, customer: Customer, presented: str) -> CustomerApiKey | None:
    """Valida una clave ``<key_id>.<secret>`` para ESTE cliente. ``None`` si no calza.

    Exige que el ``key_id`` pertenezca al cliente resuelto por ``customerCode``:
    una clave de otro cliente no autentica aquí, aunque el secreto fuera correcto.
    """
    if "." not in presented:
        return None
    key_id, _, secret = presented.partition(".")
    row = db.execute(
        select(CustomerApiKey).where(
            CustomerApiKey.key_id == key_id, CustomerApiKey.customer_id == customer.id
        )
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= _now():
        return None
    if not verify_apikey(secret, row.secret_hash):
        return None
    return row


def touch_last_used(db: Session, key: CustomerApiKey, *, commit: bool = True) -> None:
    """Actualiza ``last_used_at`` salvo que ya se haya tocado en este minuto."""
    now = _now()
    if key.last_used_at is not None and now - key.last_used_at < _LAST_USED_GRANULARITY:
        return
    key.last_used_at = now
    if commit:
        db.commit()
    else:
        db.flush()
