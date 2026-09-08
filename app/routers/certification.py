"""Expediente de certificación: consulta y seguimiento desde el portal.

Sólo de lectura y anotación. **No emite ni envía nada**: eso sigue haciéndose
con los scripts contra el API de emisión, y así el portal mantiene su regla de
no timbrar documentos desde el navegador.

Todo cuelga de ``/admin/customers/{id}/certification`` y exige que el cliente
sea de ambiente **certificación**. Un cliente de producción no tiene expediente
que mirar, y dejar la puerta abierta invitaría a usarlo donde no corresponde.
"""

from __future__ import annotations

import base64
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
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
    CertificationDossierOut,
    CertificationSetOut,
    CertificationSubmissionOut,
    DeclareRequest,
    EnvelopeOut,
    NoteOut,
    NoteRequest,
    SetupRequest,
    StepRequest,
)
from app.security.auth import admin_access, admin_read_access
from app.services import audit_service, certificate_service, certification_service

router = APIRouter(prefix="/admin/customers/{customer_id}/certification", tags=["Certificación"])


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
            submissions=[CertificationSubmissionOut.model_validate(s) for s in c["submissions"]],
        )
        for c in crudos
    ]
    sueltos = (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.customer_id == customer.id,
            CertificationSubmission.set_id.is_(None),
        )
        .order_by(CertificationSubmission.sent_at)
        .all()
    )
    return CertificationDossierOut(
        customer_id=customer.id,
        progress=certification_service.progress(crudos),
        steps=certification_service.steps(db, customer, crudos),
        sets=salida,
        unassigned=[CertificationSubmissionOut.model_validate(s) for s in sueltos],
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
    cert = certificate_service.resolve_certificate(db, customer)
    if cert is None:
        raise HTTPException(status_code=409, detail="el cliente no tiene certificado cargado")
    estado = certification_service.query_status(
        customer, cert, row.track_id, get_settings().request_timeout_s
    )
    row.sii_state = estado.get("state")
    row.sii_detail = estado.get("detail")
    row.checked_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(row)
    return row


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
        submissions=[
            CertificationSubmissionOut.model_validate(s)
            for s in sorted(cert_set.submissions, key=lambda x: x.sent_at)
        ],
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
        filename=f"{row.envelope_kind}_{row.track_id}.xml",
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
