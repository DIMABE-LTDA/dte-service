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
import copy
import datetime as dt
import logging
from zoneinfo import ZoneInfo

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
from app.services.certification_fill import MIXED_ENDPOINT

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
    "ConsumoFolios": "ConsumoFolios",
}


def _por_api_de_boleta(envio) -> bool:
    """¿Este envío salió por la API REST de boleta y no por Maullín?

    Hoy ninguno: todo sube por Maullín (ver `send_draft`). Pero los sobres de
    boletas del 16-09-2026 sí salieron por la API, y su estado sólo lo conoce
    ella. Se distinguen por el TrackID: Maullín los da de 10 dígitos
    (0259039476); la API de boleta, más cortos (32169835).
    """
    track = envio.track_id or ""
    return envio.envelope_kind == "EnvioBOLETA" and 0 < len(track) < 10


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


def find_or_create_set_by_kind(db, customer_id: int, kind: str) -> CertificationSet:
    """El set de un tipo que el SII no numera, identificado por su tipo.

    El de boletas es el caso: no lleva número de atención —no está en el
    formulario «Declarar avance», es el paso 2 del trámite— así que no hay
    código por el que buscarlo. Sin esto el import lo descartaba en silencio y
    la verificación pedía «copia su número de atención» de un número que el
    Servicio nunca entrega, bloqueando la emisión de todo lo demás.
    """
    row = (
        db.query(CertificationSet)
        .filter(CertificationSet.customer_id == customer_id, CertificationSet.kind == kind)
        .one_or_none()
    )
    if row is None:
        row = CertificationSet(customer_id=customer_id, code="", kind=kind, state="pendiente")
        db.add(row)
        db.flush()
    return row


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


def _estado_boletas(customer: Customer, cert, track_id: str, timeout_s: int) -> dict:
    """Estado de un envío de boletas, por la API REST de boleta.

    Se lleva al mismo formato que el de facturas —``state``, ``detail``, ``stats``—
    para que el expediente lo muestre igual. Los errores de cada boleta rechazada
    o con reparo van en ``detail``: es lo que hay que corregir.
    """
    from dte_chile.receipt_client import ReceiptClient, ReceiptEnvironment

    client = ReceiptClient(cert, ReceiptEnvironment[customer.environment.name], timeout=timeout_s)
    try:
        res = client.submission_status(track_id, customer.rut)
    finally:
        client.session.close()
    stats = [
        {
            "doc_type": s.get("tipo"),
            "informed": s.get("informados", 0),
            "accepted": s.get("aceptados", 0),
            "rejected": s.get("rechazados", 0),
            "flagged": s.get("reparos", 0),
        }
        for s in res.stats
    ]
    errores = [
        f"{d.get('tipo')}-{d.get('folio')} {d.get('estado')}: "
        + "; ".join(str(e.get("descripcion", "")) for e in d.get("error") or [])
        for d in res.details
    ]
    return {"state": res.state, "detail": " | ".join(errores) or None, "stats": stats}


def refresh(db, customer: Customer, cert, envio: CertificationSubmission, timeout_s: int):
    """Consulta al SII el estado de un envío y lo guarda tal cual.

    Se consulta donde se subió: Maullín. La excepción son los sobres de boletas
    que salieron por la API REST antes de corregir el canal, que sólo esa API
    conoce.
    """
    track_id = envio.track_id
    if not track_id:
        raise EmissionError("este sobre todavía no se envía: no hay TrackID que consultar")
    if _por_api_de_boleta(envio):
        estado = _estado_boletas(customer, cert, track_id, timeout_s)
    else:
        estado = query_status(customer, cert, track_id, timeout_s)
    envio.sii_state = estado.get("state")
    envio.sii_detail = estado.get("detail")
    envio.sii_stats = estado.get("stats") or None
    envio.checked_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(envio)
    return envio


#: Estados con que el SII dice que todavía está procesando un sobre: recibido
#: (REC), esquema validado (SOK), carátula OK (CRT), firma OK (FOK), pendiente
#: (PDR) y libro en proceso (LSO). Un envío así aún no es ni aceptado ni
#: rechazado.
_EN_PROCESO = {"REC", "SOK", "CRT", "FOK", "PDR", "LSO"}


def refresh_book_sources(db, customer: Customer, cert, cert_set, timeout_s: int) -> list[str]:
    """Consulta los envíos sin veredicto de los sets que alimentan un libro.

    Un libro generado se arma con el último envío **aceptado** de cada set. Si el
    envío más reciente se mandó pero nadie lo consultó, queda sin estado, no
    cuenta como aceptado, y el libro cae en silencio al envío anterior. Pasó en
    la certificación: el libro de ventas salió con los folios de exportación de
    un sobre rechazado, porque el bueno nunca se había consultado.

    Devuelve los envíos que siguen sin veredicto tras consultar —o que no se
    pudieron consultar—, para no armar el libro con ellos pendientes.
    """
    from app.services import certification_fill

    kinds = certification_fill.GENERATED_BOOKS.get(cert_set.kind)
    if not kinds:
        return []
    envios = (
        db.query(CertificationSubmission)
        .join(CertificationSet, CertificationSubmission.set_id == CertificationSet.id)
        .filter(
            CertificationSet.customer_id == customer.id,
            CertificationSet.kind.in_(kinds),
            CertificationSubmission.track_id.isnot(None),
        )
        .order_by(CertificationSubmission.id)
        .all()
    )
    pendientes = []
    for envio in envios:
        if envio.sii_state is not None and envio.sii_state not in _EN_PROCESO:
            continue
        try:
            refresh(db, customer, cert, envio, timeout_s)
        except Exception:  # noqa: BLE001 — cualquier fallo del SII deja el envío pendiente
            logger.exception("no se pudo consultar el envío %s", envio.track_id)
            pendientes.append(f"{envio.track_id} (no se pudo consultar al SII)")
            continue
        if envio.sii_state is None or envio.sii_state in _EN_PROCESO:
            pendientes.append(f"{envio.track_id} ({envio.sii_state or 'sin respuesta'})")
    return pendientes


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
        # Los documentos con reparo cuentan como entregados, igual que en
        # `entregado()`: el SII los registró y anotó una observación. Mirar sólo
        # `aceptados` pintaba de ROJO un set cuyos documentos el Servicio ya
        # tiene —el 5038180, con 3 reparos y 0 rechazos, salía "rechazado"— y
        # eso empuja a reemitir y gastar folios nuevos para volver a informar lo
        # mismo. El delator era el propio detalle: con 0 rechazados decía
        # "ninguno fue aceptado (0 rechazados)", que se contradice sola.
        if informados and not (aceptados + reparos):
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
    # Un set con reparos tiene respuesta del SII: no es "enviado, sin
    # respuesta", que es donde caía antes y hacía parecer que el Servicio no
    # había contestado. Tampoco es "aceptado" a secas: hay observaciones que
    # leer antes de declarar el avance.
    if por_clave.get("estado") == "atencion":
        return "con_reparos"
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

    El set de boletas queda fuera: se emite y se envía como los demás, pero su
    avance no se informa en el formulario «Declarar avance» de Mi SII, que tiene
    exactamente diez filas. Contarlo diría "de 11" y no cuadraría con lo que el
    operador está transcribiendo.
    """
    from app.services.certification_catalog import BY_KIND

    del_tramite = [s for s in sets if BY_KIND.get(s["kind"]) and BY_KIND[s["kind"]].declarable]
    return {
        "sets_total": len(del_tramite),
        "sets_declared": sum(1 for s in del_tramite if s["state"] == "declarado"),
        "sets_accepted": sum(
            1 for s in del_tramite if s["state"] in ("aceptado", "con_reparos", "declarado")
        ),
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

    El monto va **tal como lo declara el documento**, con sus decimales si los
    tiene. En el WSDL ``MontoDte`` es un string, no un entero, y el SII compara
    contra lo que registró. Forzarlo a entero —redondeando o truncando, se probó
    con ambos— daba DNK «Datos NO Coinciden» justo en los documentos con
    decimales, mientras que los de monto entero coincidían. Que la diferencia
    fuera exactamente ésa es lo que delató el criterio.

    Ojo con lo que significa ese DNK: habla de la consulta, no del documento. Un
    documento perfectamente aceptado responde DNK si se pregunta por él con otro
    monto.
    """

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
                "total_amount": bruto,
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


def emit(
    db, customer: Customer, cert, cert_set, *, force: bool = False, timeout_s: int = 60
) -> CertificationSubmission:
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

    if cert_set.kind == "simulacion":
        _regla_simulacion(db, customer, definicion.endpoint, definicion.payload or {})
    if definicion.endpoint == MIXED_ENDPOINT:
        xml = _emitir_mixto(db, customer, cert, cert_set, definicion.payload or {})
    else:
        xml = _emitir(
            db, customer, cert, cert_set, definicion.endpoint, definicion.payload, timeout_s
        )
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


#: Paso de simulación, según el formulario «Declarar avance» del SII (más
#: estricto que el manual de 2009, que decía mínimo 10): «Debe contener una
#: cantidad de 20 a 100 documentos (dentro del mismo envío)» y «todos los tipos
#: de documentos que está certificando».
SIMULATION_MIN_DOCS = 20
SIMULATION_MAX_DOCS = 100


def _emitir(
    db, customer: Customer, cert, cert_set, endpoint: str, payload, timeout_s: int
) -> bytes:
    """Emite un lote con su emisor y devuelve el sobre, sin enviarlo."""
    cuerpo, schema, funcion, con_db = _preparar(
        db, customer, cert, cert_set, endpoint, payload, timeout_s
    )
    # send=False siempre: en esta ruta emitir NO envía.
    req = schema.model_validate({**cuerpo, "send": False})
    resultado = funcion(db, customer, cert, req) if con_db else funcion(customer, cert, req)
    return base64.b64decode(resultado["xml_base64"])


def _preparar(db, customer: Customer, cert, cert_set, endpoint: str, payload, timeout_s: int):
    """La definición completada por el sistema, con el emisor que la procesa."""
    emisores = _emitters()
    if endpoint not in emisores:
        raise EmissionError(f"endpoint desconocido: {endpoint}")
    schema, funcion, con_db = emisores[endpoint]

    # Lo que depende del cliente o del día —emisor, fecha, referencia al caso,
    # período y líneas de los libros— lo pone el sistema, no la definición.
    from app.services import certification_fill

    if endpoint in certification_fill.DOC_ENDPOINTS:
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
    if cert_set.kind in certification_fill.GENERATED_BOOKS:
        pendientes = refresh_book_sources(db, customer, cert, cert_set, timeout_s)
        if pendientes:
            raise EmissionError(
                "hay envíos de los sets que alimentan este libro sin respuesta final del"
                " SII: " + ", ".join(pendientes) + ". Armarlo ahora tomaría el envío"
                " anterior y declararía folios que quizá no valen; espera la respuesta"
                " y vuelve a emitir"
            )
    cuerpo, notas = certification_fill.fill(db, customer, cert_set, endpoint, payload)
    if endpoint in certification_fill.BOOK_ENDPOINTS and not cuerpo.get("lines"):
        raise EmissionError(
            "el libro no tiene líneas: " + ("; ".join(notas) or "no hay documentos que declarar")
        )
    return cuerpo, schema, funcion, con_db


def _tipos_de_la_definicion(endpoint: str, payload: dict) -> list[int]:
    """Tipo de cada documento que emitiría la definición, en orden."""
    if endpoint == MIXED_ENDPOINT:
        return [
            t
            for grupo in payload.get("groups") or []
            for t in _tipos_de_la_definicion(grupo.get("endpoint", ""), grupo.get("payload") or {})
        ]
    docs = payload.get("documents") or []
    if endpoint == "issue-settlement-batch":
        return [43] * len(docs)
    return [int(d.get("type") or 0) for d in docs]


def certified_types(db, customer: Customer) -> set[int]:
    """Los tipos de documento que el contribuyente está certificando.

    Los de sus sets de pruebas (los del formulario «Declarar avance»). La
    boleta tiene su propio trámite y no entra.
    """
    from app.services.certification_catalog import BY_KIND

    kinds = {
        s.kind
        for s in db.query(CertificationSet).filter(CertificationSet.customer_id == customer.id)
    }
    return {
        t for k in kinds if k in BY_KIND and BY_KIND[k].declarable for t in BY_KIND[k].doc_types
    }


def _regla_simulacion(db, customer: Customer, endpoint: str, payload: dict) -> None:
    """No gasta folios en una simulación que el SII no aprobaría."""
    tipos = _tipos_de_la_definicion(endpoint, payload)
    if not SIMULATION_MIN_DOCS <= len(tipos) <= SIMULATION_MAX_DOCS:
        raise EmissionError(
            f"la simulación lleva {len(tipos)} documento(s): el SII pide entre"
            f" {SIMULATION_MIN_DOCS} y {SIMULATION_MAX_DOCS} en el mismo envío"
        )
    faltan = sorted(certified_types(db, customer) - set(tipos))
    if faltan:
        raise EmissionError(
            "la simulación debe contener todos los tipos de documento que se están"
            f" certificando; faltan: {', '.join(map(str, faltan))}"
        )


def _emitir_mixto(db, customer: Customer, cert, cert_set, payload: dict) -> bytes:
    """Emite varios lotes y los entrega en UN solo sobre.

    La simulación exige todos los tipos certificados «dentro del mismo envío»,
    pero la exportación y la liquidación-factura tienen su propio emisor. Cada
    grupo se emite con el suyo —asignación de folios, timbre y firma de siempre—
    y sus <DTE> ya firmados se reúnen en un <EnvioDTE> nuevo, firmado otra vez.
    La firma de un <DTE> no depende del sobre que lo contiene.

    Todos los grupos se validan antes de emitir el primero: un error en el
    último no debe dejar folios gastados en los anteriores.
    """
    from dte_chile.envelope import build_envelope
    from dte_chile.envelope import serialize as serialize_envelope
    from dte_chile.validation import Validator

    from app.core.config import get_settings
    from app.services.dte_service import _cover

    grupos = payload.get("groups") or []
    if not grupos:
        raise EmissionError("la definición mixta no trae grupos")

    preparados = []
    for n, grupo in enumerate(grupos, start=1):
        endpoint = grupo.get("endpoint", "")
        if endpoint not in {"issue-batch", "issue-export-batch", "issue-settlement-batch"}:
            raise EmissionError(f"grupo {n}: un sobre de documentos no admite «{endpoint}»")
        cuerpo, schema, funcion, _con_db = _preparar(
            db, customer, cert, cert_set, endpoint, grupo.get("payload") or {}, 0
        )
        try:
            req = schema.model_validate({**cuerpo, "send": False})
        except Exception as ex:  # noqa: BLE001 — se informa con el grupo
            raise EmissionError(f"grupo {n} ({endpoint}): {ex}") from ex
        preparados.append((funcion, req))

    dtes: list[etree._Element] = []
    for funcion, req in preparados:
        resultado = funcion(db, customer, cert, req)
        sobre = etree.fromstring(base64.b64decode(resultado["xml_base64"]))
        dtes.extend(copy.deepcopy(d) for d in sobre.iter() if _local(d.tag) == "DTE")

    tipos: dict[int, int] = {}
    for dte in dtes:
        tipo = int(next(n.text for n in dte.iter() if _local(n.tag) == "TipoDTE"))
        tipos[tipo] = tipos.get(tipo, 0) + 1
    ts = dt.datetime.now(ZoneInfo("America/Santiago")).replace(microsecond=0, tzinfo=None)
    cover = _cover(customer, cert, customer.rut, sorted(tipos.items()))
    xml = serialize_envelope(build_envelope(dtes, cover, cert, ts))
    Validator(get_settings().schemas_dir).validate(xml)
    return xml


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


def _firmas_como_se_transmiten(xml: bytes) -> None:
    """No sube un sobre cuyas firmas el SII no podría verificar.

    Se comprueba sobre los bytes, como lo hace el Servicio: corta cada <DTE> del
    texto y lo verifica suelto. El primer set de boletas salió con los <DTE> sin
    su ``xmlns`` —verificaban sobre el árbol— y el SII rechazó las cinco con
    «Firma DTE Incorrecta», con el plazo de 24 horas del CAF corriendo.

    Lo mismo con el timbre: el segundo envío firmó el DD con ``<RSR></RSR>`` y lo
    transmitió con ``<RSR/>``, y el SII puso reparo «Firma Timbre Electrónico
    Incorrecta» en las cinco.
    """
    from dte_chile.signer import verify_transmitted
    from dte_chile.ted import verify_stamps

    firmas = verify_transmitted(xml)
    if firmas and not all(firmas):
        malas = sum(not f for f in firmas)
        raise EmissionError(
            f"{malas} de {len(firmas)} firmas del sobre no verifican tal como se"
            " transmitirían; el SII lo rechazaría con «Firma DTE Incorrecta»."
            " No se envió. Vuelve a emitir el set."
        )
    timbres = verify_stamps(xml)
    if timbres and not all(timbres):
        malos = sum(not t for t in timbres)
        raise EmissionError(
            f"{malos} de {len(timbres)} timbres del sobre no verifican tal como se"
            " transmitirían; el SII lo objetaría con «Firma Timbre Electrónico"
            " Incorrecta». No se envió. Vuelve a emitir el set."
        )


def send_draft(db, customer: Customer, cert, envio: CertificationSubmission, timeout_s: int):
    """Sube al SII un sobre ya emitido y guarda su TrackID.

    Reenviar el mismo sobre no cuesta folios: es la razón de separar emitir de
    enviar.
    """
    from app.services import sii_upload

    xml = envelope(envio)
    _firmas_como_se_transmiten(xml)
    # Todo sobre de certificación —también el de boletas— sube por el upload de
    # Maullín. El correo del set de boletas lo dice: «Enviar al SII el Set de
    # Boletas generado y el RCOF asociado, vía UPLOAD, Web o automatizado, en
    # ambiente certificación». La API REST de boleta es el canal de producción:
    # el set enviado por ahí (TrackID 32169835) pasó sin reparos, pero la
    # revisión lo devolvió SRH con «El Documento no esta en el envio» en los
    # cinco casos. dte-sii, certificado, también lo sube por DTEUpload.
    # capture=False: la fila ya existe, sólo le falta el TrackID.
    resultado = sii_upload.upload(customer, cert, xml, customer.rut, timeout_s, capture=False)
    envio.track_id = str(resultado.track_id) if resultado.track_id else None
    envio.sent_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(envio)
    return envio


def folio_report(db, customer: Customer, cert, envio: CertificationSubmission):
    """El RCOF de un envío de boletas, como sobre emitido y sin enviar.

    El correo del set de boletas lo pide junto al set: «Enviar al SII el Set de
    Boletas generado y el Reporte de Consumo de Folios (RCOF) asociado», en un
    plazo de 24 horas, «puesto que se pretende verificar la capacidad de
    generación del RCOF». Asociado quiere decir que reporta exactamente esos
    folios y esos montos, así que se arma leyendo el sobre que se envió y no
    recalculando las boletas.

    Se envía por el upload de Maullín, no por la API de boleta: la especificación
    de la API dice que «palena.sii.cl es la plataforma dedicada para la recepción
    de DTE y RVD».
    """
    from dte_chile.folio_report import FolioReportCover, ReportLine, build_folio_report
    from dte_chile.folio_report import serialize as serialize_report
    from dte_chile.validation import Validator

    from app.core.config import get_settings

    if envio.envelope_kind != "EnvioBOLETA":
        raise EmissionError("el reporte de consumo de folios se arma desde un envío de boletas")
    if not envio.track_id:
        raise EmissionError("primero envía el set de boletas: el RCOF reporta lo que se envió")

    raiz = etree.fromstring(envelope(envio))
    lineas, fechas = [], set()
    for doc in raiz.iter():
        if _local(doc.tag) != "Documento":
            continue
        datos = {_local(n.tag): (n.text or "").strip() for n in doc.iter() if len(n) == 0}
        fechas.add(dt.date.fromisoformat(datos["FchEmis"]))
        lineas.append(
            ReportLine(
                doc_type=int(datos["TipoDTE"]),
                folio=int(datos["Folio"]),
                net_amount=int(datos.get("MntNeto") or 0),
                vat_amount=int(datos.get("IVA") or 0),
                exempt_amount=int(datos.get("MntExe") or 0),
                total_amount=int(datos.get("MntTotal") or 0),
            )
        )
    if not lineas:
        raise EmissionError("el sobre de boletas no trae documentos")

    previos = (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.set_id == envio.set_id,
            CertificationSubmission.envelope_kind == "ConsumoFolios",
        )
        .count()
    )
    ahora = dt.datetime.now(ZoneInfo("America/Santiago")).replace(microsecond=0, tzinfo=None)
    cover = FolioReportCover(
        issuer_rut=customer.rut,
        sender_rut=cert.rut or customer.rut,
        start_date=min(fechas),
        end_date=max(fechas),
        sequence=previos + 1,
        resolution_date=customer.resolution_date,
        resolution_number=customer.resolution_number,
        lines=lineas,
    )
    xml = serialize_report(build_folio_report(cover, cert, ahora))
    Validator(get_settings().schemas_dir).validate(xml)

    rcof = CertificationSubmission(
        set_id=envio.set_id,
        customer_id=customer.id,
        track_id=None,
        sent_at=None,
        envelope_kind="ConsumoFolios",
        envelope_encrypted=crypto.encrypt(xml),
        signed_thumbprint=_thumbprint(db, customer),
    )
    db.add(rcof)
    db.flush()
    for doc_type, folio in sorted((ln.doc_type, ln.folio) for ln in lineas):
        db.add(CertificationDocument(submission_id=rcof.id, doc_type=doc_type, folio=folio))
    db.commit()
    db.refresh(rcof)
    return rcof


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
