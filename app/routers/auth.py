"""Autenticación del portal (login JWT + perfil).

Endpoints ``def`` (síncronos): FastAPI los corre en el threadpool, así el
argon2 y la BD no bloquean el event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    TokenResponse,
    TotpActivateRequest,
    TotpActivateResponse,
    TotpDisableRequest,
    TotpSetupResponse,
    TotpStatus,
)
from app.security.auth import COOKIE_NAME, get_current_user
from app.security.passwords import verify_password
from app.security.ratelimit import make_limiter
from app.services import audit_service, totp_service, user_service

router = APIRouter(prefix="/auth", tags=["Auth"])

# Cuenta TODO intento de login por IP (éxito incluido): frena fuerza bruta.
_login_limiter = make_limiter("login", get_settings().login_attempts_per_minute, 60.0)


def _set_session_cookie(response: Response, token: str) -> None:
    """Cookie de sesión HttpOnly para la SPA (inmune a robo por XSS).

    Opción A del diseño dual: el token también va en el body (lo usan mobile/
    máquinas vía ``Authorization: Bearer``); la SPA usa la cookie y no lo persiste.
    """
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=TokenResponse)
def login(
    request: Request, response: Response, data: LoginRequest, db: Session = Depends(get_db)
) -> TokenResponse:
    ip = request.client.host if request.client else "-"
    if _login_limiter.hit(ip):
        raise HTTPException(status_code=429, detail="demasiados intentos; espera un minuto")
    user = user_service.authenticate(db, data.email, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="credenciales inválidas")
    if user.totp_enabled:
        # El detalle distingue "falta el código" de "credenciales inválidas" para
        # que la SPA sepa mostrar el campo. Sólo se llega aquí con la contraseña
        # ya correcta, así que no revela nada a quien no la tiene.
        if data.recovery_code:
            if not totp_service.consume_recovery_code(db, user, data.recovery_code):
                raise HTTPException(status_code=401, detail="código de recuperación inválido")
        elif not data.totp_code:
            raise HTTPException(status_code=401, detail="totp_required")
        elif not totp_service.verify_code(db, user, data.totp_code):
            raise HTTPException(status_code=401, detail="código de verificación inválido")
    token = create_access_token(user.id, user.role, user.customer_id)
    _set_session_cookie(response, token)
    return TokenResponse(access_token=token, role=user.role, customer_id=user.customer_id)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """Cierra la sesión del portal borrando la cookie (no afecta clientes Bearer)."""
    response.delete_cookie(key=COOKIE_NAME, path="/", samesite="strict")


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)) -> User:
    return user


# --- Segundo factor ----------------------------------------------------------


@router.get("/totp", response_model=TotpStatus)
def totp_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TotpStatus:
    return TotpStatus(
        enabled=user.totp_enabled,
        recovery_codes_left=totp_service.unused_recovery_codes(db, user),
    )


@router.post("/totp/setup", response_model=TotpSetupResponse)
def totp_setup(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> TotpSetupResponse:
    """Genera el secreto y devuelve el URI para la app. Todavía no activa nada."""
    try:
        uri = totp_service.start_enrollment(db, user)
    except totp_service.TotpError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    from urllib.parse import parse_qs, urlparse

    secret = parse_qs(urlparse(uri).query)["secret"][0]
    return TotpSetupResponse(otpauth_uri=uri, secret=secret)


@router.post("/totp/activate", response_model=TotpActivateResponse)
def totp_activate(
    data: TotpActivateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TotpActivateResponse:
    """Confirma el alta con un código y entrega los códigos de recuperación."""
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="contraseña incorrecta")
    try:
        codes = totp_service.activate(db, user, data.code, commit=False)
    except totp_service.TotpError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    # Queda en la auditoría: quién protege su cuenta y quién la desprotege es
    # exactamente lo que hay que poder revisar después de un incidente.
    audit_service.record_change(db, user.id, "user.totp_enable", "user", str(user.id), user.email)
    return TotpActivateResponse(recovery_codes=codes)


@router.post("/totp/disable", status_code=204)
def totp_disable(
    data: TotpDisableRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Apaga el segundo factor. Re-pide la contraseña a propósito: desde una
    sesión robada no debe bastar un clic."""
    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="contraseña incorrecta")
    totp_service.disable(db, user, commit=False)
    audit_service.record_change(db, user.id, "user.totp_disable", "user", str(user.id), user.email)
