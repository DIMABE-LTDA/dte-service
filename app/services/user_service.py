"""Gestión de usuarios del portal (admin + cliente) + seed de superadmin."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import User
from app.security.passwords import dummy_verify, hash_password, verify_password
from app.security.roles import Role


class UserError(Exception):
    """Error de gestión de usuarios (se mapea a 4xx en el router)."""


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.execute(
        select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if user is None:
        dummy_verify()  # tiempo constante: email inexistente/archivado no responde más rápido
        return None
    if not user.is_active or not verify_password(password, user.password_hash):
        return None
    user.last_login = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    return user


def create_user(
    db: Session,
    email: str,
    password: str,
    role: str,
    customer_id: int | None,
    *,
    commit: bool = True,
) -> User:
    if role not in {str(r) for r in Role}:
        raise UserError(f"rol inválido: {role}")
    if role == Role.CLIENT and customer_id is None:
        raise UserError("un usuario 'client' requiere customer_id")
    if role != Role.CLIENT and customer_id is not None:
        raise UserError("un usuario interno no lleva customer_id")
    if db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none():
        raise UserError("el email ya existe")

    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        role=role,
        customer_id=customer_id,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    if commit:
        db.commit()
    return user


def list_users(
    db: Session, *, limit: int = 100, offset: int = 0, include_deleted: bool = False
) -> list[User]:
    stmt = select(User).order_by(User.id)
    if not include_deleted:
        stmt = stmt.where(User.deleted_at.is_(None))
    return list(db.execute(stmt.limit(limit).offset(offset)).scalars())


def set_active(db: Session, user_id: int, is_active: bool, *, commit: bool = True) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise UserError("usuario no encontrado")
    # Guardia: no dejar el sistema sin superadmin activo.
    if not is_active and user.role == Role.SUPERADMIN and _active_superadmins(db) <= 1:
        raise UserError("no puedes desactivar el último superadmin")
    user.is_active = is_active
    db.flush()
    db.refresh(user)
    if commit:
        db.commit()
    return user


def soft_delete_user(db: Session, user_id: int, *, commit: bool = True) -> User:
    """Archiva el usuario (soft delete). No puede iniciar sesión mientras esté archivado."""
    user = db.get(User, user_id)
    if user is None:
        raise UserError("usuario no encontrado")
    if user.deleted_at is None:
        # Guardia: no archivar el último superadmin activo.
        if user.role == Role.SUPERADMIN and _active_superadmins(db) <= 1:
            raise UserError("no puedes eliminar el último superadmin")
        user.deleted_at = func.now()
        db.flush()
        db.refresh(user)
        if commit:
            db.commit()
    return user


def set_password(db: Session, user_id: int, password: str, *, commit: bool = True) -> User:
    """Cambia la contraseña de otro usuario (la fija el superadmin, no el titular).

    Reusa el mismo hash (argon2) que el alta: no hay dos caminos para llegar a
    `password_hash`. El valor en claro no vuelve al llamador ni se guarda en
    ningún lado más que aquí.
    """
    user = db.get(User, user_id)
    if user is None:
        raise UserError("usuario no encontrado")
    user.password_hash = hash_password(password)
    db.flush()
    db.refresh(user)
    if commit:
        db.commit()
    return user


def restore_user(db: Session, user_id: int, *, commit: bool = True) -> User:
    """Reactiva un usuario archivado."""
    user = db.get(User, user_id)
    if user is None:
        raise UserError("usuario no encontrado")
    if user.deleted_at is not None:
        user.deleted_at = None
        db.flush()
        db.refresh(user)
        if commit:
            db.commit()
    return user


def _active_superadmins(db: Session) -> int:
    """Superadmins que pueden operar: activos y no archivados."""
    return db.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.role == Role.SUPERADMIN,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    ).scalar_one()


def seed_superadmin(db: Session, email: str, password: str) -> None:
    """Crea el superadmin inicial si no existe (idempotente)."""
    if not email or not password:
        return
    existing = db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()
    if existing is not None:
        return
    db.add(
        User(
            email=email.lower(),
            password_hash=hash_password(password),
            role=str(Role.SUPERADMIN),
            customer_id=None,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Carrera benigna: con varios workers el lifespan corre en cada uno y
        # otro ya sembró el mismo email. No debe abortar el arranque.
        db.rollback()
