"""Servicio de boleta electrónica (39/41) y consumo de folios (RCOF).

La boleta va por un canal distinto al del DTE: su propio sobre
(``EnvioBOLETA``), su propio esquema y **servicios REST** del SII en otros
servidores (``apicert``/``api``, no Maullín ni Palena), con un token propio.
"""

from __future__ import annotations

import base64
import datetime as dt
from zoneinfo import ZoneInfo

from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType, ReferenceCode, ServiceIndicator
from dte_chile.folio_report import (
    FolioReportCover,
    ReportLine,
    build_folio_report,
)
from dte_chile.folio_report import serialize as serialize_report
from dte_chile.models import DTE, Issuer, Item, Receiver, Reference
from dte_chile.receipt import (
    ANONYMOUS_RECEIVER_RUT,
    ReceiptCover,
    build_receipt,
    build_receipt_envelope,
    serialize,
    subtotals_for,
)
from dte_chile.receipt_client import ReceiptClient, ReceiptEnvironment
from dte_chile.signer import sign_document
from dte_chile.validation import Validator
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.config import get_settings
from app.core.logging import request_id_var
from app.db.models import Customer, IssuedReceipt, ReceiptSubmission
from app.errors.exceptions import DomainError

_CL_TZ = ZoneInfo("America/Santiago")


def _anonymous_receiver() -> Receiver:
    """Consumidor final: la boleta no identifica al comprador."""
    return Receiver(
        rut=ANONYMOUS_RECEIVER_RUT, business_name="", activity="", address="", commune=""
    )


def _client(customer: Customer, cert: Certificate, settings) -> ReceiptClient:
    return ReceiptClient(
        cert,
        ReceiptEnvironment[customer.environment.name],
        timeout=settings.request_timeout_s,
    )


def issue_batch(db: Session, customer: Customer, cert, req) -> dict:
    """Emite N boletas dentro de un único EnvioBOLETA.

    El set de certificación de boletas debe viajar en un solo sobre, junto con
    su RCOF.
    """
    from app.services import folio_service

    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    # Validar TODAS antes de pedir folios: una boleta malformada no debe quemar
    # el folio de las demás.
    receipts: list[DTE] = []
    for position, item in enumerate(req.receipts, start=1):
        prefix = f"Boleta {position}: "
        if item.issuer.rut != customer.rut:
            raise DomainError(
                f"{prefix}El RUT emisor ({item.issuer.rut}) no corresponde al "
                f"cliente ({customer.rut})."
            )
        receipt = DTE(
            type=DTEType(item.type),
            folio=0,
            issue_date=item.issue_date,
            issuer=Issuer(**item.issuer.model_dump()),
            receiver=Receiver(**item.receiver.model_dump())
            if item.receiver
            else _anonymous_receiver(),
            items=[Item(**line.model_dump()) for line in item.items],
            references=[
                Reference(
                    doc_type=r.doc_type,
                    folio=r.folio,
                    date=None,  # el XSD de boleta no define FchRef
                    # CodRef: numérico en un DTE, alfanumérico en boleta («SET»).
                    code=ReferenceCode(r.code) if isinstance(r.code, int) else r.code,
                    reason=r.reason,
                )
                for r in item.references
            ],
            prices_include_vat=item.prices_include_vat,
            service_indicator=ServiceIndicator(item.service_indicator),
        )
        try:
            receipt.validate_content()
        except ValueError as ex:
            raise DomainError(f"{prefix}{ex}") from ex
        receipts.append(receipt)

    assigned: list[tuple[int, int]] = []
    cafs = []
    try:
        for receipt, entrada in zip(receipts, req.receipts, strict=True):
            if entrada.folio:  # el ERP ya reservó el folio y numeró con él
                folio = entrada.folio
                caf = folio_service.caf_for_reserved(db, customer.id, int(receipt.type), folio)
            else:
                folio, caf = folio_service.next_folio(
                    db, customer.id, int(receipt.type), request_id_var.get()
                )
            receipt.folio = folio
            assigned.append((int(receipt.type), folio))
            cafs.append(caf)
    except Exception:
        _mark(db, customer, assigned, "failed")
        raise

    try:
        signed = [
            sign_document(build_receipt(r, caf, ts), cert)
            for r, caf in zip(receipts, cafs, strict=True)
        ]
        cover = ReceiptCover(
            issuer_rut=customer.rut,
            sender_rut=cert.rut or customer.rut,
            resolution_date=customer.resolution_date,
            resolution_number=customer.resolution_number,
            subtotals=subtotals_for(receipts),
        )
        xml = serialize(build_receipt_envelope(signed, cover, cert, ts))

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = None
        if req.send:
            client = _client(customer, cert, settings)
            try:
                submission = client.send_receipts(xml, customer.rut, cert.rut or customer.rut)
            finally:
                client.session.close()
    except Exception:
        _mark(db, customer, assigned, "failed")
        raise

    _store(db, customer, receipts, signed, _register(db, customer, submission, len(receipts)))
    _mark(db, customer, assigned, "issued")
    return {
        "receipts": [
            {"index": position, "type": doc_type, "folio": folio}
            for position, (doc_type, folio) in enumerate(assigned, start=1)
        ],
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


def send_folio_report(customer: Customer, cert, req) -> dict:
    """Arma, firma y envía el Reporte de Consumo de Folios."""
    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    cover = FolioReportCover(
        issuer_rut=customer.rut,
        sender_rut=cert.rut or customer.rut,
        start_date=req.start_date,
        end_date=req.end_date,
        sequence=req.sequence,
        resolution_date=customer.resolution_date,
        resolution_number=customer.resolution_number,
        correlative=req.correlative,
        lines=[ReportLine(**line.model_dump()) for line in req.lines],
    )
    try:
        xml = serialize_report(build_folio_report(cover, cert, ts))
    except ValueError as ex:
        raise DomainError(str(ex)) from ex

    if req.validate_xsd:
        Validator(settings.schemas_dir).validate(xml)

    submission = None
    if req.send:
        # Por el upload de Maullín/Palena, no por la API de boleta: la
        # especificación de esa API dice que «palena.sii.cl es la plataforma
        # dedicada para la recepción de DTE y RVD» (RVD, ex RCOF).
        from app.services import sii_upload

        submission = sii_upload.upload(
            customer, cert, xml, customer.rut, settings.request_timeout_s
        )

    return {
        "start_date": req.start_date,
        "end_date": req.end_date,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


def _mark(db: Session, customer: Customer, assigned, status: str) -> None:
    from app.services import folio_service

    for doc_type, folio in assigned:
        folio_service.mark_assignment(db, customer.id, doc_type, folio, status)


def _register(db: Session, customer: Customer, submission, count: int) -> ReceiptSubmission | None:
    """Anota el envío para poder cuadrarlo después contra lo que diga el SII."""
    if submission is None or not submission.track_id:
        return None
    row = ReceiptSubmission(
        customer_id=customer.id,
        track_id=str(submission.track_id),
        document_count=count,
        upload_status=str(submission.status or "")[:40],
    )
    db.add(row)
    db.flush()
    return row


def _store(
    db: Session,
    customer: Customer,
    receipts: list[DTE],
    signed: list,
    submission: ReceiptSubmission | None = None,
) -> None:
    """Guarda cada boleta para que el consumidor pueda recuperarla después.

    Se almacena el ``<DTE>`` **individual**, no el sobre: es lo que hace falta
    para reimprimirla, y evita que una consulta exponga las boletas de otros
    compradores. Los documentos se toman ya firmados, sin volver a parsear el
    sobre. El XML va cifrado; los campos de búsqueda, en claro.
    """
    from lxml import etree

    for receipt, node in zip(receipts, signed, strict=True):
        single = etree.tostring(node, encoding="ISO-8859-1", xml_declaration=True)
        db.add(
            IssuedReceipt(
                customer_id=customer.id,
                doc_type=int(receipt.type),
                folio=receipt.folio,
                issue_date=receipt.issue_date,
                total_amount=receipt.total_amount,
                xml_encrypted=crypto.encrypt(single),
                submission_id=submission.id if submission else None,
            )
        )
    db.commit()


# --------------------------------------------------------------------------- #
#  Cuadratura y recuperación
# --------------------------------------------------------------------------- #
#: Estados en que el SII todavía no termina de procesar el envío.
IN_PROCESS_STATES = {"REC", "SOK", "CRT", "FOK", "PRD", "PDR"}


def summarize(stats: list | None) -> dict[str, int]:
    """Suma por tipo lo informado, aceptado, con reparos y rechazado."""
    total = {"informed": 0, "accepted": 0, "repaired": 0, "rejected": 0}
    keys = {
        "informed": "informados",
        "accepted": "aceptados",
        "repaired": "reparos",
        "rejected": "rechazados",
    }
    for line in stats or []:
        if not isinstance(line, dict):
            continue
        for ours, sii in keys.items():
            try:
                total[ours] += int(line.get(sii) or 0)
            except (TypeError, ValueError):
                continue
    return total


def list_submissions(db: Session, customer: Customer, limit: int = 100) -> list[ReceiptSubmission]:
    return (
        db.query(ReceiptSubmission)
        .filter(ReceiptSubmission.customer_id == customer.id)
        .order_by(ReceiptSubmission.sent_at.desc(), ReceiptSubmission.id.desc())
        .limit(limit)
        .all()
    )


def refresh_submission(db: Session, customer: Customer, cert, track_id: str) -> ReceiptSubmission:
    """Consulta al SII el estado de un envío de boletas y lo guarda."""
    row = (
        db.query(ReceiptSubmission)
        .filter(
            ReceiptSubmission.customer_id == customer.id,
            ReceiptSubmission.track_id == track_id,
        )
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"No hay un envío de boletas con TrackID {track_id}.")
    client = _client(customer, cert, get_settings())
    try:
        status = client.submission_status(track_id, customer.rut)
    finally:
        client.session.close()
    row.sii_state = status.state[:20]
    row.sii_stats = status.stats or None
    row.sii_details = status.details or None
    row.checked_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    db.commit()
    db.refresh(row)
    return row


def stored_receipt_xml(db: Session, customer: Customer, doc_type: int, folio: int) -> bytes:
    """El XML firmado de una boleta emitida, para entregarlo si el SII lo pide."""
    row = (
        db.query(IssuedReceipt)
        .filter(
            IssuedReceipt.customer_id == customer.id,
            IssuedReceipt.doc_type == doc_type,
            IssuedReceipt.folio == folio,
        )
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"No hay una boleta tipo {doc_type} folio {folio} emitida.")
    return crypto.decrypt(row.xml_encrypted)
