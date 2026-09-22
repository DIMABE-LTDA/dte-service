"""Servicio de emisión de DTE: folio → build → firma → sobre → validar → enviar.

Dos entradas: ``issue`` (un documento, un sobre) e ``issue_batch`` (N documentos
en UN solo sobre, como exige el set de certificación del SII).
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

from dte_chile.document_types import DispatchType, DTEType, ReferenceCode, TransferType
from dte_chile.envelope import Cover, build_envelope, serialize
from dte_chile.export_invoice import (
    Customs,
    ExportDocument,
    ExportItem,
    OtherCurrency,
    PackageGroup,
    build_export,
)
from dte_chile.models import (
    DTE,
    Driver,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
    Retention,
    Transport,
)
from dte_chile.parser import ParseError, parse_documents
from dte_chile.representation import (
    TAX_COPY,
    TRANSFERABLE_COPY,
    ResolutionInfo,
    generate_copies,
    generate_html,
    is_cedible,
)
from dte_chile.settlement import (
    Commission,
    Settlement,
    SettlementLine,
    build_settlement,
)
from dte_chile.signer import sign_document
from dte_chile.validation import Validator
from dte_chile.xml_builder import build_document
from lxml import etree
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import request_id_var
from app.db.models import Customer
from app.errors.exceptions import DomainError
from app.services import sii_upload

# El SII timbra/fecha en hora chilena: fijar la TZ explícita (no la del host).
_CL_TZ = ZoneInfo("America/Santiago")


def _reference(ref) -> Reference:
    return Reference(
        doc_type=ref.doc_type,
        folio=ref.folio,
        date=ref.date,
        code=ReferenceCode(ref.code) if ref.code is not None else None,
        reason=ref.reason,
    )


def _transport(req) -> Transport | None:
    """Mapea la sección de transporte del request (None si no viene)."""
    if req.transport is None:
        return None
    data = req.transport.model_dump()
    driver = data.pop("driver")
    return Transport(
        **data,
        driver=Driver(rut=driver["rut"], name=driver["name"]) if driver else None,
    )


def _domain_dte(doc) -> DTE:
    """Arma el DTE de dominio SIN folio (queda en 0).

    Se construye y valida todo el contenido antes de pedirle un folio al CAF:
    una entrada malformada no debe quemar uno.
    """
    return DTE(
        type=DTEType(doc.type),
        folio=0,
        issue_date=doc.issue_date,
        issuer=Issuer(**doc.issuer.model_dump()),
        receiver=Receiver(**doc.receiver.model_dump()),
        items=[Item(**item.model_dump()) for item in doc.items],
        references=[_reference(r) for r in doc.references],
        global_discounts=[GlobalDiscount(**d.model_dump()) for d in doc.global_discounts],
        retentions=[Retention(**r.model_dump()) for r in doc.retentions],
        dispatch_type=DispatchType(doc.dispatch_type) if doc.dispatch_type else None,
        transfer_type=TransferType(doc.transfer_type) if doc.transfer_type else None,
        transport=_transport(doc),
    )


def _check_issuer(customer: Customer, doc, prefix: str = "") -> None:
    """El emisor es siempre el cliente resuelto por el tenant (igual que RCV/libros)."""
    if doc.issuer.rut != customer.rut:
        raise DomainError(
            f"{prefix}El RUT emisor ({doc.issuer.rut}) no corresponde al cliente ({customer.rut})."
        )


def _cover(customer: Customer, cert, issuer_rut: str, subtotals: list[tuple[int, int]]) -> Cover:
    return Cover(
        issuer_rut=issuer_rut,
        sender_rut=cert.rut or issuer_rut,
        resolution_date=customer.resolution_date,
        resolution_number=customer.resolution_number,
        subtotals=subtotals,
    )


def _send(customer: Customer, cert, xml: bytes, issuer_rut: str, settings):
    return sii_upload.upload(customer, cert, xml, issuer_rut, settings.request_timeout_s)


#: Estados con que el SII dice que todavía está procesando el sobre: recibido
#: (REC), esquema validado (SOK), carátula OK (CRT), firma OK (FOK), pendiente
#: (PDR) y libro en proceso (LSO). Ninguno es veredicto del documento.
IN_PROCESS_STATES = frozenset({"REC", "SOK", "CRT", "FOK", "PDR", "LSO"})


def in_process(status: str | None) -> bool:
    """¿El SII aún no termina con este envío?"""
    return not status or status.upper() in IN_PROCESS_STATES


def issue(db: Session, customer: Customer, cert, req) -> dict:
    from app.services import folio_service

    _check_issuer(customer, req)

    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    dte = _domain_dte(req)
    try:
        dte.validate_content()
    except ValueError as ex:
        # El motor señala la entrada inválida con ValueError; para el cliente
        # HTTP es un 400, no un 500.
        raise DomainError(str(ex)) from ex

    if req.folio:  # el ERP ya reservó el folio y numeró su documento con él
        folio = req.folio
        caf = folio_service.caf_for_reserved(db, customer.id, req.type, folio)
    else:
        folio, caf = folio_service.next_folio(db, customer.id, req.type, request_id_var.get())
    dte.folio = folio
    try:
        signed = sign_document(build_document(dte, caf, ts), cert)
        issuer_rut = dte.issuer.rut.value
        cover = _cover(customer, cert, issuer_rut, [(int(dte.type), 1)])
        xml = serialize(build_envelope([signed], cover, cert, ts))

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        # El folio ya se consumió; déjalo trazado como quemado sin documento.
        folio_service.mark_assignment(db, customer.id, req.type, folio, "failed")
        raise

    folio_service.mark_assignment(db, customer.id, req.type, folio, "issued")
    return {
        "type": int(dte.type),
        "folio": folio,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


# --------------------------------------------------------------------------- #
#  Emisión en lote: N documentos en UN solo EnvioDTE
# --------------------------------------------------------------------------- #
def _resolve_batch_references(documents, dtes: list[DTE]) -> None:
    """Completa las referencias que apuntan a otro documento del mismo lote.

    Sólo puede correr DESPUÉS de asignar folios: es el folio recién asignado el
    que la nota necesita referenciar.
    """
    for position, (doc, dte) in enumerate(zip(documents, dtes, strict=True), start=1):
        for ref_in, ref in zip(doc.references, dte.references, strict=True):
            if ref_in.batch_index is None:
                continue
            if ref_in.batch_index > len(dtes):
                raise DomainError(
                    f"Documento {position}: la referencia apunta a la posición "
                    f"{ref_in.batch_index} y el lote trae {len(dtes)} documentos."
                )
            if ref_in.batch_index == position:
                raise DomainError(f"Documento {position}: no puede referenciarse a sí mismo.")
            target = dtes[ref_in.batch_index - 1]
            ref.doc_type = int(target.type)
            ref.folio = str(target.folio)
            ref.date = target.issue_date


def issue_batch(db: Session, customer: Customer, cert, req) -> dict:
    """Emite N documentos dentro de un único sobre.

    Es lo que exige el set de certificación: cada set se entrega como UN
    ``EnvioDTE``. Además permite que una nota referencie por posición a otro
    documento del mismo lote, cuyo folio recién se conoce al asignarlo.
    """
    from app.services import folio_service

    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    # 1) Validar TODO el lote antes de tocar folios: si un documento está
    #    malformado, ninguno debe quemar folio.
    dtes: list[DTE] = []
    for position, doc in enumerate(req.documents, start=1):
        prefix = f"Documento {position}: "
        _check_issuer(customer, doc, prefix)
        try:
            dte = _domain_dte(doc)
            dte.validate_content()
        except ValueError as ex:
            raise DomainError(f"{prefix}{ex}") from ex
        dtes.append(dte)

    # 2) Asignar un folio a cada documento. Si falla a medio camino, los ya
    #    asignados quedan trazados como quemados.
    assigned: list[tuple[int, int]] = []  # (doc_type, folio)
    cafs = []
    try:
        for dte in dtes:
            folio, caf = folio_service.next_folio(
                db, customer.id, int(dte.type), request_id_var.get()
            )
            dte.folio = folio
            assigned.append((int(dte.type), folio))
            cafs.append(caf)
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    try:
        _resolve_batch_references(req.documents, dtes)

        signed = [
            sign_document(build_document(dte, caf, ts), cert)
            for dte, caf in zip(dtes, cafs, strict=True)
        ]
        issuer_rut = dtes[0].issuer.rut.value
        subtotals = sorted(Counter(int(dte.type) for dte in dtes).items())
        xml = serialize(
            build_envelope(signed, _cover(customer, cert, issuer_rut, subtotals), cert, ts)
        )

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    _mark_batch(db, customer, assigned, "issued")
    return {
        "documents": [
            {"index": position, "type": doc_type, "folio": folio}
            for position, (doc_type, folio) in enumerate(assigned, start=1)
        ],
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


def _mark_batch(db: Session, customer: Customer, assigned, status: str) -> None:
    from app.services import folio_service

    for doc_type, folio in assigned:
        folio_service.mark_assignment(db, customer.id, doc_type, folio, status)


# --------------------------------------------------------------------------- #
#  Representación impresa
# --------------------------------------------------------------------------- #
def print_documents(customer: Customer, req) -> dict:
    """Genera el impreso de cada documento del sobre.

    El sobre viene del propio emisor (el servicio no almacena DTE). El TED se
    toma tal cual del XML: reconstruirlo daría un timbre distinto al firmado.
    """
    try:
        xml = base64.b64decode(req.xml_base64, validate=True)
    except (ValueError, binascii.Error) as ex:
        raise DomainError(f"El XML no viene en base64 válido: {ex}") from ex

    try:
        parsed = parse_documents(xml)
    except (ParseError, etree.XMLSyntaxError) as ex:
        raise DomainError(f"No se pudo leer el sobre: {ex}") from ex

    resolution = ResolutionInfo(
        number=customer.resolution_number,
        date=customer.resolution_date,
        sii_office=req.sii_office or customer.issuer_sii_office or "SANTIAGO",
    )

    documents = []
    for position, item in enumerate(parsed, start=1):
        if req.copies == "both":
            html = generate_copies(
                item.dte, item.element, resolution, verification_url=req.verification_url
            )
        else:
            copy = TAX_COPY if req.copies == "tax" else TRANSFERABLE_COPY
            html = generate_html(
                item.dte,
                item.element,
                resolution,
                copy=copy,
                verification_url=req.verification_url,
            )
        documents.append(
            {
                "index": position,
                "type": int(item.dte.type),
                "folio": item.dte.folio,
                "cedible": is_cedible(item.dte),
                "html_base64": base64.b64encode(html.encode("utf-8")).decode("ascii"),
            }
        )
    return {"documents": documents}


def print_stored(
    db, customer: Customer, doc_type: int, folio: int, copies="both", verification_url=""
):
    """El impreso de un documento ya emitido, desde el archivo del servicio.

    Ahorra al ERP tener que devolver el sobre para reimprimir: el documento ya
    está guardado aquí. El TED se toma tal cual del XML firmado; reconstruirlo
    daría un timbre distinto del que se timbró.
    """
    from app.services import idempotency

    xml = idempotency.stored_xml(db, customer, doc_type, folio)
    parsed = parse_documents(xml)[0]
    resolution = ResolutionInfo(
        number=customer.resolution_number,
        date=customer.resolution_date,
        sii_office=customer.issuer_sii_office or "SANTIAGO",
    )
    if copies == "both":
        return generate_copies(
            parsed.dte, parsed.element, resolution, verification_url=verification_url
        )
    copy = TAX_COPY if copies == "tax" else TRANSFERABLE_COPY
    return generate_html(
        parsed.dte, parsed.element, resolution, copy=copy, verification_url=verification_url
    )


# --------------------------------------------------------------------------- #
#  Liquidación factura (43)
# --------------------------------------------------------------------------- #
def _domain_settlement(req) -> Settlement:
    """Traduce la petición a una liquidación del dominio."""
    return Settlement(
        folio=0,
        issue_date=req.issue_date,
        issuer=Issuer(**req.issuer.model_dump()),
        receiver=Receiver(**req.receiver.model_dump()),
        lines=[SettlementLine(**line.model_dump()) for line in req.lines],
        commissions=[Commission(**c.model_dump()) for c in req.commissions],
        references=[_reference(r) for r in req.references],
    )


def issue_settlement(db: Session, customer: Customer, cert, req) -> dict:
    """Emite una Liquidación Factura Electrónica.

    Va por su propio camino porque el XSD le da una raíz distinta
    (``<Liquidacion>``): no pasa por ``build_document``.
    """
    from app.services import folio_service

    _check_issuer(customer, req)
    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    settlement = _domain_settlement(req)
    try:
        settlement.validate_content()
    except ValueError as ex:
        raise DomainError(str(ex)) from ex

    doc_type = int(settlement.type)
    folio, caf = folio_service.next_folio(db, customer.id, doc_type, request_id_var.get())
    settlement.folio = folio
    try:
        signed = sign_document(build_settlement(settlement, caf, ts), cert)
        issuer_rut = settlement.issuer.rut.value
        cover = _cover(customer, cert, issuer_rut, [(doc_type, 1)])
        xml = serialize(build_envelope([signed], cover, cert, ts))

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        folio_service.mark_assignment(db, customer.id, doc_type, folio, "failed")
        raise

    folio_service.mark_assignment(db, customer.id, doc_type, folio, "issued")
    return {
        "type": doc_type,
        "folio": folio,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


# --------------------------------------------------------------------------- #
#  Exportación (110 / 111 / 112)
# --------------------------------------------------------------------------- #
def _domain_export(req) -> ExportDocument:
    """Traduce la petición a un documento de exportación del dominio."""
    customs = None
    if req.customs is not None:
        data = req.customs.model_dump()
        packages = data.pop("packages")
        customs = Customs(**data, packages=[PackageGroup(**p) for p in packages])

    otra = None
    if getattr(req, "other_currency", None) is not None:
        otra = OtherCurrency(**req.other_currency.model_dump())

    return ExportDocument(
        type=DTEType(req.type),
        folio=0,
        issue_date=req.issue_date,
        issuer=Issuer(**req.issuer.model_dump()),
        receiver=Receiver(**req.receiver.model_dump()),
        currency=req.currency,
        items=[ExportItem(**item.model_dump()) for item in req.items],
        global_charges=[GlobalDiscount(**d.model_dump()) for d in req.global_charges],
        references=[_reference(r) for r in req.references],
        customs=customs,
        other_currency=otra,
        payment_mode=req.payment_mode,
        service_indicator=req.service_indicator,
        foreign_id=req.foreign_id,
        receiver_nationality=req.receiver_nationality,
    )


def issue_export(db: Session, customer: Customer, cert, req) -> dict:
    """Emite un documento de exportación (raíz <Exportaciones>, moneda extranjera)."""
    from app.services import folio_service

    _check_issuer(customer, req)
    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    document = _domain_export(req)
    try:
        document.validate_content()
    except ValueError as ex:
        raise DomainError(str(ex)) from ex

    folio, caf = folio_service.next_folio(db, customer.id, req.type, request_id_var.get())
    document.folio = folio
    try:
        signed = sign_document(build_export(document, caf, ts), cert)
        issuer_rut = document.issuer.rut.value
        cover = _cover(customer, cert, issuer_rut, [(req.type, 1)])
        xml = serialize(build_envelope([signed], cover, cert, ts))

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        folio_service.mark_assignment(db, customer.id, req.type, folio, "failed")
        raise

    folio_service.mark_assignment(db, customer.id, req.type, folio, "issued")
    return {
        "type": req.type,
        "folio": folio,
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


def issue_export_batch(db: Session, customer: Customer, cert, req) -> dict:
    """Emite N documentos de exportación dentro de un único sobre.

    El set de certificación entrega cada set de exportación como UN envío, y
    dentro de él la nota de crédito referencia a la factura y la de débito a la
    nota de crédito. Como el folio recién se conoce al asignarlo, la referencia
    se hace por posición dentro del lote, igual que en el lote normal.
    """
    from app.services import folio_service

    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    # 1) Validar TODO antes de tocar folios: un documento malformado no debe
    #    quemarle el folio a los demás.
    documents: list[ExportDocument] = []
    for position, doc in enumerate(req.documents, start=1):
        prefix = f"Documento {position}: "
        _check_issuer(customer, doc, prefix)
        try:
            document = _domain_export(doc)
            document.validate_content()
        except ValueError as ex:
            raise DomainError(f"{prefix}{ex}") from ex
        documents.append(document)

    # 2) Asignar folios.
    assigned: list[tuple[int, int]] = []
    cafs = []
    try:
        for document in documents:
            folio, caf = folio_service.next_folio(
                db, customer.id, int(document.type), request_id_var.get()
            )
            document.folio = folio
            assigned.append((int(document.type), folio))
            cafs.append(caf)
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    try:
        _resolve_batch_references(req.documents, documents)

        signed = [
            sign_document(build_export(document, caf, ts), cert)
            for document, caf in zip(documents, cafs, strict=True)
        ]
        issuer_rut = documents[0].issuer.rut.value
        subtotals = sorted(Counter(int(d.type) for d in documents).items())
        xml = serialize(
            build_envelope(signed, _cover(customer, cert, issuer_rut, subtotals), cert, ts)
        )

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    _mark_batch(db, customer, assigned, "issued")
    return {
        "documents": [
            {"index": position, "type": doc_type, "folio": folio}
            for position, (doc_type, folio) in enumerate(assigned, start=1)
        ],
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }


def issue_settlement_batch(db: Session, customer: Customer, cert, req) -> dict:
    """Emite N liquidaciones dentro de un único sobre.

    El SET BASICO LIQUIDACIONES se entrega como un solo envío. Sus casos no se
    referencian entre sí, pero mandarlos por separado dejaría cuatro envíos
    donde el Servicio espera uno.
    """
    from app.services import folio_service

    settings = get_settings()
    ts = dt.datetime.now(_CL_TZ).replace(microsecond=0, tzinfo=None)

    settlements: list[Settlement] = []
    for position, doc in enumerate(req.documents, start=1):
        prefix = f"Documento {position}: "
        _check_issuer(customer, doc, prefix)
        try:
            settlement = _domain_settlement(doc)
            settlement.validate_content()
        except ValueError as ex:
            raise DomainError(f"{prefix}{ex}") from ex
        settlements.append(settlement)

    assigned: list[tuple[int, int]] = []
    cafs = []
    try:
        for settlement in settlements:
            doc_type = int(settlement.type)
            folio, caf = folio_service.next_folio(db, customer.id, doc_type, request_id_var.get())
            settlement.folio = folio
            assigned.append((doc_type, folio))
            cafs.append(caf)
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    try:
        _resolve_batch_references(req.documents, settlements)

        signed = [
            sign_document(build_settlement(settlement, caf, ts), cert)
            for settlement, caf in zip(settlements, cafs, strict=True)
        ]
        issuer_rut = settlements[0].issuer.rut.value
        subtotals = sorted(Counter(int(s.type) for s in settlements).items())
        xml = serialize(
            build_envelope(signed, _cover(customer, cert, issuer_rut, subtotals), cert, ts)
        )

        if req.validate_xsd:
            Validator(settings.schemas_dir).validate(xml)

        submission = _send(customer, cert, xml, issuer_rut, settings) if req.send else None
    except Exception:
        _mark_batch(db, customer, assigned, "failed")
        raise

    _mark_batch(db, customer, assigned, "issued")
    return {
        "documents": [
            {"index": position, "type": doc_type, "folio": folio}
            for position, (doc_type, folio) in enumerate(assigned, start=1)
        ],
        "xml_base64": base64.b64encode(xml).decode("ascii"),
        "submission": submission,
    }
