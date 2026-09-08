"""Captura del expediente de certificación.

Guarda, de cada envío al SII de un cliente en ambiente de **certificación**, el
TrackID, el sobre exacto que se subió y qué venía dentro. Hasta ahora nada de
eso se persistía: el TrackID llegaba en la respuesta y se perdía si nadie lo
copiaba a mano, y el sobre no se guardaba en absoluto. El resultado fueron dos
juegos de identificadores contradictorios y seis sobres irrecuperables — los que
hacen falta para las muestras de impresión que exige el SII.

Dos reglas que gobiernan este módulo:

**Nunca puede romper una emisión.** Cuando se llama, el folio ya está gastado y
el documento ya está en el SII: fallar aquí no debe convertir un envío bueno en
un error. Por eso usa su propia sesión —como el access-log— y se traga sus
excepciones dejando rastro en el log.

**Sólo certificación.** En producción no captura nada. El servicio no guarda
DTE por diseño; ésta es una excepción acotada a un corpus finito y temporal.
"""

from __future__ import annotations

import contextvars
import logging

from lxml import etree

import app.db.session as db_session
from app.core import crypto
from app.db.models import (
    CertificationDocument,
    CertificationSet,
    CertificationSubmission,
    Customer,
    SiiEnvironment,
)

logger = logging.getLogger(__name__)

# Set al que pertenece el envío en curso, tomado de la cabecera
# ``X-Certification-Set``. Va en un contextvar y no como parámetro porque
# atravesaría ocho funciones de emisión para un dato opcional; es el mismo
# mecanismo que ya usa ``request_id_var`` para el id de petición.
certification_set_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "certification_set", default=None
)

# Raíz del sobre → etiqueta legible. El SII usa canales distintos para cada uno.
_ENVELOPE_KINDS = {
    "EnvioDTE": "EnvioDTE",
    "EnvioBOLETA": "EnvioBOLETA",
    "LibroCompraVenta": "LibroCompraVenta",
    "LibroGuia": "LibroGuia",
}


def _local(tag: object) -> str:
    """Nombre de etiqueta sin su namespace."""
    return str(tag).rsplit("}", 1)[-1]


def _contents(root: etree._Element) -> tuple[str, list[tuple[int, int]]]:
    """Devuelve (tipo de sobre, [(tipo documento, folio), ...]).

    Un sobre de documentos los lleva en ``TipoDTE``/``Folio``; un libro, en las
    líneas de su ``Detalle`` (``TpoDoc``/``NroDoc``). Se registran ambos: lo que
    interesa es qué declaraba el sobre, venga de donde venga.
    """
    kind = _ENVELOPE_KINDS.get(_local(root.tag), _local(root.tag))
    docs: list[tuple[int, int]] = []

    for parent, tipo, folio in (("IdDoc", "TipoDTE", "Folio"), ("Detalle", "TpoDoc", "NroDoc")):
        for node in root.iter():
            if _local(node.tag) != parent:
                continue
            t = f = None
            for child in node:
                if _local(child.tag) == tipo:
                    t = child.text
                elif _local(child.tag) == folio:
                    f = child.text
            if t and f and t.strip().isdigit() and f.strip().isdigit():
                docs.append((int(t), int(f)))
    return kind, docs


def _find_or_create_set(db, customer_id: int, code: str) -> CertificationSet:
    row = (
        db.query(CertificationSet)
        .filter(CertificationSet.customer_id == customer_id, CertificationSet.code == code)
        .one_or_none()
    )
    if row is None:
        row = CertificationSet(customer_id=customer_id, code=code, state="enviado")
        db.add(row)
        db.flush()
    return row


def capture(customer: Customer, xml: bytes, track_id: str | None) -> None:
    """Registra un envío del expediente. No hace nada fuera de certificación.

    Se llama DESPUÉS de que el SII acepta el sobre, así que un fallo aquí no
    puede deshacer nada: se registra en el log y la emisión sigue su curso.
    """
    if customer.environment != SiiEnvironment.CERTIFICATION or not track_id:
        return
    try:
        kind, docs = _contents(etree.fromstring(xml))
        code = certification_set_var.get()
        with db_session.SessionLocal() as db:
            cert_set = _find_or_create_set(db, customer.id, code) if code else None
            submission = CertificationSubmission(
                set_id=cert_set.id if cert_set else None,
                customer_id=customer.id,
                track_id=str(track_id),
                envelope_kind=kind,
                envelope_encrypted=crypto.encrypt(xml),
            )
            db.add(submission)
            db.flush()
            for doc_type, folio in docs:
                db.add(
                    CertificationDocument(
                        submission_id=submission.id, doc_type=doc_type, folio=folio
                    )
                )
            db.commit()
    except Exception:
        # El folio ya está gastado y el documento ya está en el SII: un fallo
        # guardando la evidencia no puede convertir eso en un error para quien
        # emitió. Queda en el log para que no pase inadvertido.
        logger.exception("no se pudo registrar el envío de certificación (track %s)", track_id)


def envelope(db, submission: CertificationSubmission) -> bytes:
    """Descifra el sobre guardado, para reimprimir o reenviar sin reemitir."""
    return crypto.decrypt(submission.envelope_encrypted)
