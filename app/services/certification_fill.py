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

from dte_chile.text import MAX_LENGTHS
from lxml import etree

_CL = ZoneInfo("America/Santiago")

#: Endpoints que emiten documentos (con emisor, fecha y referencias).
DOC_ENDPOINTS = {"issue-batch", "issue-export-batch", "issue-settlement-batch"}
#: Endpoints de libros.
BOOK_ENDPOINTS = {"books", "books/guides"}
#: El set de boletas. Va aparte porque su cuerpo lleva las boletas en
#: ``receipts`` y no en ``documents``, y porque la boleta no tiene receptor: el
#: comprador es anónimo.
RECEIPT_ENDPOINTS = {"boletas"}

#: Sets que pueden alimentar el Libro de Ventas, en orden de preferencia: se usa
#: **sólo el primero que exista**. Lo fija la hoja del set de pruebas:
#:
#:     CONSTRUYA EL LIBRO DE VENTAS CON LOS DOCUMENTOS CON QUE GENERO EL SET
#:     BASICO O EL SET DE FACTURA EXENTA, SEGUN CORRESPONDA. SI OBTUVO AMBOS
#:     SET, UTILICE LOS DOCUMENTOS DEL SET BASICO PARA CONSTRUIR EL LIBRO DE
#:     VENTAS.
#:
#: Antes se sumaban los documentos de cinco sets —básico, exenta, las dos
#: exportaciones y liquidación—, y el SII rechazó el set 5038171 dos veces con
#: «El Numero de Lineas de Resumen No Cuadra»: esperaba las 3 líneas del set
#: básico (33, 56, 61) y recibía 8. Que el sobre volviera LOK no lo desmentía:
#: LOK dice que el libro cuadra consigo mismo, no que tenga el contenido que el
#: set pide.
SALES_SETS = ("basico", "exenta")
#: Set cuyos documentos van al Libro de Guías.
GUIDE_SETS = ("guias",)

#: Qué le pasó a cada guía del set, por su posición en el sobre (1 = la primera).
#:
#: El instructivo del SII lo pide para el set 5038174: «EL CASO 2 CORRESPONDE A
#: UNA GUIA QUE SE FACTURO EN EL PERIODO» y «EL CASO 3 CORRESPONDE A UNA GUIA
#: ANULADA». No sale del sobre de guías —esas tres se emitieron iguales y el SII
#: las aceptó— sino del enunciado del caso.
#:
#: Sólo la anulación se anota. Que una guía se haya facturado **ya está dicho**
#: por su tipo de operación: el formato del Libro de Guías define el 1 como
#: «Operación constituye venta» y aclara al pie que se indica «cuando el
#: producto está facturado o se facturará posteriormente». El 2 es «ventas por
#: efectuar». No hay nada más que marcar.
#:
#: `MntModificado` NO sirve para esto: el formato dice «Si el campo
#: ANULADO/MODIFICADO **=3**, se anota el monto corregido», y el 3 es «productos
#: recibidos parcialmente». Usarlo para la guía facturada dejaba un
#: TotMntModificado sin ninguna guía con Anulado=3, y el SII respondió «El Monto
#: de Guías Modificadas No Cuadra».
#:
#: Los campos de referencia a la factura (TpoDocRef/FolioDocRef/FchDocRef)
#: tampoco: piden la factura concreta que absorbió la guía, y en este set no
#: existe tal factura. Inventarla sería declarar un documento que el SII no
#: tiene.
GUIDE_BOOK_CASES: dict[int, dict] = {
    3: {"voided": 2},  # anulada DESPUÉS de enviarla al SII: suma en TotGuiaAnulada
}

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
#: Tope de GiroRecep en el XSD del SII (40), la mitad que GiroEmis (80). En la
#: guía de traslado interno el emisor va también de receptor, así que un giro
#: que cabe en su propio campo no cabe en el del receptor.
_GIRO_RECEP = MAX_LENGTHS["GiroRecep"]

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
    elif endpoint in RECEIPT_ENDPOINTS:
        # Mismo criterio que los documentos: el emisor y la fecha los pone el
        # sistema. La referencia al caso NO se quita — en boletas el SII numera
        # los casos por su cuenta (CASO-1..5) y no por número de atención, así
        # que es dato del caso y no algo deducible.
        for boleta in salida.get("receipts", []):
            boleta.pop("issuer", None)
            boleta.pop("issue_date", None)
    elif endpoint in BOOK_ENDPOINTS:
        salida.pop("period", None)
        salida.pop("notification_folio", None)
        if kind in GENERATED_BOOKS:
            salida.pop("lines", None)
    return salida


# --------------------------------------------------------------------------- #
#  Emitir: lo que pone el sistema
# --------------------------------------------------------------------------- #


def _bultos(documents: list) -> list[str]:
    """Avisa de un grupo de bultos sin identificación del contenedor.

    El XSD declara `IdContainer` y `Sello` opcionales, pero el SII los exige en
    cuanto el documento informa `<TipoBultos>`: sin ellos devuelve «(HED-2-804)
    Exportacion : Campo obligatorio». Costó un sobre entero del set de
    exportación (2) —tres folios— descubrirlo, y el reparo sólo se ve DESPUÉS de
    enviar.

    Va como nota de la previa y no como bloqueo: es lo que el Servicio pidió en
    un caso concreto, no una regla que se pueda afirmar para todo despacho. Pero
    aparece donde se decide gastar folios, que es donde sirve.
    """
    campos = (("container_id", "Id. Container"), ("seal", "Sello"))
    avisos = []
    for n, doc in enumerate(documents, start=1):
        for bulto in (doc.get("customs") or {}).get("packages") or []:
            faltan = [txt for campo, txt in campos if not str(bulto.get(campo) or "").strip()]
            if faltan:
                avisos.append(
                    f"documento {n}: el grupo de bultos no informa {' ni '.join(faltan)}."
                    " El SII los exige cuando hay bultos, aunque el esquema los dé"
                    " por opcionales, y responde «(HED-2-804) Campo obligatorio»."
                )
    return avisos


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
        notas.extend(_bultos(salida.get("documents", [])))
        faltan = customer_service.issuer_missing(customer)
        if faltan:
            notas.append("faltan datos del emisor en la ficha del cliente: " + ", ".join(faltan))
    elif endpoint in RECEIPT_ENDPOINTS:
        from app.services import customer_service

        emisor = customer_service.issuer_block(customer)
        for boleta in salida.get("receipts", []):
            boleta["issuer"] = dict(emisor)
            boleta["issue_date"] = hoy.isoformat()
        faltan = customer_service.issuer_missing(customer)
        if faltan:
            notas.append("faltan datos del emisor en la ficha del cliente: " + ", ".join(faltan))
        # El plazo es lo que hace caro equivocarse aquí, y no se ve en ningún
        # otro sitio de la pantalla.
        notas.append(
            f"{len(salida.get('receipts', []))} boletas en un solo sobre. Al bajar el CAF"
            " corren 24 horas para enviarlo junto con el reporte de consumo de folios."
        )
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
    avisos: list[str] = []
    necesitan = 0
    siguiente = 0
    for doc in docs:
        tipo = doc.get("type")
        actual = (doc.get("receiver") or {}).get("rut")
        if tipo == 52 and doc.get("transfer_type") == _INTERNAL_TRANSFER:
            giro = emisor.get("activity") or ""
            doc["receiver"] = {
                "rut": emisor["rut"],
                "business_name": emisor.get("business_name") or "",
                "activity": giro[:_GIRO_RECEP],
                "address": emisor.get("address") or "",
                "commune": emisor.get("commune") or "",
                "city": emisor.get("city") or "",
            }
            if len(giro) > _GIRO_RECEP:
                avisos.append(
                    f"guía de traslado interno: el giro del emisor ({len(giro)} caracteres)"
                    f" se recortó a {_GIRO_RECEP} para GiroRecep, que es más corto que"
                    f" GiroEmis. Quedó «{giro[:_GIRO_RECEP]}»"
                )
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
        return avisos
    if not lista:
        avisos.append(
            f"sin receptores de prueba: {necesitan} documento(s) van al RUT del propio SII."
            " El instructivo pide «un Rut receptor de un cliente existente» y «RUT"
            " distintos para las distintas facturas»; configúralos en el expediente."
        )
    elif len(lista) < necesitan:
        avisos.append(
            f"{necesitan} documentos y {len(lista)} receptor(es) de prueba: se repiten."
            " El instructivo pide un RUT distinto por factura."
        )
    return avisos


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
        en_pesos = {
            "exempt_amount": _entero(_texto(otra, "MntExeOtrMnda")),
            "net_amount": _entero(_texto(otra, "MntNetoOtrMnda")),
            "vat_amount": _entero(_texto(otra, "IVAOtrMnda")),
            "total_amount": _entero(_texto(otra, "MntTotOtrMnda")),
        }
        if any(en_pesos.values()):
            linea.update(en_pesos)
        else:
            # Con forma de pago S/PAGO (21) el SII EXIGE que los montos en otra
            # moneda vayan en cero —es la regla HED-1-803—, así que ese cero no
            # dice que el documento no valga nada: dice que ahí no se informa.
            # Copiarlo al libro dejaba la línea en cero y el Servicio lo reportó:
            # «Reparo en Detalle - Falta [MntTotal MntPeriodo] T:[110]-F:[10]».
            # El libro va en pesos, así que se convierte con el tipo de cambio
            # que el propio documento declara.
            cambio = Decimal(_texto(otra, "TpoCambio") or 0)
            if cambio > 0:
                for campo, etiqueta in (
                    ("exempt_amount", "MntExe"),
                    ("net_amount", "MntNeto"),
                    ("vat_amount", "IVA"),
                    ("total_amount", "MntTotal"),
                ):
                    bruto = _texto(totales, etiqueta)
                    if bruto:
                        linea[campo] = _entero(str(Decimal(bruto) * cambio))

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
    """Las líneas de un libro a partir de un sobre ya emitido.

    En el Libro de Guías se aplica además lo que el caso dice de cada guía
    —cuál quedó anulada—, que no está en el sobre: las tres se emitieron iguales
    y el SII las aceptó. Ver ``GUIDE_BOOK_CASES``.
    """
    if kind != "libro_guias":
        return [_sales_line(d) for d in _documentos(xml)]
    lineas = []
    for posicion, doc in enumerate(_documentos(xml), start=1):
        linea = _guide_line(doc)
        caso = GUIDE_BOOK_CASES.get(posicion, {})
        if caso.get("voided"):
            linea["voided"] = caso["voided"]
            # Una guía anulada no aporta monto al período: el SII la cuenta
            # aparte, en TotGuiaAnulada, y sumarla descuadraría el resumen.
            linea["net_amount"] = linea["vat_amount"] = linea["total_amount"] = 0
        lineas.append(linea)
    return lineas


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
    candidatos = {
        set_kind: db.query(CertificationSet)
        .filter(CertificationSet.customer_id == customer.id, CertificationSet.kind == set_kind)
        .one_or_none()
        for set_kind in GENERATED_BOOKS[kind]
    }
    if kind == "libro_ventas":
        # Un solo set: el primero que exista de SALES_SETS.
        elegido = next((k for k, s in candidatos.items() if s is not None), None)
        candidatos = {elegido: candidatos[elegido]} if elegido else {" o ".join(SALES_SETS): None}
    for set_kind, s in candidatos.items():
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
