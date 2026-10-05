"""Autenticación multi-tenant por headers + resolución del cliente.

El request trae ``customerCode`` y ``apiKey``, y cada endpoint exige un
``service_code``. Hay dos formas de ``apiKey`` que conviven:

- **Nueva (recomendada):** ``<key_id>.<secret>`` → ``CustomerApiKey``, una clave
  por consumidor con los servicios que tiene permitidos (subconjunto de los
  contratados por el cliente, en ``CustomerService``). Credencial inválida → 401;
  credencial válida pero sin ese servicio → **403** (mensaje distinto, no cuenta
  como intento fallido).
- **Vieja (deprecada):** cualquier otro valor → se busca la fila
  ``CustomerService`` de (cliente, servicio) y se verifica su ``apikey_hash``
  (mirror del patrón .NET: una apiKey por servicio). Sigue funcionando para no
  cortar a nadie, pero no se deben emitir más claves así.

La auditoría la escribe el middleware de access-log; este dep solo fija el
principal y, para la clave nueva, el nombre de la clave en ``request.state``.
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
from app.services import api_key_service
from app.services.certification_service import certification_set_var

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
        certification_set: str = Header(default="", alias="X-Certification-Set"),
        db: Session = Depends(get_db),
    ) -> Customer:
        request.state.service_code = service_code
        ip = request.client.host if request.client else "-"
        # Bloquear ANTES de consultar/verificar: no gastar argon2 en atacantes.
        if _tenant_failures.is_limited(ip):
            raise HTTPException(
                status_code=429, detail="demasiados intentos fallidos; reintenta más tarde"
            )

        customer = (
            db.query(Customer)
            .filter(Customer.key == customer_code, Customer.deleted_at.is_(None))
            .first()
        )
        if customer is None:
            dummy_verify()  # tiempo constante: customerCode inexistente no responde antes
            _tenant_failures.record(ip)
            raise HTTPException(status_code=401, detail="credenciales inválidas")

        key_name: str | None = None
        if "." in api_key:
            # --- Camino nuevo: una clave con los servicios que tiene permitidos ---
            cak = api_key_service.authenticate(db, customer, api_key)
            if cak is None:
                dummy_verify()
                _tenant_failures.record(ip)
                raise HTTPException(status_code=401, detail="credenciales inválidas")
            allowed = {s.code for s in cak.services}
            if service_code not in allowed:
                # Credencial VÁLIDA, sin ese permiso: no es un intento fallido.
                raise HTTPException(
                    status_code=403,
                    detail=f'la clave "{cak.name}" no tiene autorizado este servicio',
                )
            api_key_service.touch_last_used(db, cak)
            key_name = cak.name
        else:
            # --- Camino viejo (deprecado): una apiKey por servicio ---
            cs = (
                db.query(CustomerService)
                .join(CustomerService.service)
                .filter(
                    CustomerService.customer_id == customer.id,
                    Service.code == service_code,
                )
                .first()
            )
            if cs is None:
                dummy_verify()
                _tenant_failures.record(ip)
                raise HTTPException(status_code=401, detail="credenciales inválidas")
            if not verify_apikey(api_key, cs.apikey_hash):
                _tenant_failures.record(ip)
                raise HTTPException(status_code=401, detail="credenciales inválidas")

        # La cuota se cobra DESPUÉS de autenticar (identidad Y permiso): quien no
        # acierta la credencial, o acierta pero no tiene el servicio, no debe
        # poder gastarle la cuota a un cliente legítimo desde fuera.
        if _customer_quota is not None and _customer_quota.hit(str(customer.id)):
            raise HTTPException(
                status_code=429,
                detail="cuota por minuto excedida para este cliente; reintenta en un momento",
            )

        # Set de certificación al que pertenece este envío, si quien emite lo
        # declara. Es opcional: sin la cabecera el envío se guarda igual y se
        # asocia después, para que la captura no dependa de recordar ponerla.
        certification_set_var.set((certification_set or "").strip() or None)

        if key_name is not None:
            request.state.audit_meta = {"api_key_name": key_name}
        request.state.principal = ("customer", customer.id, str(Role.CLIENT))
        return customer

    return _dep
