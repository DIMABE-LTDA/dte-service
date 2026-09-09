"""Carga y validación de certificados de cliente (admin)."""

from __future__ import annotations

import base64
import binascii
import datetime as dt

from dte_chile.certificate import Certificate
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import Customer, CustomerCertificate
from app.errors.exceptions import DomainError


def resolve_certificate(db: Session, customer: Customer) -> Certificate | None:
    """Carga el certificado vigente más reciente del cliente (o None si no hay).

    Reusado por la dependencia per-tenant y por los endpoints de operador.
    Hace cripto (from_pfx_bytes) → conviene llamarlo dentro de run_blocking.
    """
    row = (
        db.query(CustomerCertificate)
        .filter(
            CustomerCertificate.customer_id == customer.id,
            CustomerCertificate.due_date >= dt.date.today(),
        )
        .order_by(CustomerCertificate.created_at.desc())
        .first()
    )
    if row is None:
        return None
    pfx = crypto.decrypt(row.file_base64)
    return Certificate.from_pfx_bytes(pfx, crypto.decrypt_str(row.password))


def store_certificate(
    db: Session, customer: Customer, file_base64: str, password: str, *, commit: bool = True
) -> CustomerCertificate:
    """Valida el .pfx, extrae sus datos y lo guarda CIFRADO."""
    try:
        pfx = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as ex:
        raise DomainError("file_base64 no es base64 válido") from ex
    try:
        cert = Certificate.from_pfx_bytes(pfx, password)
    except ValueError as ex:
        raise DomainError("PFX inválido o contraseña incorrecta") from ex

    # Nota: NO se exige que el RUT del certificado sea igual al del cliente.
    # En Chile el certificado digital del SII se emite a una persona natural
    # (el representante), por lo que su RUT normalmente difiere del de la empresa.
    # El SII valida la autorización del firmante; aquí basta con un .pfx válido.

    datos = describe(pfx, password)

    # Cargar dos veces el mismo certificado no aporta nada y deja la ficha con
    # filas indistinguibles: mismo vencimiento, mismo titular, distinto id. Y
    # como se resuelve el más reciente, la duplicada tapa a la original sin que
    # nada lo diga.
    ya = (
        db.query(CustomerCertificate)
        .filter(
            CustomerCertificate.customer_id == customer.id,
            CustomerCertificate.thumbprint == datos["thumbprint"],
        )
        .first()
    )
    if ya is not None:
        raise DomainError(
            f"este certificado ya está cargado (id {ya.id}, titular"
            f" {ya.holder or datos['holder']}, vence {ya.due_date}). Para reemplazarlo"
            " por uno nuevo, sube el archivo nuevo; para reintentar un envío no hace"
            " falta volver a cargarlo."
        )

    row = CustomerCertificate(
        customer_id=customer.id,
        file_base64=crypto.encrypt(pfx),
        password=crypto.encrypt(password),
        due_date=datos["due_date"],
        rut=getattr(cert, "rut", None),
        holder=datos["holder"],
        issuer=datos["issuer"],
        thumbprint=datos["thumbprint"],
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    if commit:
        db.commit()
    return row


def _nombre(nombre) -> str:
    """El CN de un sujeto o emisor, o su forma larga si no lo tiene.

    Se prefiere el CN porque es lo que una persona reconoce: «ARTURO LENIN
    MUNOZ VERGARA» y «E-CERTCHILE CA FES 02», no la cadena RFC4514 entera.
    """
    from cryptography.x509.oid import NameOID

    cn = nombre.get_attributes_for_oid(NameOID.COMMON_NAME)
    return cn[0].value if cn else nombre.rfc4514_string()


def describe(pfx: bytes, password: str) -> dict:
    """Vencimiento, titular, emisor y huella del certificado dentro del .pfx."""
    import hashlib

    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

    _, cert, _ = pkcs12.load_key_and_certificates(pfx, password.encode("utf-8"))
    if cert is None:  # no usar assert: desaparece con `python -O`
        raise DomainError("el PFX no contiene un certificado")
    return {
        "due_date": cert.not_valid_after_utc.date(),
        "holder": _nombre(cert.subject)[:200],
        "issuer": _nombre(cert.issuer)[:200],
        "thumbprint": hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest(),
    }
