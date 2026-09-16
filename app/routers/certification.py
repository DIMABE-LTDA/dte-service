"""Expediente de certificación: llevar el trámite del SII desde el portal.

Emite los sets, los envía, consulta su estado y guarda cada sobre con su
TrackID. La regla de no timbrar desde el navegador se levantó sólo aquí y sólo
en certificación: son documentos de prueba contra Maullín, y hacerlo por
scripts era justo lo que dejaba la evidencia dispersa.

Casi todo cuelga de ``/admin/customers/{id}/certification`` y exige que el
cliente sea de ambiente **certificación**. Un cliente de producción no tiene
expediente, y dejar la puerta abierta invitaría a usarlo donde no corresponde.

El índice ``/admin/certification`` es la excepción: no cuelga de un cliente
porque su razón de ser es justamente listarlos a todos.
"""

from __future__ import annotations

import base64
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    CertificationDefinition,
    CertificationNote,
    CertificationSet,
    CertificationSubmission,
    Customer,
    SiiEnvironment,
    User,
)
from app.db.session import get_db
from app.schemas.certification import (
    AssignSetRequest,
    CauseOut,
    CertificationCustomerOut,
    CertificationDossierOut,
    CertificationSetOut,
    CertificationSubmissionOut,
    CheckOut,
    CloneRequest,
    ContentsOut,
    DeclareRequest,
    DefinitionOut,
    DefinitionRequest,
    DocStatsOut,
    DocumentStatusOut,
    EnvelopeOut,
    ImportRequest,
    NoteOut,
    NoteRequest,
    PreviewOut,
    PrintSamplesOut,
    ReadinessOut,
    ReceiverOut,
    ReceiversRequest,
    SetupRequest,
    StepRequest,
    TemplateRequest,
    TemplateSetOut,
)
from app.security.auth import admin_access, admin_read_access
from app.services import (
    audit_service,
    certificate_service,
    certification_causes,
    certification_checks,
    certification_fill,
    certification_preview,
    certification_service,
    certification_template,
)

router = APIRouter(prefix="/admin/customers/{customer_id}/certification", tags=["Certificación"])

#: El índice va aparte porque no cuelga de un cliente: los lista.
index_router = APIRouter(prefix="/admin/certification", tags=["Certificación"])


@index_router.get("", response_model=list[CertificationCustomerOut])
def index(
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[CertificationCustomerOut]:
    """Los contribuyentes en certificación con su avance.

    Es la portada del módulo. Sin ella, llegar al expediente exige saber de
    memoria en qué ficha está, y quien lleva varias certificaciones a la vez no
    tiene forma de ver cuál se quedó atrás.
    """
    clientes = (
        db.query(Customer)
        .filter(
            Customer.environment == SiiEnvironment.CERTIFICATION,
            Customer.deleted_at.is_(None),
        )
        .order_by(Customer.name)
        .all()
    )
    salida = []
    for cliente in clientes:
        sets = certification_service.expected_sets(db, cliente)
        ultimo = (
            db.query(CertificationSubmission.sent_at)
            .filter(
                CertificationSubmission.customer_id == cliente.id,
                CertificationSubmission.sent_at.isnot(None),
            )
            .order_by(CertificationSubmission.sent_at.desc())
            .first()
        )
        salida.append(
            CertificationCustomerOut(
                customer_id=cliente.id,
                name=cliente.name,
                rut=cliente.rut or "",
                key=cliente.key,
                progress=certification_service.progress(sets),
                last_activity=ultimo[0] if ultimo else None,
            )
        )
    return salida


def _envio(row: CertificationSubmission) -> CertificationSubmissionOut:
    """Un envío con la guía de su respuesta, si el código es conocido."""
    salida = CertificationSubmissionOut.model_validate(row)
    salida.stats = [DocStatsOut(**f) for f in (row.sii_stats or [])]
    causa = certification_causes.for_state(row.sii_state)
    if causa is not None:
        # `ok` sale del catálogo, pero el desglose manda: EPR está catalogado
        # como aceptado y puede traer todo su contenido rechazado dentro.
        entregado = certification_service.entregado(row)
        salida.cause = CauseOut(
            label=causa.label,
            meaning=causa.meaning,
            usually=causa.usually,
            check=list(causa.check),
            ok=causa.ok and entregado,
        )
    return salida


def _cert(db: Session, customer: Customer):
    """El certificado del cliente, o un error que se pueda leer.

    Un .pfx ilegible —corrupto, o con la contraseña equivocada— reventaba con un
    500: el operador veía "HTTP 500" sin saber que el problema estaba en el
    certificado que él mismo cargó.
    """
    try:
        cert = certificate_service.resolve_certificate(db, customer)
    except Exception as ex:
        raise HTTPException(
            status_code=409,
            detail="el certificado del cliente no se pudo abrir: revisa el archivo"
            " .pfx y su contraseña en la ficha",
        ) from ex
    if cert is None:
        raise HTTPException(status_code=409, detail="el cliente no tiene certificado cargado")
    return cert


def _customer(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="cliente no encontrado")
    if customer.environment != SiiEnvironment.CERTIFICATION:
        raise HTTPException(
            status_code=400,
            detail="el expediente de certificación sólo existe en clientes de ambiente"
            " CERTIFICATION",
        )
    return customer


def _submission(db: Session, customer: Customer, submission_id: int) -> CertificationSubmission:
    row = db.get(CertificationSubmission, submission_id)
    # Se comprueba la pertenencia además de la existencia: sin esto, el id de un
    # envío de otro cliente sería suficiente para leer su sobre.
    if row is None or row.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="envío no encontrado")
    return row


def _set(db: Session, customer: Customer, set_id: int) -> CertificationSet:
    row = db.get(CertificationSet, set_id)
    if row is None or row.customer_id != customer.id:
        raise HTTPException(status_code=404, detail="set no encontrado")
    return row


@router.get("", response_model=CertificationDossierOut)
def dossier(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """El expediente entero: los sets con sus etapas y los envíos sin clasificar."""
    customer = _customer(db, customer_id)
    # Salen los diez del catálogo, existan o no: el que falta es el que importa.
    crudos = certification_service.expected_sets(db, customer)
    salida = [
        CertificationSetOut(
            id=c["id"],
            code=c["code"],
            kind=c["kind"],
            state=c["state"],
            declared_at=c["declared_at"],
            stages=c["stages"],
            submissions=[_envio(s) for s in c["submissions"]],
        )
        for c in crudos
    ]
    sueltos = (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.customer_id == customer.id,
            CertificationSubmission.set_id.is_(None),
        )
        .order_by(CertificationSubmission.id)
        .all()
    )
    return CertificationDossierOut(
        customer_id=customer.id,
        progress=certification_service.progress(crudos),
        steps=certification_service.steps(db, customer, crudos),
        sets=salida,
        unassigned=[_envio(s) for s in sueltos],
    )


@router.get("/checks", response_model=ReadinessOut)
def readiness(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> ReadinessOut:
    """Verifica que el cliente esté listo para emitir sus sets.

    Todo local: no gasta folios ni habla con el SII. Existe para que un
    certificado autofirmado, un CAF sin firma del Servicio o un timbre inválido
    se vean ANTES de enviar, y no como 32 documentos rechazados una semana
    después.
    """
    customer = _customer(db, customer_id)
    return ReadinessOut(**certification_checks.run(db, customer))


@router.post("/checks/sii", response_model=CheckOut)
def readiness_sii(
    customer_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CheckOut:
    """Pide un token a Maullín con el certificado del cliente.

    Es la única comprobación que sale a la red, así que va aparte y la dispara
    una persona.
    """
    customer = _customer(db, customer_id)
    cert = _cert(db, customer)
    return CheckOut(
        **certification_checks.sii_auth(customer, cert, get_settings().request_timeout_s)
    )


@router.post("/submissions/{submission_id}/refresh", response_model=CertificationSubmissionOut)
def refresh_status(
    customer_id: int,
    submission_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationSubmission:
    """Consulta el estado del TrackID en el SII y guarda la respuesta cruda.

    El estado lo dice el Servicio, no se deduce del envío: que la subida haya
    devuelto 200 sólo significa que el sobre se recibió.
    """
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    if not row.track_id:
        # Un sobre emitido y sin enviar no tiene TrackID: no hay nada que
        # preguntarle al SII todavía.
        raise HTTPException(
            status_code=409,
            detail="este sobre todavía no se envió: no tiene TrackID que consultar",
        )
    cert = _cert(db, customer)
    return certification_service.refresh(db, customer, cert, row, get_settings().request_timeout_s)


@router.post("/submissions/{submission_id}/assign", response_model=CertificationSubmissionOut)
def assign_set(
    customer_id: int,
    submission_id: int,
    data: AssignSetRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationSubmission:
    """Atribuye un envío suelto a su set del SII, creándolo si hace falta."""
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    cert_set = certification_service.find_or_create_set(db, customer.id, data.code.strip())
    if data.kind:
        cert_set.kind = data.kind
    row.set_id = cert_set.id
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.assign",
        "certification_submission",
        str(row.id),
        f"track {row.track_id} → set {cert_set.code}",
    )
    db.refresh(row)
    return row


@router.post("/sets/{set_id}/declare", response_model=CertificationSetOut)
def declare(
    customer_id: int,
    set_id: int,
    data: DeclareRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationSetOut:
    """Marca que el avance del set se declaró en Mi SII.

    Es manual porque el SII no tiene API para declararlo. Queda en la auditoría
    de cambios: es la afirmación de que un trámite externo se hizo.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    cert_set.declared_at = dt.datetime.combine(data.declared_at, dt.time.min)
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.declare",
        "certification_set",
        str(cert_set.id),
        f"set {cert_set.code} declarado el {data.declared_at:%d-%m-%Y}",
    )
    db.refresh(cert_set)
    etapas = certification_service.stages(db, customer, cert_set)
    return CertificationSetOut(
        id=cert_set.id,
        code=cert_set.code,
        kind=cert_set.kind,
        state=certification_service.set_state(etapas),
        declared_at=cert_set.declared_at,
        stages=etapas,
        submissions=[_envio(s) for s in sorted(cert_set.submissions, key=lambda x: x.id)],
    )


@router.get("/submissions/{submission_id}/envelope", response_model=EnvelopeOut)
def envelope(
    customer_id: int,
    submission_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> EnvelopeOut:
    """El sobre tal como se subió. Alimenta las muestras de impresión."""
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    xml = certification_service.envelope(row)
    return EnvelopeOut(
        submission_id=row.id,
        track_id=row.track_id,
        # Un sobre emitido y sin enviar no tiene TrackID todavía: sin este
        # respaldo se descargaba como «EnvioDTE_None.xml», y dos sobres sin
        # enviar chocaban con el mismo nombre en la carpeta de descargas.
        filename=f"{row.envelope_kind}_{row.track_id or f'sin-enviar-{row.id}'}.xml",
        xml_base64=base64.b64encode(xml).decode("ascii"),
    )


@router.get("/notes", response_model=list[NoteOut])
def list_notes(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[CertificationNote]:
    customer = _customer(db, customer_id)
    return (
        db.query(CertificationNote)
        .join(CertificationSet)
        .filter(CertificationSet.customer_id == customer.id)
        .order_by(CertificationNote.created_at.desc())
        .all()
    )


@router.post("/notes", response_model=NoteOut)
def add_note(
    customer_id: int,
    data: NoteRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationNote:
    """Anota qué se probó y qué se descartó.

    El Libro de Ventas llevó trece intentos; lo que evitó repetirlos fue tener
    escrito lo ya descartado, junto al set y no en un documento aparte.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, data.set_id)
    row = CertificationNote(
        set_id=cert_set.id,
        author=actor.email if actor else "máquina",
        text=data.text.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/setup", response_model=CertificationDossierOut)
def setup(
    customer_id: int,
    data: SetupRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """Da de alta los sets que el SII asignó a este contribuyente.

    Se hace una vez por cliente, copiando de Mi SII el número de atención de
    cada set. A partir de ahí el expediente sabe qué falta, y los envíos que
    lleguen con la cabecera ``X-Certification-Set`` caen solos en su sitio.
    """
    customer = _customer(db, customer_id)
    for kind, code in data.codes.items():
        code = (code or "").strip()
        if not code:
            continue  # sin número, el set sigue apareciendo como pendiente
        cert_set = certification_service.find_or_create_set(db, customer.id, code)
        cert_set.kind = kind
        if not cert_set.submissions:
            cert_set.state = "pendiente"
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.setup",
        "customer",
        str(customer.id),
        f"{sum(1 for v in data.codes.values() if v.strip())} sets dados de alta",
    )
    return dossier(customer_id, actor, db)


@router.post("/steps/{step}", response_model=CertificationDossierOut)
def set_step(
    customer_id: int,
    step: str,
    data: StepRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """Marca (o desmarca) un paso del trámite que ocurre fuera del servicio."""
    from app.db.models import CertificationMilestone
    from app.services.certification_catalog import STEP_KEYS

    customer = _customer(db, customer_id)
    if step not in STEP_KEYS or step == "sets":
        # El paso de los sets lo deduce el sistema: marcarlo a mano lo haría
        # mentir, que es lo único que un semáforo no puede permitirse.
        raise HTTPException(status_code=400, detail=f"paso no marcable: {step}")
    hito = (
        db.query(CertificationMilestone)
        .filter(
            CertificationMilestone.customer_id == customer.id,
            CertificationMilestone.step == step,
        )
        .one_or_none()
    )
    if hito is None:
        hito = CertificationMilestone(customer_id=customer.id, step=step)
        db.add(hito)
    hito.done_at = data.done_at
    hito.note = data.note.strip()
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.step",
        "customer",
        str(customer.id),
        f"paso {step}: {f'cumplido el {data.done_at}' if data.done_at else 'pendiente'}",
    )
    return dossier(customer_id, actor, db)


@router.put("/sets/{set_id}/definition", response_model=DefinitionOut)
def save_definition(
    customer_id: int,
    set_id: int,
    data: DefinitionRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDefinition:
    """Guarda qué hay que emitir para este set.

    El cuerpo se guarda literal, tal como lo espera el endpoint de emisión: lo
    que se revisa aquí es exactamente lo que se enviará.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    row = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == cert_set.id)
        .one_or_none()
    )
    if row is None:
        row = CertificationDefinition(set_id=cert_set.id)
        db.add(row)
    row.endpoint = data.endpoint
    # Sólo los datos del caso: emisor, fechas, referencia al caso, período y
    # líneas generadas los pone el sistema al emitir.
    row.payload = certification_fill.strip(data.endpoint, cert_set.kind or "", data.payload)
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.definition",
        "certification_set",
        str(cert_set.id),
        f"definición del set {cert_set.code} ({data.endpoint})",
    )
    db.refresh(row)
    return row


@router.post("/sets/{set_id}/definition/clone", response_model=DefinitionOut)
def clone_definition(
    customer_id: int,
    set_id: int,
    data: CloneRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDefinition:
    """Copia la definición del mismo tipo de set desde otro cliente.

    Es lo que hace barato el segundo contribuyente: partir de un set que el SII
    ya aceptó y ajustar, en vez de transcribir códigos de Aduana desde cero.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    if not cert_set.kind:
        raise HTTPException(
            status_code=400,
            detail="el set no tiene tipo asignado, así que no se sabe de cuál copiar",
        )
    origen = (
        db.query(CertificationDefinition)
        .join(CertificationSet)
        .filter(
            CertificationSet.customer_id == data.from_customer_id,
            CertificationSet.kind == cert_set.kind,
        )
        .one_or_none()
    )
    if origen is None:
        raise HTTPException(
            status_code=404,
            detail=f"ese cliente no tiene definido un set de tipo {cert_set.kind}",
        )
    row = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == cert_set.id)
        .one_or_none()
    )
    if row is None:
        row = CertificationDefinition(set_id=cert_set.id)
        db.add(row)
    row.endpoint = origen.endpoint
    # Del otro cliente se copian sólo los datos del caso: su emisor, sus fechas
    # y su número de atención no tienen nada que hacer aquí.
    row.payload = certification_fill.strip(row.endpoint, cert_set.kind or "", origen.payload)
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.clone",
        "certification_set",
        str(cert_set.id),
        f"definición copiada del cliente {data.from_customer_id}",
    )
    db.refresh(row)
    return row


@router.get("/sets/{set_id}/definition", response_model=DefinitionOut)
def get_definition(
    customer_id: int,
    set_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    row = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == cert_set.id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="este set todavía no tiene definición")
    # Se muestra sólo lo que es del caso. Una definición guardada antes de que
    # el sistema completara emisor y fechas todavía los trae, y enseñarlos haría
    # creer que se usan: el sistema los reemplaza al emitir.
    return DefinitionOut(
        set_id=row.set_id,
        endpoint=row.endpoint,
        payload=certification_fill.strip(row.endpoint, cert_set.kind or "", row.payload),
        updated_at=row.updated_at,
    )


@router.get("/receivers", response_model=list[ReceiverOut])
def list_receivers(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[ReceiverOut]:
    """Los clientes reales del contribuyente que reciben los documentos del set.

    El instructivo del SII pide «un Rut receptor de un cliente existente» y «RUT
    distintos para las distintas facturas».
    """
    customer = _customer(db, customer_id)
    return [ReceiverOut(**r) for r in (customer.cert_receivers or [])]


@router.put("/receivers", response_model=list[ReceiverOut])
def save_receivers(
    customer_id: int,
    data: ReceiversRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> list[ReceiverOut]:
    """Reemplaza la lista entera: es corta y así no hay estados intermedios."""
    customer = _customer(db, customer_id)
    customer.cert_receivers = [r.model_dump() for r in data.receivers]
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.receivers",
        "customer",
        str(customer.id),
        f"{len(data.receivers)} receptor(es) de prueba",
    )
    db.commit()
    return [ReceiverOut(**r) for r in customer.cert_receivers]


@router.post("/sets/{set_id}/emit", response_model=CertificationSubmissionOut)
def emit_set(
    customer_id: int,
    set_id: int,
    force: bool = False,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationSubmissionOut:
    """Emite el set **sin enviarlo**: quema folios y deja el sobre para revisar.

    Emitir y enviar son dos actos distintos a propósito. Si el SII rechaza, se
    corrige y se reenvía el mismo sobre sin gastar folios nuevos.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    cert = _cert(db, customer)
    try:
        envio = certification_service.emit(
            db, customer, cert, cert_set, force=force, timeout_s=get_settings().request_timeout_s
        )
    except certification_service.EmissionError as ex:
        raise HTTPException(status_code=409, detail=str(ex)) from ex
    except ValueError as ex:
        # El timbre se firma con la clave privada que viene DENTRO del CAF, no
        # con el certificado. Si ese bloque está corrupto, cryptography levanta
        # un ValueError genérico y el operador veía "HTTP 500", sin ninguna
        # pista de que el problema era un archivo que él mismo cargó.
        if "deserialize key" not in str(ex) and "PEM" not in str(ex):
            raise
        raise HTTPException(
            status_code=409,
            detail="el CAF de alguno de los tipos de documento de este set tiene la"
            " clave privada ilegible: vuelve a descargarlo desde el SII y súbelo de"
            " nuevo en la ficha del cliente",
        ) from ex
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.emit",
        "certification_set",
        str(cert_set.id),
        f"set {cert_set.code}: {len(envio.documents)} documento(s) emitidos",
    )
    return _envio(envio)


@router.post("/submissions/{submission_id}/documents", response_model=list[DocumentStatusOut])
def document_statuses(
    customer_id: int,
    submission_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> list[DocumentStatusOut]:
    """Pregunta al SII cómo quedó cada documento del sobre, con su glosa.

    El desglose del TrackID dice cuántos con reparo, no cuál ni por qué. Sin
    esto, averiguarlo obligaba a pedirle al SII que mandara el detalle por
    correo y esperar a que llegara.
    """
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    cert = _cert(db, customer)
    try:
        filas = certification_service.document_statuses(
            db, customer, cert, row, get_settings().request_timeout_s
        )
    except certification_service.EmissionError as ex:
        raise HTTPException(status_code=409, detail=str(ex)) from ex
    return [DocumentStatusOut(**f) for f in filas]


@router.delete("/submissions/{submission_id}", response_model=CertificationDossierOut)
def discard_submission(
    customer_id: int,
    submission_id: int,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """Descarta un sobre emitido que nunca se envió.

    Sin esto, corregir la definición de un set obligaba a emitir "de nuevo"
    arrastrando el sobre viejo, o a dejarlo ahí bloqueando el botón. Un sobre
    que el SII nunca vio no es un registro de nada.
    """
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    try:
        certification_service.discard(db, row)
    except certification_service.EmissionError as ex:
        raise HTTPException(status_code=409, detail=str(ex)) from ex
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.discard",
        "certification_submission",
        str(submission_id),
        "sobre emitido y sin enviar, descartado",
    )
    return dossier(customer_id, actor, db)


@router.post("/submissions/{submission_id}/send", response_model=CertificationSubmissionOut)
def send_submission(
    customer_id: int,
    submission_id: int,
    force: bool = False,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationSubmissionOut:
    """Sube al SII un sobre ya emitido. Reenviar no cuesta folios."""
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    cert = _cert(db, customer)
    # La firma va DENTRO del sobre: cambiar el certificado del cliente no la
    # rehace. Enviar un sobre firmado con el anterior gasta un TrackID para
    # nada y vuelve como RFR «error en firma», que es de los rechazos más caros
    # de diagnosticar porque su causa habitual es otra.
    if not force and certification_service.stale_signature(db, customer, row):
        raise HTTPException(
            status_code=409,
            detail="este sobre se firmó con un certificado que ya no es el vigente."
            " La firma viaja dentro del XML, así que enviarlo ahora lo mandaría"
            " firmado por el certificado anterior y el SII lo rechazaría con RFR."
            " Vuelve a emitir el set para que se firme con el certificado actual.",
        )
    envio = certification_service.send_draft(
        db, customer, cert, row, get_settings().request_timeout_s
    )
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.send",
        "certification_submission",
        str(envio.id),
        f"enviado con TrackID {envio.track_id}",
    )
    return _envio(envio)


@router.post("/print-samples", response_model=PrintSamplesOut)
def print_samples(
    customer_id: int,
    sii_office: str = "SANTIAGO",
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> PrintSamplesOut:
    """Impresos de todos los documentos enviados, para el paso 5 del trámite.

    Se arma desde los sobres guardados. Antes esto no se podía hacer: el
    servicio no almacena DTE y de la tanda aceptada se habían perdido seis
    sobres, que son justo los que el SII pide imprimir.
    """
    customer = _customer(db, customer_id)
    resultado = certification_service.print_samples(db, customer, sii_office)
    return PrintSamplesOut(**resultado)


@router.get("/sets/{set_id}/preview", response_model=PreviewOut)
def preview(
    customer_id: int,
    set_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> PreviewOut:
    """Qué se emitirá si se pulsa Emitir, legible sin abrir el JSON.

    Emitir consume folios y no se deshace: revisar tiene que ser posible sin
    leer XML.
    """
    customer = _customer(db, customer_id)
    cert_set = _set(db, customer, set_id)
    definicion = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == cert_set.id)
        .one_or_none()
    )
    if definicion is None:
        raise HTTPException(status_code=404, detail="este set todavía no tiene definición")
    # La previa se hace sobre lo que de verdad se va a emitir: la definición
    # COMPLETADA por el sistema, no la guardada.
    cuerpo, notas = certification_fill.fill(
        db, customer, cert_set, definicion.endpoint, definicion.payload
    )
    salida = certification_preview.definition(definicion.endpoint, cuerpo)
    salida["system_notes"] = notas
    return PreviewOut(**salida)


@router.get("/submissions/{submission_id}/contents", response_model=ContentsOut)
def contents(
    customer_id: int,
    submission_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> ContentsOut:
    """Qué contiene de verdad el sobre: folio y totales del XML firmado."""
    customer = _customer(db, customer_id)
    row = _submission(db, customer, submission_id)
    return ContentsOut(
        submission_id=row.id,
        track_id=row.track_id,
        documents=certification_preview.envelope(certification_service.envelope(row)),
    )


def _apply_sets(db: Session, customer: Customer, sets: dict[str, dict]) -> int:
    """Da de alta los sets con su definición. Devuelve cuántos quedaron."""
    cargados = 0
    for kind, entrada in sets.items():
        from app.services.certification_catalog import BY_KIND

        code = str(entrada.get("code") or "").strip()
        tipo = BY_KIND.get(kind)
        if code:
            cert_set = certification_service.find_or_create_set(db, customer.id, code)
        elif tipo is not None and not tipo.declarable:
            # El de boletas no lleva número de atención: el SII no lo numera.
            # Se identifica por su tipo.
            cert_set = certification_service.find_or_create_set_by_kind(db, customer.id, kind)
        else:
            continue  # sin número de atención el set no se puede identificar
        cert_set.kind = kind
        row = (
            db.query(CertificationDefinition)
            .filter(CertificationDefinition.set_id == cert_set.id)
            .one_or_none()
        )
        if row is None:
            row = CertificationDefinition(set_id=cert_set.id)
            db.add(row)
        row.endpoint = entrada["endpoint"]
        row.payload = certification_fill.strip(entrada["endpoint"], kind, entrada["payload"])
        cargados += 1
    return cargados


@router.post("/import", response_model=CertificationDossierOut)
def import_definitions(
    customer_id: int,
    data: ImportRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """Carga en bloque los sets de un contribuyente con su definición.

    Da de alta el set si hace falta y guarda qué emitir, en una sola operación:
    es como se pone en marcha una certificación nueva.
    """
    customer = _customer(db, customer_id)
    cargados = _apply_sets(db, customer, data.sets)
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.import",
        "customer",
        str(customer.id),
        f"{cargados} set(s) cargados con su definición",
    )
    return dossier(customer_id, actor, db)


@router.get("/template", response_model=list[TemplateSetOut])
def template(
    customer_id: int,
    actor: User | None = Depends(admin_read_access),
    db: Session = Depends(get_db),
) -> list[TemplateSetOut]:
    """Los diez sets que define el SII, con qué hay que transcribir de cada uno.

    Es el catálogo que necesita la pantalla para pedir los números de atención:
    sin él, empezar una certificación exigía conocer de memoria los diez sets.
    """
    _customer(db, customer_id)
    return [
        TemplateSetOut(
            kind=s["kind"],
            label=s["label"],
            endpoint=s["endpoint"],
            help=s["help"],
            transcribe=s["kind"] not in certification_fill.GENERATED_BOOKS,
        )
        for s in certification_template.SETS
    ]


@router.post("/template", response_model=CertificationDossierOut)
def create_from_template(
    customer_id: int,
    data: TemplateRequest,
    actor: User | None = Depends(admin_access),
    db: Session = Depends(get_db),
) -> CertificationDossierOut:
    """Crea los sets desde la plantilla, con los números de atención del PDF.

    Deja el expediente con la estructura correcta —qué documentos, en qué orden
    y qué referencia a qué— y montos de relleno que el operador reemplaza con
    los de su set. Antes de esto, un contribuyente nuevo obligaba a clonar el
    expediente de otro o a pegar el JSON a mano.
    """
    customer = _customer(db, customer_id)
    sets = certification_template.sets_for_import(data.codes)
    if not sets:
        raise HTTPException(
            status_code=400,
            detail="Indica al menos un número de atención: es lo que identifica cada set.",
        )
    cargados = _apply_sets(db, customer, sets)
    audit_service.record_change(
        db,
        actor.id if actor else None,
        "certification.template",
        "customer",
        str(customer.id),
        f"{cargados} set(s) creados desde la plantilla",
    )
    return dossier(customer_id, actor, db)
