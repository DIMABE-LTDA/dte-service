"""Servicio de acuses de intercambio (responder un EnvioDTE recibido)."""

from __future__ import annotations

import base64
import datetime as dt
from zoneinfo import ZoneInfo

from dte_chile.certificate import Certificate
from dte_chile.claim import ClaimAction, ClaimClient
from dte_chile.exchange import (
    addressed_to,
    build_receipt_acknowledgment,
    build_receipts_envelope,
    build_result_response,
    parse_envelope,
    serialize,
)

from app.errors.exceptions import DomainError

_CL_TZ = ZoneInfo("America/Santiago")  # acuses fechados en hora chilena


def _ts() -> dt.datetime:
    return dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)


def _b64(xml: bytes) -> str:
    return base64.b64encode(xml).decode("ascii")


def inspect(envelope_base64: str, receiver_rut: str) -> dict:
    """Qué trae el sobre recibido, sin responder todavía.

    Quien recibe un DTE de su proveedor necesita saber de quién es, qué
    documento y por cuánto antes de aceptarlo o reclamarlo. Leerlo es trabajo
    del facturador: el ERP no tiene por qué aprender a parsear un EnvioDTE.
    """
    try:
        env = parse_envelope(base64.b64decode(envelope_base64))
    except Exception as ex:  # noqa: BLE001 - cualquier XML roto es dato malo
        raise DomainError(f"El sobre no se pudo leer: {ex}") from ex
    return {
        "issuer_rut": env.issuer_rut,
        "receiver_rut": env.receiver_rut,
        "documents": [
            {
                "doc_type": d.doc_type,
                "folio": d.folio,
                "issue_date": d.issue_date,
                "issuer_rut": d.issuer_rut,
                "receiver_rut": d.receiver_rut,
                "total_amount": d.total_amount,
                "addressed_to_me": addressed_to(d, receiver_rut),
            }
            for d in env.documents
        ],
    }


def acknowledgment(cert: Certificate, envelope_base64: str) -> str:
    env = parse_envelope(base64.b64decode(envelope_base64))
    return _b64(serialize(build_receipt_acknowledgment(env, cert, _ts())))


def result(cert: Certificate, envelope_base64: str, accept: bool, rejection_label: str) -> str:
    env = parse_envelope(base64.b64decode(envelope_base64))
    return _b64(
        serialize(
            build_result_response(env, cert, _ts(), accept=accept, rejection_label=rejection_label)
        )
    )


def receipts(cert: Certificate, envelope_base64: str, location: str) -> str:
    env = parse_envelope(base64.b64decode(envelope_base64))
    return _b64(serialize(build_receipts_envelope(env, cert, _ts(), location=location)))


def _claim_client(customer, cert) -> ClaimClient:
    from dte_chile.sii_client import Environment

    from app.core.config import get_settings

    return ClaimClient(
        cert, Environment[customer.environment.name], get_settings().request_timeout_s
    )


def register_claim(customer, cert, issuer_rut: str, doc_type: int, folio: int, action: str) -> dict:
    """Registra en el SII la aceptación o el reclamo de un documento recibido.

    Es lo que corre el plazo de la Ley 19.983. Responderle al proveedor por
    correo —los acuses de arriba— es otra cosa: el SII no se entera de eso.
    """
    try:
        accion = ClaimAction(action.upper())
    except ValueError as ex:
        validas = ", ".join(a.value for a in ClaimAction)
        raise DomainError(f"Acción {action!r} desconocida. Las válidas son: {validas}.") from ex
    client = _claim_client(customer, cert)
    try:
        resultado = client.register(issuer_rut, doc_type, folio, accion)
    except ValueError as ex:  # tipo de documento que no admite reclamo
        raise DomainError(str(ex)) from ex
    finally:
        client.session.close()
    return _claim_out(resultado)


def claim_history(customer, cert, issuer_rut: str, doc_type: int, folio: int) -> dict:
    """Qué se registró en el SII sobre ese documento, y cuándo."""
    client = _claim_client(customer, cert)
    try:
        resultado = client.history(issuer_rut, doc_type, folio)
    finally:
        client.session.close()
    return _claim_out(resultado)


def _claim_out(resultado) -> dict:
    return {
        "ok": resultado.ok,
        "code": resultado.code,
        "detail": resultado.detail,
        "events": [
            {
                "code": e.code,
                "label": e.label,
                "date": e.date,
                "responder_rut": e.responder_rut,
            }
            for e in resultado.events
        ],
    }
