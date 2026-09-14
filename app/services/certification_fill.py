"""Lo que el sistema pone en cada set: nada de esto se escribe a mano.

Una definición de set guarda sólo los datos del CASO tal como los entrega el
SII —ítems, cantidades, precios, receptores extranjeros, comisiones—. Todo lo
que depende del contribuyente o del momento lo completa el sistema al emitir,
y así la misma definición sirve para cualquier cliente y cualquier día.

Lo que se completa, y de dónde sale. Cada regla viene del «Instructivo para la
construcción de documentos con los datos del set de pruebas» del SII:

- **Emisor**: de la ficha del cliente. Escrito en cada definición, clonar a
  otro contribuyente emitía como el anterior.
- **Fecha de emisión**: la del día («Agregue fecha del día»). Fija en la
  definición, un cliente que certifique meses después quedaría con documentos
  anteriores a su CAF, que el SII rechaza.
- **Referencia al caso**: primera línea de referencia de cada documento,
  ``SET`` / ``CASO {número de atención}-{n}``. Las demás referencias van desde
  la línea 2. Ningún documento de la primera certificación la llevaba.
- **Fecha de las referencias externas** (DUS, MIC, AWB…): la de emisión.
- **Receptores**: «un Rut receptor de un cliente existente» y «RUT distintos
  para las distintas facturas». Las facturas y guías de venta que traen el RUT
  genérico del SII reciben los receptores de prueba del cliente, uno distinto
  cada una; las notas heredan el del documento que modifican; la guía de
  traslado interno va al propio emisor. Importador, mandante y vendedor son
  datos del caso y no se tocan.
- **Libros**: el período es el mes de emisión y el folio de notificación sale
  del número de atención del set. Las líneas de los libros de ventas y de guías
  se arman con los documentos del set que el SII aceptó («incorporando sólo la
  información de los documentos que son parte de sus SET de prueba»): escritas
  a mano quedaban apuntando a folios viejos en cuanto se reemitía un set.

`strip` hace el camino inverso: deja una definición con sólo los datos del
caso, para que lo que se guarda no pueda contradecir lo que el sistema pone.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from lxml import etree

_CL = ZoneInfo("America/Santiago")

#: Endpoints que emiten documentos (con emisor, fecha y referencias).
DOC_ENDPOINTS = {"issue-batch", "issue-export-batch", "issue-settlement-batch"}
#: Endpoints de libros.
BOOK_ENDPOINTS = {"books", "books/guides"}

#: Sets cuyos documentos van al Libro de Ventas. Es la composición del libro
#: que el SII aceptó (LOK): facturas, exentas, exportación y liquidación con
#: sus notas. La factura de compra (46) es una compra y las guías tienen libro
#: propio.
SALES_SETS = ("basico", "exenta", "exportacion_1", "exportacion_2", "liquidacion")
#: Set cuyos documentos van al Libro de Guías.
GUIDE_SETS = ("guias",)

#: Libros cuyas líneas se generan; el de compras es dato del caso (sus
#: documentos los entrega el SII en el propio set).
GENERATED_BOOKS = {"libro_ventas": SALES_SETS, "libro_guias": GUIDE_SETS}

#: El RUT del SII: el receptor de relleno que traían las definiciones.
SII_RUT = "60803000-K"
#: Documentos que llevan como receptor a un cliente del contribuyente.
_OWN_CUSTOMER_DOCS = {33, 34, 52}
#: Notas nacionales: heredan el receptor del documento que modifican.
_NATIONAL_NOTES = {56, 61}
#: IndTraslado 5: traslado interno. No hay venta: el receptor es el emisor.
_INTERNAL_TRANSFER = 5

#: Notas: en el libro declaran el documento que modifican. Las de exportación
#: (111/112) no lo llevaban en el libro que el SII aceptó.
_NOTES_WITH_REF = {56, 61}


def today() -> dt.date:
    """Hoy en Chile: la fecha del documento es la del SII, no la del servidor."""
    return dt.datetime.now(_CL).date()


def case_reason(code: str, n: int) -> str:
    """El texto que el SII espera en RazonRef: «CASO 5038170-1»."""
    return f"CASO {code}-{n}"


# --------------------------------------------------------------------------- #
#  Guardar: sólo datos del caso
# --------------------------------------------------------------------------- #


def strip(endpoint: str, kind: str, payload: dict) -> dict:
    """Quita de una definición todo lo que completa el sistema.

    Se aplica al guardar, importar y clonar. Así una definición no puede traer
    un emisor, una fecha o un número de atención que contradiga la ficha.
    """
    salida = copy.deepcopy(payload or {})
    if endpoint in DOC_ENDPOINTS:
        for doc in salida.get("documents", []):
            doc.pop("issuer", None)
            doc.pop("issue_date", None)
            refs = []
            for ref in doc.get("references", []) or []:
                if str(ref.get("doc_type")) == "SET":
                    continue  # la pone el sistema con el número de atención
                if ref.get("batch_index") is None:
                    ref.pop("date", None)  # externa: toma la fecha de emisión
                refs.append(ref)
            if refs:
                doc["references"] = refs
            else:
                doc.pop("references", None)
    elif endpoint in BOOK_ENDPOINTS:
        salida.pop("period", None)
        salida.pop("notification_folio", None)
        if kind in GENERATED_BOOKS:
            salida.pop("lines", None)
    return salida


# --------------------------------------------------------------------------- #
#  Emitir: lo que pone el sistema
# --------------------------------------------------------------------------- #


def fill(db, customer, cert_set, endpoint: str, payload: dict, *, hoy=None) -> tuple[dict, list]:
    """La definición completa, lista para emitir, y lo que conviene avisar.

    Devuelve también notas para quien revisa: de dónde salió cada cosa que no
    estaba en la definición, o por qué falta.
    """
    from app.services import customer_service

    hoy = hoy or today()
    salida = strip(endpoint, cert_set.kind or "", payload)
    notas: list[str] = []

    if endpoint in DOC_ENDPOINTS:
        emisor = customer_service.issuer_block(customer)
        for n, doc in enumerate(salida.get("documents", []), start=1):
            doc["issuer"] = dict(emisor)
            doc["issue_date"] = hoy.isoformat()
            # El número del caso sale del número de atención del set y la
            # posición del documento, en el orden en que el SII entregó los
            # casos. Si el set los numera de otra forma, la definición puede
            # traer su propio `case`.
            caso = doc.pop("case", None)
            refs = [
                {
                    "doc_type": "SET",
                    "folio": "0",
                    "date": hoy.isoformat(),
                    "reason": f"CASO {caso}" if caso else case_reason(cert_set.code, n),
                }
            ]
            for ref in doc.get("references", []) or []:
                if ref.get("batch_index") is None:
                    ref["date"] = hoy.isoformat()
                refs.append(ref)
            doc["references"] = refs
        notas.extend(_receivers(customer, emisor, salida.get("documents", [])))
        faltan = customer_service.issuer_missing(customer)
        if faltan:
            notas.append("faltan datos del emisor en la ficha del cliente: " + ", ".join(faltan))
    elif endpoint in BOOK_ENDPOINTS:
        salida["period"] = hoy.strftime("%Y-%m")
        if str(cert_set.code).isdigit():
            salida["notification_folio"] = int(cert_set.code)
        kind = cert_set.kind or ""
        if kind in GENERATED_BOOKS:
            lineas, avisos = book_lines(db, customer, kind)
            salida["lines"] = lineas
            notas.extend(avisos)
            if lineas:
                # El período lo fijan los documentos: todos los del set deben
                # ser del mismo mes, y el libro declara ese mes.
                meses = sorted({str(line["date"])[:7] for line in lineas if line.get("date")})
                if meses:
                    salida["period"] = meses[-1]
                if len(meses) > 1:
                    notas.append(
                        "los documentos son de meses distintos (" + ", ".join(meses) + "):"
                        " el SII pide que todos los del set sean del mismo período"
                    )
    return salida, notas


def _receivers(customer, emisor: dict, docs: list[dict]) -> list[str]:
    """Pone a cada documento su receptor según el instructivo, y avisa.

    Un solo recorrido basta: en los sets las notas apuntan hacia atrás —la ND a
    la NC, la NC a la factura—, así que cuando se llega a una nota su documento
    ya tiene receptor.
    """
    lista = [r for r in (customer.cert_receivers or []) if r.get("rut")]
    necesitan = 0
    siguiente = 0
    for doc in docs:
        tipo = doc.get("type")
        actual = (doc.get("receiver") or {}).get("rut")
        if tipo == 52 and doc.get("transfer_type") == _INTERNAL_TRANSFER:
            doc["receiver"] = {
                "rut": emisor["rut"],
                "business_name": emisor.get("business_name") or "",
                "activity": emisor.get("activity") or "",
                "address": emisor.get("address") or "",
                "commune": emisor.get("commune") or "",
                "city": emisor.get("city") or "",
            }
        elif tipo in _OWN_CUSTOMER_DOCS and actual in (SII_RUT, None):
            necesitan += 1
            if lista:
                doc["receiver"] = dict(lista[siguiente % len(lista)])
                siguiente += 1
        elif tipo in _NATIONAL_NOTES and actual in (SII_RUT, None):
            ref = next((r for r in doc.get("references", []) if r.get("batch_index")), None)
            if ref and 0 < ref["batch_index"] <= len(docs):
                doc["receiver"] = dict(docs[ref["batch_index"] - 1].get("receiver") or {})

    if not necesitan:
        return []
    if not lista:
        return [
            f"sin receptores de prueba: {necesitan} documento(s) van al RUT del propio SII."
            " El instructivo pide «un Rut receptor de un cliente existente» y «RUT"
            " distintos para las distintas facturas»; configúralos en el expediente."
        ]
    if len(lista) < necesitan:
        return [
            f"{necesitan} documentos y {len(lista)} receptor(es) de prueba: se repiten."
            " El instructivo pide un RUT distinto por factura."
        ]
    return []


# --------------------------------------------------------------------------- #
#  Líneas de los libros, desde los documentos aceptados
# --------------------------------------------------------------------------- #


def _local(tag) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _hijo(nodo, nombre: str):
    for h in nodo.iter():
        if _local(h.tag) == nombre:
            return h
    return None


def _texto(nodo, nombre: str) -> str | None:
    h = _hijo(nodo, nombre)
    return h.text.strip() if h is not None and h.text else None


def _requerido(nodo, nombre: str) -> str:
    """Un dato sin el cual el documento no es un documento: tipo, folio."""
    valor = _texto(nodo, nombre)
    if not valor:
        raise ValueError(f"el documento no trae {nombre}")
    return valor


def _entero(valor: str | None) -> int:
    if not valor:
        return 0
    return int(Decimal(valor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _documentos(xml: bytes) -> list:
    """Los documentos de un sobre, en orden: factura, liquidación o exportación."""
    raiz = etree.fromstring(xml)
    return [
        n for n in raiz.iter() if _local(n.tag) in ("Documento", "Liquidacion", "Exportaciones")
    ]


def _sales_line(doc) -> dict:
    """Una línea del Libro de Ventas con los datos del propio documento.

    Montos del bloque Totales, redondeados: así los declaró el libro que el SII
    aceptó, incluidas las exportaciones en su moneda. Si el documento trae su
    equivalente en pesos (OtraMoneda en PESO CL), se usa ese.
    """
    iddoc, receptor, totales = _hijo(doc, "IdDoc"), _hijo(doc, "Receptor"), _hijo(doc, "Totales")
    tipo = int(_requerido(iddoc, "TipoDTE"))
    linea = {
        "doc_type": tipo,
        "folio": int(_requerido(iddoc, "Folio")),
        "date": _texto(iddoc, "FchEmis"),
        "rut": _texto(receptor, "RUTRecep") or "",
        "business_name": (_texto(receptor, "RznSocRecep") or "")[:50],
        "exempt_amount": _entero(_texto(totales, "MntExe")),
        "net_amount": _entero(_texto(totales, "MntNeto")),
        "vat_amount": _entero(_texto(totales, "IVA")),
        "total_amount": _entero(_texto(totales, "MntTotal")),
    }

    otra = _hijo(doc, "OtraMoneda")
    if otra is not None and (_texto(otra, "TpoMoneda") or "").upper().startswith("PESO"):
        linea["exempt_amount"] = _entero(_texto(otra, "MntExeOtrMnda"))
        linea["net_amount"] = _entero(_texto(otra, "MntNetoOtrMnda"))
        linea["vat_amount"] = _entero(_texto(otra, "IVAOtrMnda"))
        linea["total_amount"] = _entero(_texto(otra, "MntTotOtrMnda"))

    # Comisiones de la liquidación: los totales, que están dentro de <Totales>.
    if totales is not None:
        for com in totales:
            if _local(com.tag) == "Comisiones" and len(com):
                linea["commission_net"] = _entero(_texto(com, "ValComNeto"))
                linea["commission_exempt"] = _entero(_texto(com, "ValComExe"))
                linea["commission_vat"] = _entero(_texto(com, "ValComIVA"))

    if tipo in _NOTES_WITH_REF:
        for ref in doc.iter():
            if _local(ref.tag) != "Referencia":
                continue
            tpo = _texto(ref, "TpoDocRef") or ""
            if tpo.isdigit():
                linea["ref_doc_type"] = int(tpo)
                linea["ref_folio"] = int(_texto(ref, "FolioRef") or 0)
                break
    return linea


def _guide_line(doc) -> dict:
    """Una línea del Libro de Guías con los datos de la guía."""
    iddoc, receptor, totales = _hijo(doc, "IdDoc"), _hijo(doc, "Receptor"), _hijo(doc, "Totales")
    linea = {
        "folio": int(_requerido(iddoc, "Folio")),
        "date": _texto(iddoc, "FchEmis"),
        "receiver_rut": _texto(receptor, "RUTRecep") or "",
        "receiver_name": (_texto(receptor, "RznSocRecep") or "")[:50],
        "net_amount": _entero(_texto(totales, "MntNeto")) if totales is not None else 0,
        "vat_amount": _entero(_texto(totales, "IVA")) if totales is not None else 0,
        "total_amount": _entero(_texto(totales, "MntTotal")) if totales is not None else 0,
    }
    traslado = _texto(iddoc, "IndTraslado")
    if traslado and traslado.isdigit():
        linea["transfer_type"] = int(traslado)
    return linea


def lines_from_envelope(xml: bytes, kind: str) -> list[dict]:
    """Las líneas de un libro a partir de un sobre ya emitido."""
    hacer = _guide_line if kind == "libro_guias" else _sales_line
    return [hacer(d) for d in _documentos(xml)]


def book_lines(db, customer, kind: str) -> tuple[list[dict], list[str]]:
    """Las líneas de un libro con los documentos aceptados de sus sets.

    De cada set se toma el último envío que el SII aceptó: es el que tiene los
    folios que el Servicio conoce. Un set sin envío aceptado no aporta nada, y
    se avisa: declarar documentos rechazados es declarar folios que el SII no
    tiene.
    """
    from app.db.models import CertificationSet, CertificationSubmission
    from app.services.certification_service import entregado, envelope

    lineas: list[dict] = []
    sin_aceptar: list[str] = []
    for set_kind in GENERATED_BOOKS[kind]:
        s = (
            db.query(CertificationSet)
            .filter(CertificationSet.customer_id == customer.id, CertificationSet.kind == set_kind)
            .one_or_none()
        )
        if s is None:
            sin_aceptar.append(set_kind)
            continue
        envios = (
            db.query(CertificationSubmission)
            .filter(CertificationSubmission.set_id == s.id)
            .order_by(CertificationSubmission.id.desc())
            .all()
        )
        aceptado = next((e for e in envios if entregado(e) and e.envelope_encrypted), None)
        if aceptado is None:
            sin_aceptar.append(f"{set_kind} ({s.code})")
            continue
        lineas.extend(lines_from_envelope(envelope(aceptado), kind))

    avisos = []
    if sin_aceptar:
        avisos.append(
            "el libro se arma con los documentos aceptados de cada set; todavía sin"
            " envío aceptado: " + ", ".join(sin_aceptar)
        )
    return lineas, avisos
