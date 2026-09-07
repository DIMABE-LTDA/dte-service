"""Autenticación de usuarios del portal (JWT) + dependencias de RBAC.

El token se acepta por dos vías (diseño dual):
  - Header ``Authorization: Bearer <jwt>`` → mobile, Odoo con JWT, herramientas.
  - Cookie ``access_token`` (HttpOnly) → la SPA del navegador (inmune a XSS).
El header tiene precedencia sobre la cookie.

Dependencias ``def`` (síncronas): FastAPI las corre en el threadpool, así las
consultas a BD no bloquean el event loop.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db
from app.security.ratelimit import SlidingWindowLimiter
from app.security.roles import ADMIN_ROLES, WRITE_ROLES, Role

COOKIE_NAME = "access_token"

# Solo cuenta FALLOS de X-Admin-Key por IP. Es la credencial con escritura sobre
# TODOS los clientes: sin tope se puede probar a ciegas hasta dar con ella.
_admin_failures = SlidingWindowLimiter(get_settings().admin_key_failures_per_5min, 300.0)


def _token_from(request: Request, authorization: str) -> str | None:
    """Extrae el JWT del header Bearer (precedencia) o de la cookie de sesión."""
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return request.cookies.get(COOKIE_NAME)


def get_current_user(
    request: Request,
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    token = _token_from(request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="falta autenticación")
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])  # 'sub' no numérico → token inválido, no 500
    except Exception as ex:
        raise HTTPException(status_code=401, detail="token inválido o expirado") from ex

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="usuario inexistente o inactivo")
    request.state.principal = ("user", user.id, user.role)
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    allowed = {str(r) for r in roles}

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="permiso insuficiente")
        return user

    return _dep


require_superadmin = require_roles(Role.SUPERADMIN)
require_write = require_roles(*WRITE_ROLES)  # superadmin + operator
require_admin_panel = require_roles(*ADMIN_ROLES)  # cualquier rol interno


def _admin_principal(
    request: Request, authorization: str, x_admin_key: str, db: Session, allowed: set[str]
) -> User | None:
    """Resuelve el principal de /admin: JWT con rol en ``allowed`` **o** una clave
    de máquina. Devuelve el ``User`` (JWT) o ``None`` (máquina).

    Claves de máquina, en orden: la ``DTE_ADMIN_API_KEY`` de entorno (bootstrap,
    rol admin, sólo si ``DTE_ADMIN_BOOTSTRAP_KEY_ENABLED`` sigue en true) o una
    ``MachineKey`` de BD por consumidor (``key_id.secret``, con rol propio). Una
    X-Admin-Key presente pero inválida es 401 (no degrada) y cuenta contra el
    límite de fallos por IP."""
    if x_admin_key:
        from app.services import machine_key_service

        settings = get_settings()
        ip = request.client.host if request.client else "-"
        # Bloquear ANTES de verificar: no gastar argon2 en quien está tanteando.
        if _admin_failures.is_limited(ip):
            raise HTTPException(
                status_code=429, detail="demasiados intentos fallidos; reintenta más tarde"
            )
        # 1) Clave de bootstrap por entorno (compat). compare_digest: sin timing.
        if settings.admin_bootstrap_key_enabled and secrets.compare_digest(
            x_admin_key, settings.admin_api_key
        ):
            request.state.principal = ("system", None, "admin")
            return None
        # 2) Clave de máquina por consumidor (hasheada en BD), con su rol.
        mk = machine_key_service.authenticate(db, x_admin_key)
        if mk is not None:
            # Credencial válida: un 403 por rol no cuenta como intento fallido.
            if mk.role not in allowed:
                raise HTTPException(status_code=403, detail="permiso insuficiente")
            request.state.principal = ("system", mk.id, mk.role)
            return None
        _admin_failures.record(ip)
        raise HTTPException(status_code=401, detail="credenciales inválidas")
    token = _token_from(request, authorization)
    if token:
        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
        except Exception as ex:
            raise HTTPException(status_code=401, detail="token inválido o expirado") from ex
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="usuario inexistente o inactivo")
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="permiso insuficiente")
        request.state.principal = ("user", user.id, user.role)
        return user
    raise HTTPException(status_code=401, detail="falta autenticación (Bearer/cookie o X-Admin-Key)")


def admin_access(
    request: Request,
    authorization: str = Header(default=""),
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
    db: Session = Depends(get_db),
) -> User | None:
    """Escritura en /admin: rol superadmin/operator (JWT) o X-Admin-Key (máquina)."""
    return _admin_principal(request, authorization, x_admin_key, db, {str(r) for r in WRITE_ROLES})


def admin_read_access(
    request: Request,
    authorization: str = Header(default=""),
    x_admin_key: str = Header(default="", alias="X-Admin-Key"),
    db: Session = Depends(get_db),
) -> User | None:
    """Lectura en /admin: cualquier rol interno (incluye auditor) o X-Admin-Key."""
    return _admin_principal(request, authorization, x_admin_key, db, {str(r) for r in ADMIN_ROLES})
