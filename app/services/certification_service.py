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
    # El desglose por tipo se guarda con el estado: sin él, un "EPR / Envío
    # Procesado" con todos sus documentos rechazados dentro se lee como éxito.
    stats = [
        {
            "doc_type": s.doc_type,
            "informed": s.informed,
            "accepted": s.accepted,
            "rejected": s.rejected,
            "flagged": s.flagged,
        }
        for s in getattr(res, "stats", []) or []
    ]
    return {
        "state": getattr(res, "status", None),
        "detail": getattr(res, "detail", None),
        "stats": stats,
    }


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


def doc_counts(envio) -> tuple[int, int, int, int]:
    """(informados, aceptados, rechazados, con reparos) de un envío.

    Todo ceros cuando no hay desglose: los libros no lo traen, y un envío que
    aún no se ha consultado tampoco. En ese caso el estado del sobre es lo
    único que hay, y se usa tal cual.
    """
    filas = getattr(envio, "sii_stats", None) or []
    return (
        sum(f.get("informed", 0) for f in filas),
        sum(f.get("accepted", 0) for f in filas),
        sum(f.get("rejected", 0) for f in filas),
        sum(f.get("flagged", 0) for f in filas),
    )


def entregado(envio) -> bool:
    """True si el envío puede darse por entregado ante el SII.

    Un sobre procesado con todos sus documentos rechazados NO lo está, por
    mucho que su estado sea EPR. Cuando no hay desglose se cree al estado: es
    el caso de los libros, cuyo LOK sí es el veredicto entero.

    Los documentos **aceptados con reparo** cuentan como entregados: el SII los
    aceptó y quedaron registrados; el reparo es una observación sobre el
    contenido, no un rechazo —los rechazados van en su propia columna—. Contarlos
    como no entregados dejaba fuera del Libro de Ventas documentos que el SII sí
    tiene, que es justo el descuadre que el libro viene a evitar.
    """
    if envio.sii_state not in _ACEPTADOS:
        return False
    informados, aceptados, _rechazados, reparos = doc_counts(envio)
    if not informados:
        return True
    return (aceptados + reparos) > 0


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

    req_state, req_detail = _requisitos(db, customer)
    out = [{"key": "requisitos", "label": "Requisitos", "state": req_state, "detail": req_detail}]

    if not envios:
        out += [
            {"key": k, "label": lbl, "state": "pendiente", "detail": ""} for k, lbl in _ETAPAS[1:]
        ]
        return out
    ultimo = envios[-1]

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
        informados, aceptados, rechazados, reparos = doc_counts(ultimo)
        if informados and not aceptados:
            # El sobre se procesó y su contenido entero se cayó. Verde aquí
            # sería exactamente la lectura que hizo perder una semana.
            out.append(
                {
                    "key": "estado",
                    "label": "Estado SII",
                    "state": "error",
                    "detail": f"{ultimo.sii_state} · ninguno de los {informados}"
                    f" documentos fue aceptado ({rechazados} rechazados)",
                }
            )
        elif rechazados or reparos:
            out.append(
                {
                    "key": "estado",
                    "label": "Estado SII",
                    "state": "atencion",
                    "detail": f"{ultimo.sii_state} · {aceptados} de {informados} aceptados"
                    + (f" · {rechazados} rechazados" if rechazados else "")
                    + (f" · {reparos} con reparos" if reparos else ""),
                }
            )
        else:
            detalle = ultimo.sii_detail or "aceptado"
            out.append(
                {
                    "key": "estado",
                    "label": "Estado SII",
                    "state": "ok",
                    "detail": f"{ultimo.sii_state} · {detalle}"
                    + (f" · {aceptados} documentos aceptados" if aceptados else ""),
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
        # Declarar un set cuyo contenido el SII rechazó sería declarar en falso.
        listo = any(entregado(e) for e in envios)
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

    salida: list[dict] = []
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
    from app.schemas.receipt import ReceiptBatchRequest
    from app.services import book_service, dte_service, receipt_service

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
        "boletas": (ReceiptBatchRequest, receipt_service.issue_batch, True),
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


def _documentos_para_consultar(xml: bytes) -> list[dict]:
    """(tipo, folio, fecha, RUT receptor, monto) de cada documento del sobre.

    El monto es el ``MntTotal`` del propio documento, **en su moneda**, no su
    equivalente en pesos. El SII compara la tupla contra lo que registró, y lo
    que registró es lo que venía en el documento: preguntar por una factura de
    exportación con el monto en pesos devuelve DNK —«Datos NO Coinciden»— aunque
    el documento esté perfectamente aceptado. Se detectó así, y despistó: DNK
    parecía un reparo y era la consulta mal armada.

    ``MontoDte`` es entero y un documento en moneda extranjera lleva decimales,
    así que hay que recortarlos. Se **trunca**, no se redondea: con redondeo,
    160677.62 se consultaba como 160678 y el SII respondía DNK, mientras que los
    documentos de monto entero coincidían. Que sólo fallaran los que tenían
    decimales fue lo que delató el criterio.
    """
    from decimal import Decimal

    raiz = etree.fromstring(xml)
    salida = []
    for nodo in raiz.iter():
        if _local(nodo.tag) not in ("Documento", "Exportaciones", "Liquidacion"):
            continue
        campos: dict[str, str] = {}
        for hijo in nodo.iter():
            nombre = _local(hijo.tag)
            if nombre in ("TipoDTE", "Folio", "FchEmis", "RUTRecep", "MntTotal"):
                campos.setdefault(nombre, (hijo.text or "").strip())
        if not campos.get("TipoDTE") or not campos.get("Folio"):
            continue
        bruto = campos.get("MntTotal") or "0"
        salida.append(
            {
                "doc_type": int(campos["TipoDTE"]),
                "folio": int(campos["Folio"]),
                "date": campos.get("FchEmis", ""),
                "rut": campos.get("RUTRecep", ""),
                "total_amount": int(Decimal(bruto)),
            }
        )
    return salida


def document_statuses(db, customer: Customer, cert, envio, timeout: int = 60) -> list[dict]:
    """Pregunta al SII, documento por documento, cómo quedó cada uno.

    El desglose que devuelve el TrackID dice cuántos con reparo, no cuál ni por
    qué: para saberlo había que pedirle al SII el detalle por correo. Esto lo
    consulta directamente.

    Los datos del documento salen del sobre que se envió, no de la definición:
    lo que el SII conoce es lo que recibió, y la definición pudo cambiar después.
    """
    from dte_chile.sii_client import Environment, SIIClient

    if not envio.track_id:
        raise EmissionError("este sobre no se ha enviado: no hay nada que consultar")

    lineas = _documentos_para_consultar(envelope(envio))
    if not lineas:
        return []

    cliente = SIIClient(cert, Environment.CERTIFICATION, timeout=timeout)
    salida = []
    for linea in lineas:
        try:
            estado = cliente.query_document(
                issuer_rut=customer.rut,
                receiver_rut=linea["rut"],
                doc_type=linea["doc_type"],
                folio=linea["folio"],
                issue_date=dt.date.fromisoformat(str(linea["date"])),
                total_amount=linea["total_amount"],
            )
            salida.append(
                {
                    "doc_type": estado.doc_type,
                    "folio": estado.folio,
                    "status": estado.status,
                    "label": estado.label,
                    "error_label": estado.error_label,
                }
            )
        except Exception as ex:  # noqa: BLE001
            # Un documento que falla no puede dejar sin respuesta a los demás:
            # lo habitual es que el interesante sea justo otro.
            salida.append(
                {
                    "doc_type": int(linea["doc_type"]),
                    "folio": int(linea["folio"]),
                    "status": "?",
                    "label": "",
                    "error_label": f"no se pudo consultar: {ex}",
                }
            )
    return salida


def discard(db, envio: CertificationSubmission) -> None:
    """Descarta un sobre emitido que nunca se envió.

    Sirve para corregir la definición y volver a emitir sin arrastrar el sobre
    viejo, que si no bloquea la emisión. Es seguro porque el SII nunca lo vio:
    no hay TrackID, no hay nada que contradecir.

    Lo que NO devuelve son los folios. El puntero ya avanzó y rebobinarlo es la
    operación más peligrosa del sistema —dos procesos entregando el mismo folio
    es el peor error posible acá—, así que los folios de un sobre descartado
    quedan sin usar. Para el SII eso no es problema: un folio que nunca llegó no
    existe, y el timbraje los sigue contando como disponibles.

    Un sobre CON TrackID no se borra nunca: es el registro de lo que se le
    entregó al Servicio, y su respuesta se consulta contra él.
    """
    if envio.track_id:
        raise EmissionError(
            f"el sobre #{envio.id} ya se envió al SII (TrackID {envio.track_id})."
            " Lo enviado no se borra: es el registro de lo que recibió el Servicio."
        )
    db.delete(envio)
    db.commit()


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

    # Lo que depende del cliente o del día —emisor, fecha, referencia al caso,
    # período y líneas de los libros— lo pone el sistema, no la definición.
    from app.services import certification_fill

    if definicion.endpoint in certification_fill.DOC_ENDPOINTS:
        from app.services import customer_service

        faltan = customer_service.issuer_missing(customer)
        if faltan:
            # Mejor detenerse aquí que dejar que el esquema falle con un
            # error de validación ilegible: esto lo arregla una persona en la
            # ficha del cliente, y hay que decirle qué.
            raise EmissionError(
                "faltan datos del emisor en la ficha del cliente: "
                + ", ".join(faltan)
                + ". Se usan en el encabezado de cada documento."
            )
    cuerpo, _notas = certification_fill.fill(
        db, customer, cert_set, definicion.endpoint, definicion.payload
    )
    if definicion.endpoint in certification_fill.BOOK_ENDPOINTS and not cuerpo.get("lines"):
        raise EmissionError(
            "el libro no tiene líneas: " + ("; ".join(_notas) or "no hay documentos que declarar")
        )
    # send=False siempre: en esta ruta emitir NO envía.
    req = schema.model_validate({**cuerpo, "send": False})
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
        signed_thumbprint=_thumbprint(db, customer),
    )
    db.add(envio)
    db.flush()
    for doc_type, folio in docs:
        db.add(CertificationDocument(submission_id=envio.id, doc_type=doc_type, folio=folio))
    db.commit()
    db.refresh(envio)
    return envio


def _thumbprint(db, customer: Customer) -> str | None:
    """Huella del certificado con el que se está firmando ahora mismo."""
    from app.db.models import CustomerCertificate

    fila = (
        db.query(CustomerCertificate)
        .filter(
            CustomerCertificate.customer_id == customer.id,
            CustomerCertificate.due_date >= dt.date.today(),
        )
        .order_by(CustomerCertificate.created_at.desc())
        .first()
    )
    return fila.thumbprint if fila else None


def stale_signature(db, customer: Customer, envio: CertificationSubmission) -> bool:
    """True si el sobre se firmó con un certificado que ya no es el vigente.

    Enviarlo así gasta un TrackID para nada: la firma va dentro del XML y no se
    rehace al cambiar el certificado. Es exactamente lo que pasó con el primer
    envío real de esta certificación.

    Si no se sabe con cuál se firmó —sobres anteriores a que se guardara— no se
    afirma nada: un falso positivo aquí bloquearía un envío legítimo.
    """
    if envio.signed_thumbprint is None:
        return False
    actual = _thumbprint(db, customer)
    return actual is not None and actual != envio.signed_thumbprint


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


# --------------------------------------------------------------------------- #
#  Muestras de impresión (paso 5 del trámite)
# --------------------------------------------------------------------------- #


def printable_envelopes(db, customer: Customer) -> list[CertificationSubmission]:
    """Los sobres que deben ir en las muestras de impresión.

    Sólo los **enviados**: el SII pide la impresión de los documentos del set de
    pruebas, y un sobre que se emitió pero no se envió no es parte del set. Y
    sólo el último aceptado de cada set, para no imprimir los intentos
    rechazados junto al bueno.
    """
    envios = (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.customer_id == customer.id,
            CertificationSubmission.track_id.isnot(None),
        )
        .order_by(CertificationSubmission.id)
        .all()
    )
    por_set: dict[int | None, CertificationSubmission] = {}
    for envio in envios:
        # El aceptado manda; si ninguno lo está todavía, vale el último enviado.
        actual = por_set.get(envio.set_id)
        mejor = (
            actual is None or envio.sii_state in _ACEPTADOS or actual.sii_state not in _ACEPTADOS
        )
        if mejor:
            por_set[envio.set_id] = envio
    return list(por_set.values())


def print_samples(db, customer: Customer, sii_office: str = "SANTIAGO") -> dict:
    """Genera los impresos de todos los sobres del expediente.

    El paso 5 exige la representación impresa de **todos** los documentos del
    set, con su timbre PDF417. Sin los sobres guardados esto no se podía hacer:
    el servicio no almacena DTE y de la tanda aceptada se habían perdido seis.
    """
    from app.schemas.dte import PrintRequest
    from app.services import dte_service

    documentos = []
    saltados = []
    for envio in printable_envelopes(db, customer):
        # Los libros no tienen representación impresa: son un registro, no un
        # documento tributario que se entregue a nadie.
        if envio.envelope_kind not in ("EnvioDTE", "EnvioBOLETA"):
            saltados.append({"track_id": envio.track_id, "reason": envio.envelope_kind})
            continue
        req = PrintRequest(
            xml_base64=base64.b64encode(envelope(envio)).decode("ascii"),
            copies="both",
            sii_office=sii_office,
        )
        try:
            resultado = dte_service.print_documents(customer, req)
        except Exception as ex:  # noqa: BLE001 - se informa, no se interrumpe
            saltados.append({"track_id": envio.track_id, "reason": str(ex)[:200]})
            continue
        for doc in resultado["documents"]:
            documentos.append({**doc, "track_id": envio.track_id})
    return {"documents": documentos, "skipped": saltados}
