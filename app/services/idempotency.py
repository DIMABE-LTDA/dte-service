"""Emitir dos veces por un corte de red, no.

Sin esto, la secuencia era: el ERP pide emitir; el servicio toma un folio,
firma y sube el documento al SII; la respuesta se pierde en el camino —la red
se corta, o se agota el tiempo de espera, que además es el mismo a los dos
lados—; el ERP ve un error y el usuario vuelve a pulsar «Emitir». Se emitía otra
vez: otro folio, otro documento válido en el SII, la misma venta. Y no había
forma automática de descubrirlo.

Con una clave de idempotencia —que el ERP deriva de su propio documento, algo
como ``odoo:<bd>:<id del asiento>``— la segunda petición devuelve lo que se
emitió la primera vez en lugar de emitir de nuevo. Reintentar deja de ser
peligroso, que es justo lo que hace falta para que el ERP pueda reintentar solo.

La marca se inserta **antes** de emitir, no después: si dos peticiones con la
misma clave llegan a la vez, sólo una pasa; la otra recibe «en curso» en vez de
emitir un duplicado.

De paso, aquí queda archivado cada documento emitido. Hasta ahora el servicio
sólo guardaba el sobre en certificación —en producción la única copia viajaba
en la respuesta HTTP—, así que una respuesta perdida se llevaba el documento
con ella.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import json
from collections.abc import Callable
from typing import Any

from dte_chile.parser import ParseError, parse_documents
from lxml import etree
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import Customer, EmissionRequest, IssuedDocument

#: Estados de la marca de idempotencia.
RUNNING = "running"
DONE = "done"
FAILED = "failed"


class EmissionInProgress(Exception):
    """Otra petición con la misma clave está emitiendo ahora mismo."""


class EmissionAlreadyFailed(Exception):
    """La emisión con esta clave ya falló, y repetirla podría duplicar.

    No se reintenta sola: el folio pudo quedar consumido y el documento pudo
    llegar igual al SII. Quien integra revisa el inventario de folios y decide.
    """


def run(
    db: Session,
    customer: Customer,
    key: str | None,
    endpoint: str,
    fn: Callable[[], dict],
) -> dict:
    """Ejecuta una emisión; con ``key``, a lo sumo una vez."""
    if not key:
        result = fn()
        _archive(db, customer, result)
        return result

    marca = _claim(db, customer, key, endpoint)
    if marca is None:  # ya había una marca: la petición es un reintento
        return _replay(db, customer, key)

    try:
        result = fn()
    except Exception as ex:
        _close_failed(db, marca, ex)
        raise

    _archive(db, customer, result)
    marca.state = DONE
    marca.response_encrypted = crypto.encrypt(json.dumps(result, default=_plain).encode())
    marca.completed_at = _now()
    db.commit()
    return result


def _claim(db: Session, customer: Customer, key: str, endpoint: str) -> EmissionRequest | None:
    """Reserva la clave. Devuelve None si ya estaba tomada."""
    marca = EmissionRequest(customer_id=customer.id, key=key, endpoint=endpoint, state=RUNNING)
    db.add(marca)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return marca


def _replay(db: Session, customer: Customer, key: str) -> dict:
    marca = (
        db.query(EmissionRequest)
        .filter(EmissionRequest.customer_id == customer.id, EmissionRequest.key == key)
        .one()
    )
    if marca.state == DONE and marca.response_encrypted:
        return dict(json.loads(crypto.decrypt(marca.response_encrypted)))
    if marca.state == FAILED:
        raise EmissionAlreadyFailed(
            f"La emisión con la clave {key!r} ya falló ({marca.error or 'sin detalle'}). "
            "Puede haber consumido un folio: revisa el inventario de folios antes de "
            "reintentar, y usa otra clave si decides emitir de nuevo."
        )
    raise EmissionInProgress(
        f"Ya hay una emisión en curso con la clave {key!r}. Espera su resultado en vez "
        "de emitir otra vez."
    )


def _close_failed(db: Session, marca: EmissionRequest, ex: Exception) -> None:
    """Deja constancia del fallo.

    Un error de datos —el documento venía mal— se borra: no gastó nada y el ERP
    debe poder corregir y reintentar con la misma clave. Cualquier otro se
    guarda como fallido, porque pudo quedar un folio consumido y un documento
    en el SII.
    """
    from app.errors.exceptions import DomainError

    db.rollback()
    if isinstance(ex, DomainError | ValueError):
        db.delete(marca)
    else:
        marca.state = FAILED
        marca.error = str(ex)[:500]
        marca.completed_at = _now()
        db.add(marca)
    db.commit()


def _archive(db: Session, customer: Customer, result: Any) -> None:
    """Guarda cada documento del sobre emitido, para poder recuperarlo.

    Se guarda el ``<DTE>`` suelto y no el sobre entero: es lo que hace falta
    para reimprimirlo o entregarlo, y un sobre de lote traería documentos de
    otras ventas.
    """
    if not isinstance(result, dict):
        return
    xml_base64 = result.get("xml_base64")
    if not xml_base64:
        return
    try:
        documentos = parse_documents(base64.b64decode(xml_base64))
    except (ParseError, ValueError, etree.XMLSyntaxError):
        return  # boletas y libros tienen su propio archivo

    track_id = _track_id(result.get("submission"))
    for parsed in documentos:
        doc = parsed.dte
        nodo = parsed.element.getparent()  # <DTE>, con la firma
        if nodo is None:
            nodo = parsed.element
        existe = (
            db.query(IssuedDocument)
            .filter(
                IssuedDocument.customer_id == customer.id,
                IssuedDocument.doc_type == int(doc.type),
                IssuedDocument.folio == doc.folio,
            )
            .first()
        )
        if existe is not None:
            continue
        db.add(
            IssuedDocument(
                customer_id=customer.id,
                doc_type=int(doc.type),
                folio=doc.folio,
                track_id=track_id,
                xml_encrypted=crypto.encrypt(
                    etree.tostring(nodo, encoding="ISO-8859-1", xml_declaration=True)
                ),
            )
        )
    db.commit()


def stored_xml(db: Session, customer: Customer, doc_type: int, folio: int) -> bytes:
    """El XML firmado de un documento emitido."""
    row = (
        db.query(IssuedDocument)
        .filter(
            IssuedDocument.customer_id == customer.id,
            IssuedDocument.doc_type == doc_type,
            IssuedDocument.folio == folio,
        )
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"No hay un documento tipo {doc_type} folio {folio} emitido.")
    return crypto.decrypt(row.xml_encrypted)


def _track_id(submission: Any) -> str | None:
    if submission is None:
        return None
    if isinstance(submission, dict):
        valor = submission.get("track_id")
    else:
        valor = getattr(submission, "track_id", None)
    return str(valor) if valor else None


def _plain(value: Any) -> Any:
    """Para que la respuesta guardada sea JSON: fechas y objetos del motor."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
