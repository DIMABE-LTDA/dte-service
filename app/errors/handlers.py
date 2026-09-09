"""Mapeo de la jerarquía ``DteError`` del motor → códigos HTTP + body uniforme."""

from __future__ import annotations

from dte_chile import FolioError, FoliosExhausted
from dte_chile.errors import (
    BheError,
    DteError,
    RcvError,
    SiiAuthError,
    SiiError,
    SiiUploadError,
)
from dte_chile.text import DocumentDataError
from dte_chile.validation import ValidationError, XSDNotAvailable
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.logging import request_id_var
from app.errors.exceptions import (
    CertificateUnavailable,
    DomainError,
    SiiCredentialUnavailable,
)
from app.schemas.common import ErrorBody, ErrorResponse

# Orden importa: del más específico al más general (se evalúa con isinstance).
_STATUS: list[tuple[type[Exception], int]] = [
    # Datos del documento que el SII rechazaría (caracteres, largos): es
    # culpa del que llama, y el detalle dice campo por campo qué corregir.
    (DocumentDataError, 422),
    (ValidationError, 422),
    (XSDNotAvailable, 503),
    (FoliosExhausted, 409),
    (FolioError, 409),
    (SiiAuthError, 502),
    (SiiUploadError, 502),
    (RcvError, 502),
    (BheError, 502),
    (SiiError, 502),
    (DteError, 500),
]


def _status_for(exc: Exception) -> int:
    for typ, status in _STATUS:
        if isinstance(exc, typ):
            return status
    return 500


def _body(exc: Exception, details: list[str]) -> dict:
    return ErrorResponse(
        error=ErrorBody(
            type=type(exc).__name__,
            message=str(exc),
            details=details,
            request_id=request_id_var.get(),
        )
    ).model_dump()


async def _document_data_handler(_: Request, exc: DocumentDataError) -> JSONResponse:
    """Devuelve un detalle por campo, para corregir todo de una vez."""
    return JSONResponse(status_code=422, content=_body(exc, [str(p) for p in exc.problems]))


def _dte_error_handler(request: Request, exc: Exception) -> JSONResponse:
    details = list(getattr(exc, "errors", []) or [])
    return JSONResponse(status_code=_status_for(exc), content=_body(exc, details))


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]  # type: ignore[attr-defined]
    return JSONResponse(status_code=422, content=_body(exc, details))


#: Qué mirar cuando el SII no entrega token. El mensaje del motor —"rechazó la
#: semilla firmada (estado=10)"— es exacto y no dice nada accionable: la causa
#: nunca está en la semilla, sino en quién la firma.
_AUTH_CHECKS = (
    "El certificado tiene que estar emitido por una entidad acreditada"
    " (e-certchile, Acepta, Certinet…). Uno autofirmado o de pruebas no autentica"
    " contra el SII, aunque firme bien.",
    "El RUT del certificado necesita el atributo «Enviar Doctos» en el ambiente"
    " al que estás enviando. Maullín y Palena tienen registros de usuarios"
    " SEPARADOS: el permiso de producción no vale en certificación ni al revés.",
    "Comprueba que el certificado no esté vencido ni revocado.",
)


async def _sii_auth_handler(request: Request, exc: Exception) -> JSONResponse:
    """Traduce el fallo de autenticación en algo que se pueda accionar.

    Sin esto, el operador ve un número de estado y no tiene por dónde empezar.
    Es el mismo error que costó diez envíos rechazados antes de descubrir que
    faltaba un permiso.
    """
    return JSONResponse(status_code=502, content=_body(exc, list(_AUTH_CHECKS)))


async def _cert_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content=_body(exc, []))


async def _sii_credential_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content=_body(exc, []))


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content=_body(exc, []))


async def _integrity_handler(request: Request, exc: Exception) -> JSONResponse:
    # Mensaje genérico: no se filtra el detalle del error de BD (puede revelar esquema).
    body = ErrorResponse(
        error=ErrorBody(
            type="IntegrityError",
            message="conflicto: el registro ya existe o viola una restricción",
            details=[],
            request_id=request_id_var.get(),
        )
    ).model_dump()
    return JSONResponse(status_code=409, content=body)


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DocumentDataError, _document_data_handler)
    # Antes que DteError: es una subclase y se evalúa por tipo exacto primero.
    app.add_exception_handler(SiiAuthError, _sii_auth_handler)
    app.add_exception_handler(DteError, _dte_error_handler)
    app.add_exception_handler(IntegrityError, _integrity_handler)
    app.add_exception_handler(CertificateUnavailable, _cert_unavailable_handler)
    app.add_exception_handler(SiiCredentialUnavailable, _sii_credential_unavailable_handler)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
