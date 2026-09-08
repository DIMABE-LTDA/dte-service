"""Schemas del Libro de Compras y Ventas (IECV)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.dte import SubmissionResultOut


class NonRecoverableVatIn(BaseModel):
    """IVA sin derecho a crédito (<IVANoRec>), con su motivo.

    1 operaciones no gravadas · 2 fuera de plazo · 3 gastos rechazados
    · 4 entrega gratuita · 9 otros.
    """

    code: Literal[1, 2, 3, 4, 9]
    amount: Decimal = Field(ge=0)


class BookLineIn(BaseModel):
    """Una línea (documento) del libro.

    **Los montos del IECV van en pesos.** Un documento emitido en moneda
    extranjera —las exportaciones, tipos 110/111/112— se declara con su
    ``currency`` y su ``exchange_rate``, y el servicio lo convierte. Sin eso, una
    factura de USD 15,40 entraba al libro como 15 pesos.
    """

    doc_type: int
    folio: int
    date: dt.date
    rut: str
    business_name: str
    # Decimal, no int: en moneda extranjera los montos llevan decimales. Sin
    # ``currency`` tienen que ser enteros, porque ya son pesos.
    exempt_amount: Decimal = Decimal(0)
    net_amount: Decimal = Decimal(0)
    vat_amount: Decimal = Decimal(0)
    total_amount: Decimal = Decimal(0)
    voided: bool = False
    # --- Sólo Libro de Compras ---
    common_use_vat: Decimal = Field(Decimal(0), ge=0)
    non_recoverable_vat: list[NonRecoverableVatIn] = []
    retained_total_vat: Decimal = Field(Decimal(0), ge=0)
    non_billable_amount: Decimal = Decimal(0)
    # Documento que la nota de crédito o débito modifica (sólo libro de ventas).
    ref_doc_type: int | None = None
    ref_folio: int | None = None
    # Comisiones de la liquidación factura: se restan del total del documento.
    commission_net: Decimal = Decimal(0)
    commission_exempt: Decimal = Decimal(0)
    commission_vat: Decimal = Decimal(0)

    # --- Moneda extranjera (exportaciones) ---
    # Etiqueta del documento, tal como va en su <TpoMoneda>: "DOLAR USA", etc.
    # Vacío = el documento ya está en pesos y no se convierte nada.
    currency: str | None = None
    # Tipo de cambio observado del día del documento, en pesos por unidad. Va
    # por línea y no por libro: cada documento se convierte al de SU fecha.
    exchange_rate: Decimal | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _check_currency(self) -> BookLineIn:
        montos = (
            self.exempt_amount,
            self.net_amount,
            self.vat_amount,
            self.total_amount,
            self.common_use_vat,
            self.retained_total_vat,
            self.non_billable_amount,
            self.commission_net,
            self.commission_exempt,
            self.commission_vat,
            *(e.amount for e in self.non_recoverable_vat),
        )
        if self.currency and self.exchange_rate is None:
            raise ValueError(
                f"la línea {self.doc_type}/{self.folio} declara moneda"
                f" '{self.currency}' pero no su tipo de cambio: el IECV es en pesos"
            )
        if self.exchange_rate is not None and not self.currency:
            raise ValueError(
                f"la línea {self.doc_type}/{self.folio} trae tipo de cambio sin decir de qué moneda"
            )
        if not self.currency and any(m != m.to_integral_value() for m in montos):
            # Un decimal sin moneda declarada es justo el error que se busca
            # evitar: se emitiría "15.40" en un campo que el XSD quiere entero.
            raise ValueError(
                f"la línea {self.doc_type}/{self.folio} trae montos con decimales"
                " sin declarar la moneda: en pesos deben ser enteros"
            )
        return self


class BookRequest(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$", examples=["2026-05"])  # AAAA-MM
    operation_type: Literal["VENTA", "COMPRA"] = "VENTA"
    # Factor de proporcionalidad del IVA de uso común (sólo Libro de Compras).
    proportionality_factor: float | None = Field(None, ge=0, le=1)
    # MENSUAL declara TODO el período y el SII lo contrasta contra los DTE que
    # tiene registrados. ESPECIAL es el que pide una notificación puntual —el
    # caso del set de certificación—, y ahí FolioNotificacion es su número de
    # atención.
    book_type: Literal["MENSUAL", "ESPECIAL", "RECTIFICA", "AJUSTE"] = "MENSUAL"
    notification_folio: int = Field(1, ge=1)
    lines: list[BookLineIn] = Field(min_length=1)
    send: bool = True  # subir el libro al SII (el set de certificación lo exige)
    validate_xsd: bool = True  # validar contra LibroCV_v10.xsd antes de enviar


class BookResponse(BaseModel):
    period: str
    operation_type: str
    xml_base64: str
    submission: SubmissionResultOut | None = None


class GuideBookLineIn(BaseModel):
    """Una guía dentro del Libro de Guías de Despacho."""

    folio: int
    date: dt.date | None = None
    transfer_type: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9] | None = None  # TpoOper
    receiver_rut: str = ""
    receiver_name: str = ""
    net_amount: int = 0
    vat_amount: int = 0
    total_amount: int = 0
    vat_rate: int = 19
    # 1 = anulada antes de enviarla al SII, 2 = después, 3 = recepción parcial.
    voided: Literal[1, 2, 3] | None = None
    # Guía facturada en el período: monto absorbido + referencia a la factura.
    modified_amount: int | None = None
    ref_doc_type: int | None = None
    ref_folio: int | None = None
    ref_date: dt.date | None = None


class GuideBookRequest(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$", examples=["2026-11"])  # AAAA-MM
    # Folio de la notificación con que el SII pide el libro; en certificación,
    # el número de atención del set. El XSD exige un entero positivo.
    notification_folio: int = Field(1, ge=1)
    submission_type: Literal["TOTAL", "PARCIAL", "FINAL", "AJUSTE"] = "TOTAL"
    lines: list[GuideBookLineIn] = Field(min_length=1)
    send: bool = True
    validate_xsd: bool = True


class GuideBookResponse(BaseModel):
    period: str
    xml_base64: str
    submission: SubmissionResultOut | None = None
