"""Gestión de usuarios del portal (solo superadmin).

Endpoints ``def`` (síncronos): corren en el threadpool, así el argon2 y la BD
no bloquean el event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.session import get_db
from app.schemas.user import UserActiveUpdate, UserCreate, UserOut, UserPasswordUpdate
from app.security.auth import require_superadmin
from app.services import audit_service, totp_service, user_service
from app.services.user_service import UserError

router = APIRouter(prefix="/users", tags=["Usuarios"])


@router.post("", response_model=UserOut)
def create_user(
    data: UserCreate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    try:
        user = user_service.create_user(
            db, data.email, data.password, data.role, data.customer_id, commit=False
        )
    except UserError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    audit_service.record_change(
        db, actor.id, "user.create", "user", str(user.id), f"{user.email} ({user.role})"
    )
    return user


@router.get("", response_model=list[UserOut])
def list_users(
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    include_deleted: bool = Query(default=False),
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> list[User]:
    return user_service.list_users(db, limit=limit, offset=offset, include_deleted=include_deleted)


@router.patch("/{user_id}/active", response_model=UserOut)
def set_active(
    user_id: int,
    data: UserActiveUpdate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    try:
        user = user_service.set_active(db, user_id, data.is_active, commit=False)
    except UserError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    audit_service.record_change(
        db, actor.id, "user.set_active", "user", str(user.id), f"is_active={data.is_active}"
    )
    return user


@router.delete("/{user_id}", response_model=UserOut)
def delete_user(
    user_id: int,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    """Soft delete: archiva el usuario (no podrá iniciar sesión)."""
    try:
        user = user_service.soft_delete_user(db, user_id, commit=False)
    except UserError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    audit_service.record_change(db, actor.id, "user.delete", "user", str(user.id), user.email)
    return user


@router.post("/{user_id}/restore", response_model=UserOut)
def restore_user(
    user_id: int,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    try:
        user = user_service.restore_user(db, user_id, commit=False)
    except UserError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    audit_service.record_change(db, actor.id, "user.restore", "user", str(user.id), user.email)
    return user


@router.patch("/{user_id}/password", response_model=UserOut)
def set_password(
    user_id: int,
    data: UserPasswordUpdate,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    """Cambia la contraseña de otro usuario (p. ej. la olvidó y no hay self-service).

    La auditoría registra QUE se cambió y quién lo hizo, nunca el valor: ni en
    la respuesta, ni en el log, ni acá. Igual que ``totp/reset``, es un cambio
    que baja momentáneamente el control del titular sobre su cuenta, así que
    queda anotado.
    """
    try:
        user = user_service.set_password(db, user_id, data.password, commit=False)
    except UserError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    audit_service.record_change(db, actor.id, "user.set_password", "user", str(user.id), user.email)
    return user


@router.post("/{user_id}/totp/reset", response_model=UserOut)
def reset_totp(
    user_id: int,
    actor: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> User:
    """Apaga el segundo factor de otro usuario.

    Es la salida cuando alguien pierde el teléfono Y los códigos de
    recuperación. Queda en la auditoría de cambios porque baja el nivel de
    protección de una cuenta con acceso al material tributario: si aparece sin
    que nadie lo haya pedido, hay que preguntar.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="usuario inexistente")
    totp_service.disable(db, user, commit=False)
    audit_service.record_change(db, actor.id, "user.totp_reset", "user", str(user.id), user.email)
    return user
