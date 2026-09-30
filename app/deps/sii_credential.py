"""Resolución de la clave tributaria del cliente por request (per-tenant).

Análogo a ``deps.certificate`` pero para el login web del SII (BHE): descifra la
clave guardada del cliente en threadpool. La lógica vive en
``sii_credential_service.resolve_sii_password``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.concurrency import run_blocking
from app.core.config import get_settings
from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_bhe
from app.errors.exceptions import SiiCredentialUnavailable
from app.security.ratelimit import make_limiter
from app.services import sii_credential_service


def _sii_password_dep(tenant_dependency: Callable[..., Customer]) -> Callable[..., Awaitable[str]]:
    async def _dep(
        customer: Customer = Depends(tenant_dependency),
        db: Session = Depends(get_db),
    ) -> str:
        password = await run_blocking(sii_credential_service.resolve_sii_password, db, customer)
        if password is None:
            raise SiiCredentialUnavailable(customer.id)
        return password

    return _dep


sii_pwd_bhe = _sii_password_dep(require_bhe)


_portal_limiter = make_limiter("siiportal", get_settings().sii_portal_queries_per_minute, 60.0)


def check_sii_portal_quota(customer_id: int) -> None:
    """Cuota de consultas al portal del SII por cliente, sea por su apiKey o por un
    operador: la clave tributaria que se expone al SII es la misma."""
    if _portal_limiter.hit(str(customer_id)):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas consultas al portal del SII para este cliente. "
            "Espera un minuto antes de reintentar.",
        )
