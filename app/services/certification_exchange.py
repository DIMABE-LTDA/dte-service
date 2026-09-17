"""Paso 4 de la certificación: responder el set de intercambio del SII.

El SII entrega un EnvioDTE «como si hubiera sido recibido en su casilla de
intercambio» y pide construir la Respuesta de Intercambio y la Recepción de
Mercaderías. Son tres archivos, con el formato del «Mensaje de Respuesta a
DTE»:

1. **Acuse de recibo** (RespuestaDTE con RecepcionEnvio): el envío y cada DTE.
2. **Recibo de mercaderías** (EnvioRecibos, Ley 19.983): sólo lo recibido.
3. **Resultado comercial** (RespuestaDTE con ResultadoDTE): aceptación o
   rechazo de cada DTE.

El set trae a propósito una factura dirigida a otro receptor. El motor la
distingue sola por su RUT receptor: se informa no recibida, se rechaza y no
lleva recibo.

Se firma con el certificado del cliente, y cada archivo se valida contra su XSD
antes de entregarlo.
"""

from __future__ import annotations

import base64
import datetime as dt
from zoneinfo import ZoneInfo

from dte_chile.exchange import (
    Contact,
    addressed_to,
    build_receipt_acknowledgment,
    build_receipts_envelope,
    build_result_response,
    parse_envelope,
    serialize,
)
from dte_chile.validation import Validator

from app.core.config import get_settings
from app.db.models import Customer
from app.services import customer_service

_CL = ZoneInfo("America/Santiago")


class ExchangeError(Exception):
    """El archivo recibido no se puede responder (se mapea a 4xx)."""


def respond(customer: Customer, cert, file_name: str, envelope_xml: bytes) -> dict:
    """Las tres respuestas al set de intercambio, firmadas y validadas."""
    try:
        envio = parse_envelope(envelope_xml, file_name or "envio.xml")
    except Exception as ex:  # noqa: BLE001 — cualquier XML ilegible es un 4xx
        raise ExchangeError(f"el archivo no es un EnvioDTE legible: {ex}") from ex
    if not envio.documents:
        raise ExchangeError("el envío no trae documentos")

    ahora = dt.datetime.now(_CL).replace(microsecond=0, tzinfo=None)
    perfil = customer_service.issuer_profile(customer)
    contacto = Contact(name=(perfil.get("legal_name") or customer.name)[:40])
    recinto = ", ".join(p for p in (perfil.get("address"), perfil.get("commune")) if p)[:80]
    rut = customer.rut

    archivos = [
        (
            "acuse_recibo",
            f"RespuestaEnvio_{rut}.xml",
            build_receipt_acknowledgment(
                envio, cert, ahora, contact=contacto, response_id=1, responder_rut=rut
            ),
        ),
        (
            "recibo_mercaderias",
            f"EnvioRecibos_{rut}.xml",
            build_receipts_envelope(
                envio,
                cert,
                ahora,
                location=recinto or "BODEGA",
                contact=contacto,
                responder_rut=rut,
            ),
        ),
        (
            "resultado_comercial",
            f"ResultadoDTE_{rut}.xml",
            build_result_response(
                envio, cert, ahora, contact=contacto, response_id=2, responder_rut=rut
            ),
        ),
    ]

    validador = Validator(get_settings().schemas_dir)
    salida = []
    for kind, nombre, elemento in archivos:
        xml = serialize(elemento)
        validador.validate(xml)
        salida.append(
            {"kind": kind, "name": nombre, "xml_base64": base64.b64encode(xml).decode("ascii")}
        )

    return {
        "envelope_id": envio.set_dte_id,
        "issuer_rut": envio.issuer_rut,
        "documents": [
            {
                "doc_type": d.doc_type,
                "folio": d.folio,
                "receiver_rut": d.receiver_rut,
                "total_amount": d.total_amount,
                "received": addressed_to(d, rut),
            }
            for d in envio.documents
        ],
        "files": salida,
    }
