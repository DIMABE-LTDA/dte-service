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
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.security.auth import COOKIE_NAME, get_current_user
from app.security.ratelimit import make_limiter
from app.services import user_service

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
