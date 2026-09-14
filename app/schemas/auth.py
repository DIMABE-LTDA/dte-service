"""Schemas de autenticación del portal."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    email: str
    password: str
    # Segundo factor. Va en el mismo envío que la contraseña en vez de en un
    # segundo paso con token de desafío: no hay estado intermedio que guardar ni
    # que expirar, y la SPA sólo tiene que mostrar el campo cuando el API lo pide.
    totp_code: str | None = None
    # Alternativa al código, para cuando se perdió el teléfono. Es de un solo uso.
    recovery_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    customer_id: int | None = None


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    customer_id: int | None = None
    totp_enabled: bool = False


class TotpSetupResponse(BaseModel):
    """El URI se pega en la app de autenticación; el secreto, para teclearlo a
    mano si no se puede escanear."""

    otpauth_uri: str
    secret: str


class TotpActivateRequest(BaseModel):
    code: str
    # Se re-pide igual que al desactivar. Con sólo una cookie de sesión robada,
    # un atacante podía dar de alta un segundo factor que él controla sobre una
    # cuenta que aún no lo tenía, quedarse con los códigos de recuperación y
    # dejar fuera al titular — usando como puerta justo la contraseña filtrada
    # contra la que el segundo factor debía proteger.
    password: str


class TotpActivateResponse(BaseModel):
    """Los códigos de recuperación se ven UNA vez: en la base van hasheados."""

    recovery_codes: list[str]


class TotpDisableRequest(BaseModel):
    # Se re-pide la contraseña: apagar el segundo factor desde una sesión robada
    # no debe ser un solo clic.
    password: str


class TotpStatus(BaseModel):
    enabled: bool
    recovery_codes_left: int
