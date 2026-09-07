"""Autenticación multi-tenant por headers + resolución del cliente.

Mirror del patrón .NET: el request trae ``apiKey`` y ``customerCode`` y cada
endpoint exige un ``service_code``. Se valida que el cliente exista, tenga ese
servicio habilitado y la apikey calce (hash). La auditoría la escribe el
middleware de access-log; este dep solo fija el principal en ``request.state``.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Customer, CustomerService, Service
from app.db.session import get_db
from app.security.apikeys import dummy_verify, verify_apikey
from app.security.ratelimit import make_limiter
from app.security.roles import Role

# Solo cuenta FALLOS de autenticación por IP: una IP que acumula fallos queda
# bloqueada (429) sin penalizar el tráfico legítimo de alto volumen.
_tenant_failures = make_limiter("tenantfail", get_settings().tenant_auth_failures_per_5min, 300.0)

# Cuota del cliente YA autenticado, por cliente y no por IP: es el cliente quien
# consume, salga por la IP que salga. Frena que uno acapare el servicio — firmar
# y hablar con el SII son operaciones caras que pagan todos.
_customer_quota = (
    make_limiter("custquota", get_settings().customer_requests_per_minute, 60.0)
    if get_settings().customer_requests_per_minute > 0
    else None
)


def tenant_for(service_code: str) -> Callable[..., Customer]:
    """Devuelve una dependencia que resuelve el ``Customer`` para ese servicio."""

    def _dep(
        request: Request,
        api_key: str = Header(alias="apiKey"),
        customer_code: str = Header(alias="customerCode"),
        db: Session = Depends(get_db),
    ) -> Customer:
        request.state.service_code = service_code
        ip = request.client.host if request.client else "-"
        # Bloquear ANTES de consultar/verificar: no gastar argon2 en atacantes.
        if _tenant_failures.is_limited(ip):
            raise HTTPException(
                status_code=429, detail="demasiados intentos fallidos; reintenta más tarde"
            )
        cs = (
            db.query(CustomerService)
            .join(CustomerService.service)
            .join(CustomerService.customer)
            .filter(
                Customer.key == customer_code,
                Service.code == service_code,
                Customer.deleted_at.is_(None),  # un cliente archivado no autentica
            )
            .first()
        )
        if cs is None:
            dummy_verify()  # tiempo constante: customerCode inexistente no responde antes
        if cs is None or not verify_apikey(api_key, cs.apikey_hash):
            _tenant_failures.record(ip)
            raise HTTPException(status_code=401, detail="credenciales inválidas")

        # La cuota se cobra DESPUÉS de autenticar: quien no acierta la credencial
        # no debe poder gastarle la cuota a un cliente legítimo desde fuera.
        if _customer_quota is not None and _customer_quota.hit(str(cs.customer_id)):
            raise HTTPException(
                status_code=429,
                detail="cuota por minuto excedida para este cliente; reintenta en un momento",
            )

        request.state.principal = ("customer", cs.customer_id, str(Role.CLIENT))
        return cs.customer

    return _dep
