"""Modelos ORM (multi-tenant + certificados + folios en BD + auditoría).

Espejo del servicio .NET, con dos mejoras: secretos cifrados en reposo y el
ambiente SII por cliente. Los folios viven en BD (asignador HA).
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SiiEnvironment(enum.StrEnum):
    CERTIFICATION = "CERTIFICATION"  # Maullín
    PRODUCTION = "PRODUCTION"  # Palena


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)  # customerCode
    # rut NO es único a propósito: una misma empresa puede tener clientes
    # separados por ambiente (certificación/producción). El customerCode (key)
    # es el identificador único de tenant.
    rut: Mapped[str] = mapped_column(String(20), index=True)
    environment: Mapped[SiiEnvironment] = mapped_column(
        Enum(SiiEnvironment), default=SiiEnvironment.CERTIFICATION
    )
    # Carátula de producción (en certificación va 0); por cliente.
    resolution_number: Mapped[int] = mapped_column(Integer, default=0)
    resolution_date: Mapped[dt.date] = mapped_column(Date, default=dt.date(2014, 8, 22))
    # Soft delete: NULL = activo; con fecha = archivado (no autentica ni se lista).
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    certificates: Mapped[list[CustomerCertificate]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    services: Mapped[list[CustomerService]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )
    sii_credential: Mapped[CustomerSiiCredential | None] = relationship(
        back_populates="customer", cascade="all, delete-orphan", uselist=False
    )


class CustomerCertificate(Base):
    __tablename__ = "customer_certificate"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    file_base64: Mapped[str] = mapped_column(String)  # .pfx en base64, Fernet-cifrado
    password: Mapped[str] = mapped_column(String)  # Fernet-cifrado
    due_date: Mapped[dt.date] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    customer: Mapped[Customer] = relationship(back_populates="certificates")


class CustomerSiiCredential(Base):
    """Clave tributaria del SII por cliente (login web para BHE).

    Es una credencial distinta del certificado (.pfx): las Boletas de Honorarios
    recibidas se consultan por login web, no por TLS mutuo. Una fila por cliente
    (1:1, write-only); la clave se guarda Fernet-cifrada.
    """

    __tablename__ = "customer_sii_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), unique=True, index=True
    )
    password: Mapped[str] = mapped_column(String)  # clave tributaria, Fernet-cifrada
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="sii_credential")


class Service(Base):
    __tablename__ = "service"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(36), unique=True, index=True)  # UUID == SERVICE_CODE
    name: Mapped[str] = mapped_column(String(100))


class CustomerService(Base):
    __tablename__ = "customer_service"
    __table_args__ = (UniqueConstraint("customer_id", "service_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("service.id", ondelete="CASCADE"))
    apikey_hash: Mapped[str] = mapped_column(String)  # argon2

    customer: Mapped[Customer] = relationship(back_populates="services")
    service: Mapped[Service] = relationship()


class Caf(Base):
    __tablename__ = "caf"
    # Toda consulta de CAF filtra por (cliente, tipo) → índice compuesto.
    __table_args__ = (Index("ix_caf_customer_doctype", "customer_id", "doc_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    doc_type: Mapped[int] = mapped_column(Integer)
    folio_from: Mapped[int] = mapped_column(Integer)
    folio_to: Mapped[int] = mapped_column(Integer)
    xml_encrypted: Mapped[str] = mapped_column(String)  # CAF completo (con RSASK), Fernet-cifrado
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class FolioPointer(Base):
    """Último folio asignado por (cliente, tipo). Fila bloqueada con FOR UPDATE."""

    __tablename__ = "folio_pointer"

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), primary_key=True
    )
    doc_type: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_folio: Mapped[int] = mapped_column(Integer, default=0)


class FolioAssignment(Base):
    """Trazabilidad de cada folio entregado: qué request lo consumió y su destino.

    Se inserta en la MISMA transacción que avanza el ``FolioPointer`` (atómico).
    ``status`` permite identificar folios quemados sin documento válido emitido.
    """

    __tablename__ = "folio_assignment"
    __table_args__ = (UniqueConstraint("customer_id", "doc_type", "folio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    doc_type: Mapped[int] = mapped_column(Integer)
    folio: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(64), default="-")
    status: Mapped[str] = mapped_column(String(20), default="assigned")  # assigned|issued|failed
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class IssuedReceipt(Base):
    """Boleta emitida, guardada para que el consumidor pueda recuperarla.

    El SII exige que la representación impresa indique un sitio donde consultar
    la boleta, y ese sitio necesita el documento. Es el ÚNICO tipo de documento
    que se almacena: los DTE los conserva el emisor.

    La búsqueda pide folio + fecha + monto a propósito. Los folios son
    correlativos: con sólo el folio, cualquiera enumeraría todas las ventas.
    Esos tres datos los tiene quien recibió la boleta, y nadie más.
    """

    __tablename__ = "issued_receipt"
    __table_args__ = (
        UniqueConstraint("customer_id", "doc_type", "folio"),
        Index("ix_issued_receipt_lookup", "customer_id", "folio", "issue_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"))
    doc_type: Mapped[int] = mapped_column(Integer)
    folio: Mapped[int] = mapped_column(Integer)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    total_amount: Mapped[int] = mapped_column(Integer)
    # El XML del <DTE> firmado, cifrado en reposo como el resto del material
    # tributario. Los campos de búsqueda van en claro porque se consultan.
    xml_encrypted: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """Usuario del portal: interno (customer_id NULL) o de cliente (customer_id set)."""

    __tablename__ = "app_user"  # 'user' es palabra reservada en Postgres

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String(20))  # ver security.roles.Role
    # CASCADE intencional: borrar un cliente elimina sus usuarios 'client'
    # (no quedan cuentas huérfanas apuntando a un tenant inexistente). Los
    # usuarios internos llevan customer_id NULL y no se ven afectados.
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # Soft delete: NULL = activo; con fecha = archivado (no puede iniciar sesión).
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    # --- Segundo factor (TOTP) ---
    # El secreto va cifrado con Fernet, como el resto del material sensible: con
    # él en claro cualquiera con acceso a la base genera códigos válidos.
    totp_secret: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Se separa de tener secreto: entre pedir el alta y confirmar el primer
    # código hay un estado intermedio, y ahí el segundo factor NO debe exigirse
    # todavía o el usuario se queda fuera si cierra la pestaña.
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    recovery_codes: Mapped[list[RecoveryCode]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RecoveryCode(Base):
    """Código de un solo uso para entrar si se pierde el teléfono.

    Hasheados con argon2, igual que las contraseñas: no se vuelven a mostrar. Sin
    esto, perder el teléfono deja fuera del portal que custodia los certificados
    de todas las empresas, y no hay a quién pedirle ayuda si eras el único
    superadmin.
    """

    __tablename__ = "recovery_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    user: Mapped[User] = relationship(back_populates="recovery_codes")


class RequestLog(Base):
    """Access-log de TODA petición (lo escribe el middleware). Sin secretos."""

    __tablename__ = "request_log"
    __table_args__ = (
        # El portal del cliente filtra por (principal_type, principal_id); el
        # panel filtra por service_code. Ambos ordenan por id desc.
        Index("ix_request_log_principal", "principal_type", "principal_id", "id"),
        Index("ix_request_log_service", "service_code", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(20))  # user|customer|system|anon
    principal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    principal_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    service_code: Mapped[str | None] = mapped_column(String(36), nullable=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(300))
    request_id: Mapped[str] = mapped_column(String(64), default="-")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(10))  # ok|denied|error
    latency_ms: Mapped[int] = mapped_column(Integer)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AdminAudit(Base):
    """Auditoría de cambios de datos maestros (quién modificó qué)."""

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    summary: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class MachineKey(Base):
    """Credencial de máquina (Odoo u otra integración) para los endpoints /admin.

    Reemplaza la ``X-Admin-Key`` única: cada consumidor tiene su clave hasheada,
    con rol e identidad propios y revocable. El cliente envía ``<key_id>.<secret>``;
    ``key_id`` (público, indexado) permite localizar la fila y verificar un solo
    hash argon2 (no recorrer todas las claves).
    """

    __tablename__ = "machine_key"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # etiqueta legible (p.ej. "odoo-prod")
    key_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # prefijo público
    secret_hash: Mapped[str] = mapped_column(String)  # argon2 del secreto
    role: Mapped[str] = mapped_column(String(20))  # operator | auditor (nunca superadmin)
    # Soft delete unificado: NULL = activa; con fecha = revocada/archivada.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
