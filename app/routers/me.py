"""Configuración del propio contribuyente, para el cliente máquina.

Existe porque hay datos del emisor que viven aquí y se usan allá: el número y la
fecha de resolución van en la carátula de **todos** los DTE, y hasta ahora sólo
se veían y editaban desde el portal. Quien integra desde su ERP no tenía forma
de comprobar contra qué está emitiendo, ni de corregirlo sin pedirle a otra
persona que entrara al portal.

Se limita a lo que es dato del propio emisor y afecta a lo que él emite: la
resolución, el perfil del emisor (razón social, giro, ACTECO, dirección), su
certificado digital, sus CAF y su clave tributaria. El ambiente NO se toca
desde aquí: se resuelve al autenticar, a partir de la credencial usada, y poder
cambiarlo con esa misma credencial sería justo el agujero que la separación por
ambiente evita —una apiKey de certificación pasaría a emitir en producción—.

**Por qué esto duplica endpoints de `/admin`.** Los de administración toman el
cliente del path, y la credencial que los abre no está acotada a ninguno: vale
para todos los contribuyentes del servicio. Guardarla en un ERP para que suba
un CAF le entregaría a esa instalación las llaves de los demás inquilinos. Aquí
el cliente es el que resolvió la credencial, así que quien integra registra lo
suyo —y sólo lo suyo— desde su propio sistema.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.models import Customer
from app.db.session import get_db
from app.deps.auth import require_bhe, require_dte
from app.schemas.admin import (
    CafInfo,
    CafOut,
    CafUpload,
    CertificateInfo,
    CertificateOut,
    CertificateUpload,
    FolioTypeReportOut,
    IssuerProfile,
    SiiKeyStatus,
    SiiKeyUpload,
)
from app.services import (
    audit_service,
    certificate_service,
    customer_service,
    folio_service,
    sii_credential_service,
)

router = APIRouter(prefix="/me", tags=["Configuración"])


class MyConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_code: str
    name: str
    rut: str
    #: CERTIFICATION o PRODUCTION. Sólo lectura: lo fija la credencial.
    environment: str
    resolution_number: int
    resolution_date: dt.date
    #: Datos del emisor que el sistema pone en cada documento que emite por
    #: su cuenta —los sets de certificación—. Un ERP que emite por el API
    #: sigue mandando los suyos en cada documento.
    issuer: IssuerProfile
    issuer_missing: list[str]


class MyConfigUpdate(BaseModel):
    """Lo editable: la resolución y los datos del emisor. Nunca el ambiente.

    Todos los campos son opcionales para poder corregir uno sin repetir el resto.
    """

    resolution_number: int | None = Field(default=None, ge=0)
    resolution_date: dt.date | None = None
    #: Si viene, reemplaza el perfil entero: así Odoo lo sincroniza desde su
    #: ficha de compañía sin tener que saber qué había antes.
    issuer: IssuerProfile | None = None


def _out(customer: Customer) -> MyConfigOut:
    return MyConfigOut(
        customer_code=customer.key,
        name=customer.name,
        rut=customer.rut,
        environment=customer.environment.value
        if hasattr(customer.environment, "value")
        else str(customer.environment),
        resolution_number=customer.resolution_number,
        resolution_date=customer.resolution_date,
        issuer=IssuerProfile(**customer_service.issuer_profile(customer)),
        issuer_missing=customer_service.issuer_missing(customer),
    )


@router.get("", response_model=MyConfigOut)
def my_config(
    customer: Customer = Depends(require_dte),
) -> MyConfigOut:
    """Con qué datos está emitiendo esta credencial.

    Sirve para verificar antes de emitir en serie: el ambiente y la resolución
    son los dos datos que, mal puestos, invalidan todos los documentos.
    """
    return _out(customer)


@router.patch("", response_model=MyConfigOut)
def update_my_config(
    body: MyConfigUpdate,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> MyConfigOut:
    """Corrige la resolución del emisor.

    Es dato del propio contribuyente y afecta sólo a lo que él emite, así que su
    propia credencial basta. No cambia nada ya emitido: la carátula viaja dentro
    de cada XML firmado.
    """
    if body.resolution_number is not None:
        customer.resolution_number = body.resolution_number
    if body.resolution_date is not None:
        customer.resolution_date = body.resolution_date
    if body.issuer is not None:
        customer_service.set_issuer(customer, body.issuer)
    db.commit()
    db.refresh(customer)
    return _out(customer)


# --------------------------------------------------------------------------- #
#  Puesta en marcha desde el ERP: certificado, CAF y clave tributaria
# --------------------------------------------------------------------------- #
#: Las mutaciones quedan auditadas sin usuario del portal: las hizo una máquina
#: con la credencial del propio contribuyente.
_MACHINE: int | None = None


def _audit(db: Session, customer: Customer, action: str, summary: str) -> None:
    audit_service.record_change(
        db, _MACHINE, action, "customer", str(customer.id), f"[API del cliente] {summary}"
    )


@router.get("/certificates", response_model=list[CertificateInfo])
def my_certificates(
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> list[CertificateInfo]:
    """Los certificados cargados, con su vencimiento y el RUT del firmante.

    Ese RUT es el de una persona, no el de la empresa: es quien necesita el
    atributo «Enviar Doctos» en el SII.
    """
    today = dt.date.today()
    return [
        CertificateInfo(
            id=c.id,
            due_date=c.due_date,
            created_at=c.created_at,
            expired=c.due_date < today,
            rut=c.rut,
            holder=c.holder,
            issuer=c.issuer,
        )
        for c in customer_service.list_certificates(db, customer)
    ]


@router.post("/certificate", response_model=CertificateOut)
def upload_my_certificate(
    data: CertificateUpload,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> CertificateOut:
    """Carga el certificado digital (.pfx) del contribuyente."""
    row = certificate_service.store_certificate(
        db, customer, data.file_base64, data.password, commit=False
    )
    _audit(db, customer, "certificate.upload", f"vence {row.due_date}")
    return CertificateOut(id=row.id, rut=customer.rut, due_date=row.due_date)


@router.get("/cafs", response_model=list[CafInfo])
def my_cafs(
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> list[CafInfo]:
    """Los CAF cargados por tipo de documento, con el último folio usado."""
    pointers = customer_service.folio_pointers(db, customer.id)
    return [
        CafInfo(
            id=c.id,
            doc_type=c.doc_type,
            folio_from=c.folio_from,
            folio_to=c.folio_to,
            exhausted=c.exhausted,
            last_folio=pointers.get(c.doc_type, 0),
        )
        for c in customer_service.list_cafs(db, customer)
    ]


@router.post("/caf", response_model=CafOut)
def upload_my_caf(
    data: CafUpload,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> CafOut:
    """Carga un CAF del contribuyente.

    Se rechaza aquí el que el SII rechazaría después con los folios ya
    gastados: llaves que no corresponden entre sí, CAF de otro ambiente o
    vencido, rango solapado con uno ya cargado.
    """
    row = customer_service.add_caf(db, customer, data.xml_base64, commit=False)
    detalle = f"tipo {row.doc_type} folios {row.folio_from}-{row.folio_to}"
    _audit(db, customer, "caf.upload", detalle)
    return CafOut(
        id=row.id, doc_type=row.doc_type, folio_from=row.folio_from, folio_to=row.folio_to
    )


@router.post("/cafs/{caf_id}/retire", response_model=CafInfo)
def retire_my_caf(
    caf_id: int,
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> CafInfo:
    """Saca de circulación un CAF que todavía tiene folios libres.

    Hace falta cuando llega uno que debe reemplazar al vigente: el asignador
    toma siempre el rango disponible más bajo.
    """
    row = customer_service.retire_caf(db, customer, caf_id, commit=False)
    _audit(
        db,
        customer,
        "caf.retire",
        f"tipo {row.doc_type} folios {row.folio_from}-{row.folio_to} fuera de uso",
    )
    pointers = customer_service.folio_pointers(db, customer.id)
    return CafInfo(
        id=row.id,
        doc_type=row.doc_type,
        folio_from=row.folio_from,
        folio_to=row.folio_to,
        exhausted=row.exhausted,
        last_folio=pointers.get(row.doc_type, 0),
    )


@router.get("/folios", response_model=list[FolioTypeReportOut])
def my_folios(
    customer: Customer = Depends(require_dte),
    db: Session = Depends(get_db),
) -> list[FolioTypeReportOut]:
    """Inventario de folios y CAF.

    Es lo mismo que ``GET /dte/folios``, publicado también aquí para que un ERP
    encuentre junta toda la configuración del emisor.
    """
    return [FolioTypeReportOut(**r) for r in folio_service.folio_report(db, customer)]


@router.get("/sii-key", response_model=SiiKeyStatus)
def my_sii_key(
    customer: Customer = Depends(require_bhe),
    db: Session = Depends(get_db),
) -> SiiKeyStatus:
    """Si hay clave tributaria configurada. Nunca devuelve la clave."""
    return SiiKeyStatus(configured=sii_credential_service.has_sii_password(db, customer))


@router.post("/sii-key", response_model=SiiKeyStatus)
def set_my_sii_key(
    data: SiiKeyUpload,
    customer: Customer = Depends(require_bhe),
    db: Session = Depends(get_db),
) -> SiiKeyStatus:
    """Guarda cifrada la clave tributaria del SII.

    Con ella se consultan las boletas de honorarios recibidas, que el SII sólo
    entrega por login web.
    """
    sii_credential_service.store_sii_password(db, customer, data.password, commit=False)
    _audit(db, customer, "sii_key.upload", "clave tributaria guardada")
    return SiiKeyStatus(configured=True)


@router.delete("/sii-key", response_model=SiiKeyStatus)
def delete_my_sii_key(
    customer: Customer = Depends(require_bhe),
    db: Session = Depends(get_db),
) -> SiiKeyStatus:
    sii_credential_service.clear_sii_password(db, customer, commit=False)
    _audit(db, customer, "sii_key.delete", "clave tributaria eliminada")
    return SiiKeyStatus(configured=False)
