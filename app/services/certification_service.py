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
import datetime as dt
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


def find_or_create_set(db, customer_id: int, code: str) -> CertificationSet:
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
            cert_set = find_or_create_set(db, customer.id, code) if code else None
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


def query_status(customer: Customer, cert, track_id: str, timeout_s: int) -> dict:
    """Pregunta al SII por un TrackID y devuelve su respuesta.

    Se guarda tal cual: el estado es del Servicio, no una deducción nuestra.
    """
    from dte_chile.sii_client import Environment, SIIClient

    client = SIIClient(cert, Environment[customer.environment.name], timeout=timeout_s)
    try:
        res = client.query_status(track_id, customer.rut)
    finally:
        client.session.close()
    return {"state": getattr(res, "status", None), "detail": getattr(res, "detail", None)}


def envelope(submission: CertificationSubmission) -> bytes:
    """Descifra el sobre guardado, para reimprimir o reenviar sin reemitir."""
    return crypto.decrypt(submission.envelope_encrypted)


# --------------------------------------------------------------------------- #
#  Expediente: etapas y semáforo
# --------------------------------------------------------------------------- #

# Respuestas del SII que cuentan como set entregado.
_ACEPTADOS = {"EPR", "LOK"}
# Rechazos explícitos. El resto (vacío, DOK, SOK...) queda en "en curso".
_RECHAZOS = {"RFR", "RCT", "RCH", "LRH", "LRS", "LRC", "LRF", "LNC", "RSC"}

_ETAPAS = (
    ("requisitos", "Requisitos"),
    ("emision", "Emisión"),
    ("envio", "Envío"),
    ("estado", "Estado SII"),
    ("declaracion", "Declaración"),
)


def _requisitos(db, customer: Customer) -> tuple[str, str]:
    """Certificado vigente y CAF con folios. Es lo único comprobable antes de emitir."""
    from app.db.models import Caf, CustomerCertificate

    hoy = dt.date.today()
    certs = (
        db.query(CustomerCertificate).filter(CustomerCertificate.customer_id == customer.id).all()
    )
    if not certs:
        return "error", "sin certificado cargado: el cliente no puede firmar"
    vigente = max(c.due_date for c in certs)
    if vigente < hoy:
        return "error", f"el certificado venció el {vigente:%d-%m-%Y}"

    cafs = db.query(Caf).filter(Caf.customer_id == customer.id, Caf.exhausted.is_(False)).count()
    if not cafs:
        return "error", "sin CAF disponibles: no hay folios que asignar"
    dias = (vigente - hoy).days
    if dias < 30:
        return "atencion", f"el certificado vence en {dias} días"
    return "ok", f"certificado vigente hasta {vigente:%d-%m-%Y} · {cafs} CAF disponibles"


def stages(db, customer: Customer, cert_set) -> list[dict]:
    """Las cinco etapas del set, con su color y el porqué.

    El verde de la última significa "no queda nada que hacer con este set". Un
    semáforo que se pone verde al enviar mentiría: enviado no es aceptado, y
    aceptado no es declarado.
    """
    envios = sorted(cert_set.submissions, key=lambda s: s.sent_at)
    ultimo = envios[-1] if envios else None
    estados = {e.sii_state for e in envios if e.sii_state}

    req_state, req_detail = _requisitos(db, customer)
    out = [{"key": "requisitos", "label": "Requisitos", "state": req_state, "detail": req_detail}]

    if not envios:
        out += [
            {"key": k, "label": lbl, "state": "pendiente", "detail": ""} for k, lbl in _ETAPAS[1:]
        ]
        return out

    docs = sum(len(e.documents) for e in envios[-1:])
    out.append(
        {
            "key": "emision",
            "label": "Emisión",
            "state": "ok",
            "detail": f"{docs} documento(s) en el último envío",
        }
    )
    intentos = len(envios)
    out.append(
        {
            "key": "envio",
            "label": "Envío",
            "state": "ok",
            "detail": f"TrackID {ultimo.track_id}"
            + (f" · {intentos} intentos" if intentos > 1 else ""),
        }
    )

    if ultimo.sii_state is None:
        out.append(
            {
                "key": "estado",
                "label": "Estado SII",
                "state": "pendiente",
                "detail": "sin consultar",
            }
        )
    elif ultimo.sii_state in _ACEPTADOS:
        out.append(
            {
                "key": "estado",
                "label": "Estado SII",
                "state": "ok",
                "detail": f"{ultimo.sii_state} · {ultimo.sii_detail or 'aceptado'}",
            }
        )
    elif ultimo.sii_state in _RECHAZOS:
        out.append(
            {
                "key": "estado",
                "label": "Estado SII",
                "state": "error",
                "detail": f"{ultimo.sii_state} · {ultimo.sii_detail or 'rechazado'}",
            }
        )
    else:
        out.append(
            {
                "key": "estado",
                "label": "Estado SII",
                "state": "atencion",
                "detail": f"{ultimo.sii_state} · en proceso",
            }
        )

    if cert_set.declared_at:
        out.append(
            {
                "key": "declaracion",
                "label": "Declaración",
                "state": "ok",
                "detail": f"declarado el {cert_set.declared_at:%d-%m-%Y}",
            }
        )
    else:
        listo = bool(estados & _ACEPTADOS)
        out.append(
            {
                "key": "declaracion",
                "label": "Declaración",
                "state": "atencion" if listo else "pendiente",
                "detail": "falta declarar el avance en Mi SII" if listo else "",
            }
        )
    return out


def set_state(etapas: list[dict]) -> str:
    """Estado resumido del set, a partir de sus etapas."""
    por_clave = {e["key"]: e["state"] for e in etapas}
    if por_clave.get("declaracion") == "ok":
        return "declarado"
    if por_clave.get("estado") == "error":
        return "rechazado"
    if por_clave.get("estado") == "ok":
        return "aceptado"
    if por_clave.get("envio") == "ok":
        return "enviado"
    return "pendiente"
