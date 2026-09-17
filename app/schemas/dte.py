"""Schemas de emisión de DTE."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IssuerIn(BaseModel):
    rut: str
    business_name: str
    activity: str
    economic_activity: int
    address: str
    commune: str
    city: str = ""
    # Sucursal desde la que se emite. El código lo asigna el SII y aparece en
    # "Mi SII → Direcciones"; respalda el domicilio que declara el documento.
    branch_name: str = ""
    branch_code: int | None = Field(None, ge=1)


class ReceiverIn(BaseModel):
    rut: str
    business_name: str
    activity: str
    address: str
    commune: str
    city: str = ""


class ItemIn(BaseModel):
    name: str
    quantity: float
    unit_price: int
    exempt: bool = False
    description: str = ""
    unit: str = ""
    # Descuento de la línea. Con solo el % basta: el monto se deriva del bruto.
    discount_pct: float = Field(0, ge=0, le=100)
    discount_amount: int | None = Field(None, ge=0)


class GlobalDiscountIn(BaseModel):
    """Descuento/recargo global del documento (<DscRcgGlobal>)."""

    value: float = Field(ge=0)
    kind: Literal["D", "R"] = "D"  # TpoMov: descuento o recargo
    value_type: Literal["%", "$"] = "%"  # TpoValor
    reason: str = ""  # GlosaDR
    scope: Literal["afecto", "exento", "no_afecto"] = "afecto"


class DriverIn(BaseModel):
    """Chofer del traslado (<Chofer>)."""

    rut: str
    name: str


class TransportIn(BaseModel):
    """Sección <Transporte>.

    Los campos ``trailer_plate``, ``departure_date``, ``departure_time`` y
    ``arrival_date`` los incorporó la Res. Ex. N°154, obligatoria desde el
    2026-11-01 en todo documento que acompaña traslado de bienes.
    """

    plate: str = ""
    trailer_plate: str = ""
    carrier_rut: str | None = None
    driver: DriverIn | None = None
    dest_address: str = ""
    dest_commune: str = ""
    dest_city: str = ""
    departure_date: dt.date | None = None
    departure_time: dt.time | None = None
    arrival_date: dt.date | None = None


class RetentionIn(BaseModel):
    """Impuesto o retención adicional (<ImptoReten>).

    Por defecto, el código 15 (IVA retenido total) sobre el IVA completo: es el
    caso normal de la factura de compra, donde el comprador retiene todo el IVA.
    """

    code: int = 15  # TipoImp
    amount: int | None = Field(None, ge=0)  # None → el IVA completo
    rate: float | None = Field(None, ge=0, le=100)


class ReferenceIn(BaseModel):
    # Código numérico del documento (33, 61, 801...) o el literal "SET": el
    # instructivo del SII exige que la PRIMERA referencia de cada documento del
    # set de pruebas sea TpoDocRef=SET con RazonRef "CASO {atención}-{n}". Sin
    # ella el documento no queda asociado a ningún caso del set.
    doc_type: int | Literal["SET"]
    folio: str
    date: dt.date
    code: int | None = None
    reason: str = ""


class DteIssueRequest(BaseModel):
    type: Literal[33, 34, 46, 52, 56, 61]
    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn
    items: list[ItemIn] = Field(min_length=1)
    references: list[ReferenceIn] = []
    global_discounts: list[GlobalDiscountIn] = []
    retentions: list[RetentionIn] = []
    # Traslado de bienes: obligatorio en la guía (52), opcional en la factura
    # que ampara el traslado.
    dispatch_type: Literal[1, 2, 3] | None = None  # TipoDespacho
    transfer_type: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9] | None = None  # IndTraslado
    transport: TransportIn | None = None
    send: bool = True  # subir a Maullín/Palena (según ambiente del cliente)
    validate_xsd: bool = True  # validar contra el XSD antes de enviar


class SubmissionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    track_id: str | None = None
    status: str
    detail: str = ""


class DteIssueResponse(BaseModel):
    type: int
    folio: int
    xml_base64: str
    submission: SubmissionResultOut | None = None


# --------------------------------------------------------------------------- #
#  Emisión en lote: N documentos dentro de UN solo EnvioDTE
# --------------------------------------------------------------------------- #
# El XSD del sobre admite hasta 2000 DTE y 20 SubTotDTE.
MAX_BATCH_DOCUMENTS = 2000


class BatchReferenceIn(ReferenceIn):
    """Referencia que además puede apuntar a otro documento del mismo lote.

    En el set de certificación las notas referencian facturas que se emiten en
    el mismo envío, cuyo folio recién se conoce al asignarlo. Con
    ``batch_index`` (1-based) se apunta a esa posición y el servicio completa
    tipo, folio y fecha una vez asignados.
    """

    batch_index: int | None = Field(None, ge=1)
    # Al usar batch_index estos tres se derivan del documento apuntado.
    doc_type: int | Literal["SET"] | None = None  # type: ignore[assignment]
    folio: str | None = None  # type: ignore[assignment]
    date: dt.date | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _one_way_or_the_other(self) -> BatchReferenceIn:
        explicit = (self.doc_type, self.folio, self.date)
        if self.batch_index is None:
            if any(v is None for v in explicit):
                raise ValueError(
                    "La referencia necesita batch_index, o bien doc_type, folio y date."
                )
        elif any(v is not None for v in explicit):
            raise ValueError(
                "Con batch_index no se informan doc_type, folio ni date: se derivan "
                "del documento referenciado."
            )
        return self


class DteBatchItemIn(BaseModel):
    """Un documento dentro del lote (sin ``send``/``validate_xsd``: son del lote)."""

    type: Literal[33, 34, 46, 52, 56, 61]
    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn
    items: list[ItemIn] = Field(min_length=1)
    references: list[BatchReferenceIn] = []
    global_discounts: list[GlobalDiscountIn] = []
    retentions: list[RetentionIn] = []
    dispatch_type: Literal[1, 2, 3] | None = None
    transfer_type: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9] | None = None
    transport: TransportIn | None = None


class DteBatchRequest(BaseModel):
    documents: list[DteBatchItemIn] = Field(min_length=1, max_length=MAX_BATCH_DOCUMENTS)
    send: bool = True
    validate_xsd: bool = True


class DteBatchDocumentOut(BaseModel):
    index: int  # posición en el lote, 1-based
    type: int
    folio: int


class DteBatchResponse(BaseModel):
    documents: list[DteBatchDocumentOut]
    xml_base64: str  # el sobre completo, uno solo para todo el lote
    submission: SubmissionResultOut | None = None


# --------------------------------------------------------------------------- #
#  Representación impresa
# --------------------------------------------------------------------------- #
class PrintRequest(BaseModel):
    """Reimprime los documentos de un sobre ya emitido.

    El servicio no guarda los DTE: el emisor conserva el ``EnvioDTE`` que le
    devolvió la emisión y lo manda de vuelta para obtener el impreso.
    """

    xml_base64: str  # EnvioDTE completo, o un DTE suelto
    # both = tributario + cedible (el que exige el SII para adjuntar muestras).
    copies: Literal["both", "tax", "transferable"] = "both"
    # Unidad del SII bajo el recuadro. Si no se indica, la de la ficha del
    # emisor; y sin ella, SANTIAGO.
    sii_office: str | None = None
    # Sitio donde el consumidor consulta su boleta. El SII lo exige impreso en
    # la boleta electrónica; en los demás documentos se ignora.
    verification_url: str = ""


class PrintedDocumentOut(BaseModel):
    index: int  # posición dentro del sobre, 1-based
    type: int
    folio: int
    cedible: bool  # si al documento le corresponde ejemplar cedible
    html_base64: str


class PrintResponse(BaseModel):
    documents: list[PrintedDocumentOut]


# --------------------------------------------------------------------------- #
#  Liquidación factura (43)
# --------------------------------------------------------------------------- #
class SettlementLineIn(BaseModel):
    """Una línea de la liquidación. El monto puede ser negativo."""

    # TpoDocLiq: qué tipo de documento se liquida en esta línea.
    liquidated_type: str = Field(min_length=1, max_length=3, examples=["33", "39", "61"])
    name: str
    amount: int  # sin cota inferior: una nota de crédito entra restando
    quantity: float | None = None
    exempt: bool = False
    description: str = ""


class CommissionIn(BaseModel):
    """Comisión u otro cargo del mandatario; se resta del total a liquidar."""

    description: str
    net_amount: int = 0
    exempt_amount: int = 0
    vat_amount: int | None = None  # None → se calcula sobre el neto
    kind: Literal["C", "O"] = "C"  # comisión / otros cargos
    rate: float | None = Field(None, ge=0, le=100)


class SettlementIssueRequest(BaseModel):
    """Emisión de una Liquidación Factura Electrónica (tipo 43)."""

    issue_date: dt.date
    issuer: IssuerIn  # el mandatario
    receiver: ReceiverIn  # el mandante
    lines: list[SettlementLineIn] = Field(min_length=1)
    commissions: list[CommissionIn] = []
    references: list[ReferenceIn] = []
    send: bool = True
    validate_xsd: bool = True


class SettlementBatchItemIn(BaseModel):
    """Una liquidación dentro del lote."""

    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn
    lines: list[SettlementLineIn] = Field(min_length=1)
    commissions: list[CommissionIn] = []
    references: list[BatchReferenceIn] = []


class SettlementBatchRequest(BaseModel):
    """N liquidaciones en UN solo sobre: así se entrega el set del SII."""

    documents: list[SettlementBatchItemIn] = Field(min_length=1, max_length=MAX_BATCH_DOCUMENTS)
    send: bool = True
    validate_xsd: bool = True


# --------------------------------------------------------------------------- #
#  Exportación (110 / 111 / 112)
# --------------------------------------------------------------------------- #
class ExportItemIn(BaseModel):
    """Línea en moneda extranjera: los montos llevan decimales."""

    name: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    unit: str = ""
    description: str = ""
    discount_pct: Decimal | None = Field(None, ge=0, le=100)
    surcharge_pct: Decimal | None = Field(None, ge=0)
    amount: Decimal | None = None  # si viene, manda sobre cantidad × precio


class PackageGroupIn(BaseModel):
    kind_code: int = Field(ge=1)
    quantity: int | None = Field(None, ge=0)
    marks: str = ""
    container_id: str = ""
    seal: str = ""
    seal_issuer: str = ""


class CustomsIn(BaseModel):
    """Bloque Aduana. Los códigos salen de las tablas de Aduana del SII."""

    sale_mode: int | None = None
    sale_clause: int | None = None
    clause_total: Decimal | None = None
    transport_route: int | None = None
    transport_name: str = ""
    carrier_rut: str = ""
    carrier_name: str = ""
    booking: str = ""
    operator: str = ""
    loading_port: int | None = None
    unloading_port: int | None = None
    tare: Decimal | None = None
    tare_unit: int | None = None
    gross_weight: Decimal | None = None
    gross_weight_unit: int | None = None
    net_weight: Decimal | None = None
    net_weight_unit: int | None = None
    total_items: int | None = None
    total_packages: int | None = None
    packages: list[PackageGroupIn] = []
    freight: Decimal | None = None
    insurance: Decimal | None = None
    receiver_country: int | None = None
    destination_country: int | None = None


class OtherCurrencyIn(BaseModel):
    """``<OtraMoneda>``: los mismos montos en otra moneda, normalmente pesos.

    El SII la exige en exportación —«(HED-3-834) Exportacion: seccion
    (OtraMoneda) obligatoria»— aunque el XSD la declare opcional. Sólo se indica
    el tipo de cambio; los montos los deriva el motor de los del documento, para
    que no puedan dejar de cuadrar con el total.
    """

    #: Pesos por unidad de la moneda del documento (TpoCambio).
    exchange_rate: Decimal = Field(gt=0, examples=["950.50"])
    #: Moneda de destino. Tabla de monedas de Aduana.
    currency: str = Field("PESO CL", min_length=1)


class ExportIssueRequest(BaseModel):
    """Emisión de factura (110) o nota (111/112) de exportación."""

    type: Literal[110, 111, 112]
    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn
    # TpoMoneda: valor literal del XSD, p.ej. "DOLAR USA" o "LIBRA EST".
    currency: str = Field(min_length=1, examples=["DOLAR USA", "LIBRA EST"])
    items: list[ExportItemIn] = Field(min_length=1)
    global_charges: list[GlobalDiscountIn] = []
    references: list[ReferenceIn] = []
    customs: CustomsIn | None = None
    other_currency: OtherCurrencyIn | None = None
    payment_mode: int | None = None  # FmaPagExp
    service_indicator: int | None = None
    # <Extranjero>: identificación del comprador de fuera de Chile.
    foreign_id: str = Field("", max_length=20)  # NumId
    receiver_nationality: int | None = None  # Nacionalidad: código de país de Aduana
    send: bool = True
    validate_xsd: bool = True


class ExportBatchItemIn(BaseModel):
    """Un documento de exportación dentro del lote."""

    type: Literal[110, 111, 112]
    issue_date: dt.date
    issuer: IssuerIn
    receiver: ReceiverIn
    currency: str = Field(min_length=1, examples=["DOLAR USA", "LIBRA EST"])
    items: list[ExportItemIn] = Field(min_length=1)
    global_charges: list[GlobalDiscountIn] = []
    references: list[BatchReferenceIn] = []
    customs: CustomsIn | None = None
    other_currency: OtherCurrencyIn | None = None
    payment_mode: int | None = None
    service_indicator: int | None = None
    foreign_id: str = Field("", max_length=20)
    receiver_nationality: int | None = None


class ExportBatchRequest(BaseModel):
    """N documentos de exportación en UN solo sobre.

    El set de certificación entrega cada set de exportación como un envío
    aparte, y dentro de él las notas referencian a la factura por posición.
    """

    documents: list[ExportBatchItemIn] = Field(min_length=1, max_length=MAX_BATCH_DOCUMENTS)
    send: bool = True
    validate_xsd: bool = True
