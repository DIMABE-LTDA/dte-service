"""Segundo factor del portal (TOTP, RFC 6238) y códigos de recuperación.

Quien entra al portal administra el material tributario de todas las empresas
del servicio: certificados de firma y CAF. Con sólo correo y contraseña, una
contraseña filtrada basta para timbrar a nombre de cualquiera de ellas.

Dos decisiones que conviene tener presentes:

- **El secreto se guarda cifrado con Fernet**, no en claro. Un secreto TOTP en
  claro es equivalente a la credencial: con él se generan códigos válidos.
- **Los códigos de recuperación no son opcionales.** Sin ellos, perder el
  teléfono deja fuera del portal, y si eras el único superadmin no hay a quién
  pedirle que te reactive. Se guardan hasheados con argon2 y se muestran una
  sola vez, igual que las apiKey.
"""

from __future__ import annotations

import hmac
import secrets
import time

import pyotp
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import RecoveryCode, User
from app.security.apikeys import hash_apikey, verify_apikey

_STEP = 30  # segundos por paso (RFC 6238)
# Ventana de tolerancia: ±1 paso. Cubre el desfase de reloj del teléfono sin
# ampliar de más la superficie de adivinación.
_VALID_WINDOW = 1

_RECOVERY_CODES = 8
_EMISOR = "DTE Service"


class TotpError(Exception):
    """Error de gestión del segundo factor (se mapea a 4xx en el router)."""


def start_enrollment(db: Session, user: User, *, commit: bool = True) -> str:
    """Genera un secreto nuevo y devuelve el URI ``otpauth://`` para la app.

    No activa nada: hasta que no se confirme un código, el usuario entra como
    siempre. Si se llama dos veces, el secreto anterior se descarta — es lo que
    quiere alguien que empezó el alta en un teléfono y la termina en otro.
    """
    if user.totp_enabled:
        raise TotpError("el segundo factor ya está activo")
    secret = pyotp.random_base32()
    user.totp_secret = crypto.encrypt(secret)
    db.flush()
    if commit:
        db.commit()
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=_EMISOR)


def verify_code(db: Session, user: User, code: str, *, commit: bool = True) -> bool:
    """Comprueba un código de 6 dígitos y **lo gasta**.

    Un TOTP es de un solo uso (RFC 6238 §5.2). Sin consumirlo, el mismo código
    abre sesiones ilimitadas mientras dura su ventana —hasta 90 s con la
    tolerancia de ±1 paso—, así que quien lo vea una sola vez deja de tener un
    único disparo. Se recuerda el último paso aceptado y se rechaza cualquier
    código de ese paso o anteriores.

    El avance va en un UPDATE condicional y no en una asignación: con varios
    workers, dos peticiones simultáneas con el mismo código entrarían las dos si
    cada una leyera, comparara y escribiera por su cuenta.
    """
    if not user.totp_secret or not code:
        return False
    secret = crypto.decrypt_str(user.totp_secret)
    totp = pyotp.TOTP(secret)
    presentado = code.strip()
    paso_actual = int(time.time()) // _STEP

    for delta in range(-_VALID_WINDOW, _VALID_WINDOW + 1):
        paso = paso_actual + delta
        # compare_digest: la comparación de pyotp también lo es, pero aquí somos
        # nosotros quienes comparamos.
        if not hmac.compare_digest(totp.at(paso * _STEP), presentado):
            continue
        # CursorResult y no Result: es un UPDATE, y lo que importa es cuántas
        # filas tocó (0 = el paso ya se había consumido).
        gastado: CursorResult = db.execute(  # type: ignore[assignment]
            update(User)
            .where(
                User.id == user.id,
                or_(User.totp_last_step.is_(None), User.totp_last_step < paso),
            )
            .values(totp_last_step=paso)
        )
        if gastado.rowcount == 0:
            return False  # ya se usó ese código (o uno posterior)
        user.totp_last_step = paso
        if commit:
            db.commit()
        return True
    return False


def activate(db: Session, user: User, code: str, *, commit: bool = True) -> list[str]:
    """Confirma el alta con un código y devuelve los códigos de recuperación.

    Se devuelven UNA vez: en la base quedan sólo sus hashes.
    """
    if user.totp_enabled:
        raise TotpError("el segundo factor ya está activo")
    if not user.totp_secret:
        raise TotpError("primero hay que pedir el alta del segundo factor")
    if not verify_code(db, user, code, commit=False):
        raise TotpError("el código no es válido; revisa la hora del teléfono")

    user.totp_enabled = True
    codes = [
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        for _ in range(_RECOVERY_CODES)
    ]
    for c in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_apikey(c)))
    db.flush()
    if commit:
        db.commit()
    return codes


def consume_recovery_code(db: Session, user: User, code: str, *, commit: bool = True) -> bool:
    """Gasta un código de recuperación. Devuelve False si no calza ninguno.

    Recorre los no usados: son ocho como máximo, así que verificar uno a uno no
    es problema, y no hay forma de indexarlos porque van hasheados con sal.
    """
    if not code:
        return False
    rows = db.execute(
        select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
    ).scalars()
    for row in rows:
        if verify_apikey(code.strip(), row.code_hash):
            from sqlalchemy import func

            row.used_at = func.now()
            db.flush()
            if commit:
                db.commit()
            return True
    return False


def unused_recovery_codes(db: Session, user: User) -> int:
    return len(
        db.execute(
            select(RecoveryCode.id).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
            )
        )
        .scalars()
        .all()
    )


def disable(db: Session, user: User, *, commit: bool = True) -> None:
    """Apaga el segundo factor y borra secreto y códigos."""
    user.totp_secret = None
    user.totp_enabled = False
    # El paso pertenece al secreto viejo: si no se olvida, al re-activar con un
    # secreto nuevo el corte heredado rechazaría códigos válidos.
    user.totp_last_step = None
    for row in db.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id)).scalars():
        db.delete(row)
    db.flush()
    if commit:
        db.commit()
