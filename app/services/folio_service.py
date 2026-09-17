"""Asignador de folios en BD (HA, anti-duplicación).

La fila ``FolioPointer(customer, doc_type)`` se bloquea con ``SELECT ... FOR
UPDATE``; así dos workers/hosts no entregan el mismo folio. El CAF (con la llave
de timbre) se carga descifrado en memoria vía ``load_caf_bytes``.
"""

from __future__ import annotations

import datetime as dt

from dte_chile import FolioError, FoliosExhausted
from dte_chile.caf import CAF, CERTIFICATION_IDK, load_caf_bytes
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import crypto
from app.db.models import Caf, Customer, FolioAssignment, FolioPointer, SiiEnvironment


def ensure_pointer(db: Session, customer_id: int, doc_type: int, *, commit: bool = True) -> None:
    """Crea el puntero (last_folio=0) si no existe. Llamar al subir un CAF."""
    exists = db.get(FolioPointer, (customer_id, doc_type))
    if exists is None:
        db.add(FolioPointer(customer_id=customer_id, doc_type=doc_type, last_folio=0))
        if commit:
            db.commit()
        else:
            db.flush()


def next_folio(
    db: Session, customer_id: int, doc_type: int, request_id: str = "-"
) -> tuple[int, CAF]:
    """Asigna el siguiente folio disponible y devuelve (folio, CAF).

    Registra la asignación en ``FolioAssignment`` (mismo commit que avanza el
    puntero) para trazar qué request consumió cada folio.

    No entrega folios que el SII va a rechazar: de un CAF vencido (Res. Ex. SII
    N° 58/2017) o de otro ambiente (un CAF de certificación en un cliente de
    producción, o al revés). Esos CAF se saltan; si no queda ninguno usable, el
    error dice cuál es el problema en vez de «folios agotados».
    """
    ptr = db.execute(
        select(FolioPointer)
        .where(FolioPointer.customer_id == customer_id, FolioPointer.doc_type == doc_type)
        .with_for_update()
    ).scalar_one_or_none()
    if ptr is None:
        raise FolioError(f"No hay CAF cargado para el tipo {doc_type}.")

    customer = db.get(Customer, customer_id)
    certification = customer is not None and customer.environment == SiiEnvironment.CERTIFICATION
    today = dt.date.today()
    target = ptr.last_folio + 1
    candidates = (
        db.query(Caf)
        .filter(
            Caf.customer_id == customer_id,
            Caf.doc_type == doc_type,
            Caf.exhausted.is_(False),
            Caf.folio_to >= target,
        )
        .order_by(Caf.folio_from)
        .all()
    )
    skipped: list[str] = []
    for caf_row in candidates:
        caf = load_caf_bytes(crypto.decrypt(caf_row.xml_encrypted))
        _backfill(caf_row, caf)
        if caf.key_id is not None and caf.is_certification != certification:
            skipped.append(
                f"CAF {caf_row.folio_from}-{caf_row.folio_to} es de "
                f"{'certificación' if caf.is_certification else 'producción'}"
            )
            continue
        if caf.is_expired(today):
            skipped.append(
                f"CAF {caf_row.folio_from}-{caf_row.folio_to} venció el {caf.expires_on:%d-%m-%Y}"
            )
            continue
        break
    else:
        db.commit()  # guarda lo completado en _backfill y libera el bloqueo
        if skipped:
            raise FoliosExhausted(
                f"No hay folios utilizables para tipo {doc_type}: " + "; ".join(skipped) + "."
            )
        raise FoliosExhausted(f"Folios agotados para tipo {doc_type} (último: {ptr.last_folio}).")

    folio = max(target, caf_row.folio_from)  # salta huecos y CAF no utilizables
    ptr.last_folio = folio
    if folio >= caf_row.folio_to:
        caf_row.exhausted = True
    db.add(
        FolioAssignment(
            customer_id=customer_id, doc_type=doc_type, folio=folio, request_id=request_id
        )
    )
    db.commit()
    return folio, caf


def _backfill(row: Caf, caf: CAF) -> None:
    """Completa fecha, vencimiento e IDK en CAF cargados antes de guardarlos."""
    if row.key_id is None and caf.key_id is not None:
        row.key_id = caf.key_id
    if row.authorized_on is None and caf.authorized_on is not None:
        row.authorized_on = caf.authorized_on
        row.expires_on = caf.expires_on


def mark_assignment(db: Session, customer_id: int, doc_type: int, folio: int, status: str) -> None:
    """Actualiza el desenlace de un folio asignado (``issued`` / ``failed``)."""
    row = db.execute(
        select(FolioAssignment).where(
            FolioAssignment.customer_id == customer_id,
            FolioAssignment.doc_type == doc_type,
            FolioAssignment.folio == folio,
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = status
        db.commit()


#: Minutos tras los cuales un folio ``assigned`` sin desenlace se considera
#: huérfano: la emisión se cortó (caída del proceso) entre pedir el folio y
#: marcarlo emitido o fallido.
STALE_ASSIGNMENT_MINUTES = 15


def caf_state(row: Caf, last_folio: int, certification: bool, today: dt.date, unused: int) -> str:
    """in_use | pending | exhausted | retired | expired | wrong_environment.

    ``unused``: folios del rango que nunca se asignaron. Un CAF vencido o de
    otro ambiente que el asignador saltó queda «detrás» del puntero sin haberse
    usado; llamarlo agotado escondería folios que hay que anular en el SII.
    """
    if row.key_id is not None and (row.key_id == CERTIFICATION_IDK) != certification:
        return "wrong_environment"
    if row.expires_on is not None and today > row.expires_on and unused:
        return "expired"
    if row.folio_to <= last_folio:
        return "exhausted"
    if row.exhausted:
        return "retired"
    return "in_use" if row.folio_from <= last_folio + 1 else "pending"


def folio_report(db: Session, customer: Customer, now: dt.datetime | None = None) -> list[dict]:
    """Inventario y trazabilidad de folios por tipo de documento.

    Para auditar: qué CAF hay y en qué estado, cuántos folios quedan de verdad
    utilizables, y qué folios se gastaron sin un documento válido —fallidos o
    huérfanos—, que son los que hay que revisar y, si corresponde, anular en el
    SII.
    """
    now = now or dt.datetime.now(dt.UTC).replace(tzinfo=None)
    today = now.date()
    certification = customer.environment == SiiEnvironment.CERTIFICATION
    pointers = {
        p.doc_type: p.last_folio
        for p in db.query(FolioPointer).filter(FolioPointer.customer_id == customer.id)
    }
    cafs = (
        db.query(Caf)
        .filter(Caf.customer_id == customer.id)
        .order_by(Caf.doc_type, Caf.folio_from)
        .all()
    )
    counts: dict[tuple[int, str], int] = {
        (doc_type, status): count
        for doc_type, status, count in db.query(
            FolioAssignment.doc_type, FolioAssignment.status, func.count()
        )
        .filter(FolioAssignment.customer_id == customer.id)
        .group_by(FolioAssignment.doc_type, FolioAssignment.status)
    }
    stale_before = now - dt.timedelta(minutes=STALE_ASSIGNMENT_MINUTES)
    review = (
        db.query(FolioAssignment)
        .filter(
            FolioAssignment.customer_id == customer.id,
            (FolioAssignment.status == "failed")
            | (
                (FolioAssignment.status == "assigned") & (FolioAssignment.created_at < stale_before)
            ),
        )
        .order_by(FolioAssignment.doc_type, FolioAssignment.folio)
        .limit(1000)
        .all()
    )

    types = sorted({c.doc_type for c in cafs} | set(pointers))
    report = []
    for doc_type in types:
        last = pointers.get(doc_type, 0)
        rows = []
        usable = 0
        for c in (c for c in cafs if c.doc_type == doc_type):
            used = (
                db.query(func.count(FolioAssignment.id))
                .filter(
                    FolioAssignment.customer_id == customer.id,
                    FolioAssignment.doc_type == doc_type,
                    FolioAssignment.folio.between(c.folio_from, c.folio_to),
                )
                .scalar()
                or 0
            )
            unused = c.folio_to - c.folio_from + 1 - used
            state = caf_state(c, last, certification, today, unused)
            remaining = max(0, c.folio_to - max(last, c.folio_from - 1))
            if state in ("in_use", "pending"):
                usable += remaining
            rows.append(
                {
                    "id": c.id,
                    "folio_from": c.folio_from,
                    "folio_to": c.folio_to,
                    "authorized_on": c.authorized_on,
                    "expires_on": c.expires_on,
                    "state": state,
                    "remaining": remaining,
                    "unused": unused,
                }
            )
        report.append(
            {
                "doc_type": doc_type,
                "last_folio": last,
                "usable_remaining": usable,
                "issued": counts.get((doc_type, "issued"), 0),
                "failed": counts.get((doc_type, "failed"), 0),
                "assigned": counts.get((doc_type, "assigned"), 0),
                "cafs": rows,
                "to_review": [
                    {
                        "folio": a.folio,
                        "status": "failed" if a.status == "failed" else "orphaned",
                        "request_id": a.request_id,
                        "assigned_at": a.created_at,
                    }
                    for a in review
                    if a.doc_type == doc_type
                ],
            }
        )
    return report
