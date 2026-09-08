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

import base64
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
                # Se captura después de enviar, así que la fecha es ahora. El
                # default del modelo es None porque un sobre emitido y sin
                # enviar todavía no tiene fecha de envío.
                sent_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
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
    envios = sorted(cert_set.submissions, key=lambda s: s.id)
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
    intentos = len([e for e in envios if e.track_id])
    if ultimo.track_id is None:
        # Hay un sobre emitido esperando envío: los folios ya se gastaron pero el
        # SII todavía no lo tiene. Pintarlo verde diría que el set está entregado.
        out.append(
            {
                "key": "envio",
                "label": "Envío",
                "state": "atencion",
                "detail": "hay un sobre emitido sin enviar"
                + (f" · {intentos} enviados antes" if intentos else ""),
            }
        )
    else:
        out.append(
            {
                "key": "envio",
                "label": "Envío",
                "state": "ok",
                "detail": f"TrackID {ultimo.track_id}"
                + (f" · {intentos} intentos" if intentos > 1 else ""),
            }
        )

    if ultimo.track_id is None:
        out.append(
            {
                "key": "estado",
                "label": "Estado SII",
                "state": "pendiente",
                "detail": "el sobre aún no se ha enviado",
            }
        )
    elif ultimo.sii_state is None:
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


# --------------------------------------------------------------------------- #
#  El trámite completo: qué sets pide el SII y en qué paso va la postulación
# --------------------------------------------------------------------------- #


def expected_sets(db, customer: Customer) -> list[dict]:
    """Los diez sets del trámite, existan o no todavía en la base.

    Es la diferencia entre un expediente que muestra lo que llegó y uno que
    muestra lo que falta. Un set sin enviar tiene que verse: es el que hay que
    hacer.
    """
    from app.services.certification_catalog import BY_KIND, SET_TYPES

    existentes = {
        s.kind: s
        for s in db.query(CertificationSet).filter(CertificationSet.customer_id == customer.id)
    }
    # Un set con número de atención pero sin kind reconocido (se dio de alta al
    # vuelo desde la cabecera) se muestra igual, al final: perderlo sería peor.
    sueltos = [s for k, s in existentes.items() if k not in BY_KIND]

    salida = []
    for tipo in SET_TYPES:
        cert_set = existentes.get(tipo.kind)
        if cert_set is None:
            salida.append(
                {
                    "id": None,
                    "code": "",
                    "kind": tipo.kind,
                    "state": "sin_dar_de_alta",
                    "declared_at": None,
                    "stages": [],
                    "submissions": [],
                }
            )
            continue
        etapas = stages(db, customer, cert_set)
        salida.append(
            {
                "id": cert_set.id,
                "code": cert_set.code,
                "kind": cert_set.kind,
                "state": set_state(etapas),
                "declared_at": cert_set.declared_at,
                "stages": etapas,
                "submissions": sorted(cert_set.submissions, key=lambda x: x.id),
            }
        )
    for cert_set in sueltos:
        etapas = stages(db, customer, cert_set)
        salida.append(
            {
                "id": cert_set.id,
                "code": cert_set.code,
                "kind": cert_set.kind,
                "state": set_state(etapas),
                "declared_at": cert_set.declared_at,
                "stages": etapas,
                "submissions": sorted(cert_set.submissions, key=lambda x: x.id),
            }
        )
    return salida


def progress(sets: list[dict]) -> dict:
    """Cuántos sets van.

    ``sets_total`` sale del **catálogo**, no de las filas: un envío que se dio de
    alta al vuelo sin clasificar se muestra, pero no infla el denominador. El
    trámite pide diez sets y el contador tiene que decir diez.
    """
    from app.services.certification_catalog import BY_KIND

    del_tramite = [s for s in sets if s["kind"] in BY_KIND]
    return {
        "sets_total": len(del_tramite),
        "sets_declared": sum(1 for s in del_tramite if s["state"] == "declarado"),
        "sets_accepted": sum(1 for s in del_tramite if s["state"] in ("aceptado", "declarado")),
        "sets_pending": sum(
            1 for s in del_tramite if s["state"] in ("sin_dar_de_alta", "pendiente", "enviado")
        ),
    }


def steps(db, customer: Customer, sets: list[dict]) -> list[dict]:
    """Los seis pasos del trámite.

    El primero lo deduce el sistema de los sets; los otros cinco ocurren fuera
    —en el sitio del SII o por correo— y los confirma el operador.
    """
    from app.db.models import CertificationMilestone
    from app.services.certification_catalog import STEPS

    hitos = {
        m.step: m
        for m in db.query(CertificationMilestone).filter(
            CertificationMilestone.customer_id == customer.id
        )
    }
    p = progress(sets)
    salida = []
    for paso in STEPS:
        hito = hitos.get(paso.key)
        if paso.key == "sets":
            if p["sets_declared"] == p["sets_total"] and p["sets_total"]:
                estado = "ok"
            elif p["sets_accepted"]:
                estado = "atencion"
            else:
                estado = "pendiente"
            detalle = (
                f"{p['sets_declared']} de {p['sets_total']} declarados"
                f" · {p['sets_accepted']} aceptados por el SII"
            )
        else:
            estado = "ok" if hito and hito.done_at else "pendiente"
            detalle = paso.detail
        salida.append(
            {
                "key": paso.key,
                "label": paso.label,
                "detail": detalle,
                "automatic": paso.automatic,
                "state": estado,
                "done_at": hito.done_at if hito else None,
                "note": hito.note if hito else "",
            }
        )
    return salida


# --------------------------------------------------------------------------- #
#  Emisión guiada: emitir y enviar son dos actos distintos
# --------------------------------------------------------------------------- #

#: endpoint de la definición → (schema de la petición, función del servicio).
#: Se resuelve tarde para no arrastrar los servicios de emisión al importar.
def _emitters():
    from app.schemas.book import BookRequest, GuideBookRequest
    from app.schemas.dte import DteBatchRequest, ExportBatchRequest, SettlementBatchRequest
    from app.services import book_service, dte_service

    return {
        "issue-batch": (DteBatchRequest, dte_service.issue_batch, True),
        "issue-export-batch": (ExportBatchRequest, dte_service.issue_export_batch, True),
        "issue-settlement-batch": (
            SettlementBatchRequest,
            dte_service.issue_settlement_batch,
            True,
        ),
        "books": (BookRequest, book_service.build, False),
        "books/guides": (GuideBookRequest, book_service.build_guides, False),
    }


class EmissionError(Exception):
    """Error de emisión guiada (se mapea a 4xx en el router)."""


def draft_for(db, cert_set) -> CertificationSubmission | None:
    """El sobre emitido y aún sin enviar de este set, si lo hay."""
    return (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.set_id == cert_set.id,
            CertificationSubmission.track_id.is_(None),
        )
        .order_by(CertificationSubmission.id.desc())
        .first()
    )


def emit(db, customer: Customer, cert, cert_set, *, force: bool = False) -> CertificationSubmission:
    """Emite el set según su definición **sin enviarlo** al SII.

    Emitir consume folios y no se deshace. Por eso:

    - Si ya hay un sobre emitido sin enviar, no se emite otro salvo que se
      insista: es la protección contra el doble clic y contra el reintento
      distraído. Los registros de esta certificación muestran el tipo 33 gastado
      hasta el folio 22 para un set que necesitaba cuatro documentos.
    - El sobre queda guardado en el acto, antes de cualquier envío, así que
      aunque el envío falle los folios no se pierden: se reenvía el mismo sobre.
    """
    from app.db.models import CertificationDefinition

    definicion = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == cert_set.id)
        .one_or_none()
    )
    if definicion is None:
        raise EmissionError("este set todavía no tiene definido qué emitir")

    previo = draft_for(db, cert_set)
    if previo is not None and not force:
        raise EmissionError(
            f"el set ya tiene un sobre emitido y sin enviar (#{previo.id});"
            " envíalo o vuelve a emitir de forma explícita, sabiendo que gastarás"
            " folios nuevos"
        )

    emisores = _emitters()
    if definicion.endpoint not in emisores:
        raise EmissionError(f"endpoint desconocido: {definicion.endpoint}")
    schema, funcion, con_db = emisores[definicion.endpoint]

    # send=False siempre: en esta ruta emitir NO envía.
    req = schema.model_validate({**definicion.payload, "send": False})
    resultado = funcion(db, customer, cert, req) if con_db else funcion(customer, cert, req)

    xml = base64.b64decode(resultado["xml_base64"])
    kind, docs = _contents(etree.fromstring(xml))
    envio = CertificationSubmission(
        set_id=cert_set.id,
        customer_id=customer.id,
        track_id=None,
        sent_at=None,
        envelope_kind=kind,
        envelope_encrypted=crypto.encrypt(xml),
    )
    db.add(envio)
    db.flush()
    for doc_type, folio in docs:
        db.add(CertificationDocument(submission_id=envio.id, doc_type=doc_type, folio=folio))
    db.commit()
    db.refresh(envio)
    return envio


def send_draft(db, customer: Customer, cert, envio: CertificationSubmission, timeout_s: int):
    """Sube al SII un sobre ya emitido y guarda su TrackID.

    Reenviar el mismo sobre no cuesta folios: es la razón de separar emitir de
    enviar.
    """
    from app.services import sii_upload

    xml = envelope(envio)
    # capture=False: la fila ya existe, sólo le falta el TrackID.
    resultado = sii_upload.upload(customer, cert, xml, customer.rut, timeout_s, capture=False)
    envio.track_id = str(resultado.track_id) if resultado.track_id else None
    envio.sent_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(envio)
    return envio
