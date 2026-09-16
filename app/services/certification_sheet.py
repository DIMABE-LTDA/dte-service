"""Lee el archivo del set de pruebas que entrega el SII y arma las definiciones.

El SII entrega a cada contribuyente un texto con sus casos —ítems, cantidades,
precios, descuentos, referencias— y un segundo archivo con el set de boletas.
Transcribirlos a mano fue la fuente de casi todos los rechazos de la primera
certificación: tildes quitadas, un anticipo con el tipo equivocado, líneas de
exportación sin cantidad ni precio, un pasaporte que faltaba. Este módulo los
lee y devuelve las definiciones en el mismo formato que carga ``/import``.

Tres reglas lo gobiernan:

1. **Los valores se copian literales.** Nombres con sus tildes y eñes, montos
   tal cual. Sólo para *reconocer* palabras clave se compara en mayúsculas y
   sin tildes; nunca se guarda un valor normalizado. El instructivo del SII lo
   exige («debe incluir acentos, ñ u otros que se indiquen») y quitarlas costó
   siete rechazos.
2. **Dentro de un caso no se adivina.** Una línea que no se reconoce detiene la
   lectura con su número de línea. Es preferible a emitir un documento que
   ignoró un dato del enunciado: eso lo descubre el SII, con los folios ya
   gastados.
3. **Lo que la hoja no trae se completa con valores declarados aquí**, cada uno
   con su porqué: el receptor extranjero, el mandante, el proveedor, el
   contenedor, el tipo de cambio. Ver «Datos que la hoja no trae».

El texto informativo fuera de los casos —«IMPORTANTE», «INSTRUCCIONES AL
CONTRIBUYENTE»— no se interpreta, pero se devuelve como notas para mostrarlo.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from dte_chile.customs_codes import (
    COUNTRIES,
    MEASURE_UNITS,
    PACKAGE_TYPES,
    PAYMENT_MODES,
    PORTS,
    SALE_CLAUSES,
    SALE_MODES,
    TRANSPORT_ROUTES,
    code_for,
)

from app.services.certification_template import _IMPORTADOR, _MANDANTE


class SheetError(ValueError):
    """El archivo trae algo que el lector no sabe interpretar."""

    def __init__(self, mensaje: str, linea: int | None = None):
        self.linea = linea
        super().__init__(f"línea {linea}: {mensaje}" if linea else mensaje)


@dataclass
class Sheet:
    """Lo leído: las definiciones por set y el texto que no se interpretó."""

    sets: dict[str, dict] = field(default_factory=dict)
    notas: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Datos que la hoja no trae
# --------------------------------------------------------------------------- #

#: Vendedor de la factura de compra. En ese documento quien emite es el
#: comprador, así que el receptor es el proveedor; la hoja no lo identifica.
PROVEEDOR_FACTURA_COMPRA = {
    "rut": "17099910-K",
    "business_name": "PROVEEDOR EJEMPLO LIMITADA",
    "activity": "Venta por mayor materiales construccion",
    "address": "Calle Proveedor 456",
    "commune": "Providencia",
    "city": "Santiago",
}

#: Emisor de los documentos del libro de compras. El instructivo pide «Agregar
#: datos del emisor del documento para lo cual se deberán usar Rut válidos».
PROVEEDOR_LIBRO_COMPRAS = {"rut": "76158145-7", "business_name": "PROVEEDOR DE PRUEBA SPA"}

#: Contenedor y sello. El SII los exige cuando el bulto es un contenedor —«(HED-2-804)
#: Exportacion : Campo obligatorio»— aunque el XSD los declare opcionales.
CONTENEDOR = {"container_id": "TCLU1234567", "seal": "SL-4471209"}

#: Tipo de cambio de referencia para OtraMoneda, que el SII exige en exportación
#: («(HED-3-834) seccion (OtraMoneda) obligatoria»). La hoja no lo da y no es
#: dato que el Servicio compare: son los que usaron los sets aprobados.
TIPO_DE_CAMBIO = {"DOLAR USA": "940.91", "LIBRA EST": "1265", "EURO": "1015"}

#: Folio de los documentos externos que referencia una exportación (DUS, AWB,
#: resolución SNA, MIC). La hoja pide la referencia pero no su número.
FOLIO_EXTERNO = "1"

#: Pasaporte del huésped en una factura de hotelería. El SII exige «2 Linea(s) de
#: Referencia»: la del caso y la del pasaporte (813). La hoja no da el número.
PASAPORTE = "C01X00T47"

#: Destino de la guía de traslado interno: otra bodega de la propia empresa.
BODEGA_DESTINO = "BODEGA 2"


# --------------------------------------------------------------------------- #
#  Vocabulario de la hoja
# --------------------------------------------------------------------------- #

#: Nombre del set en el encabezado → kind. Se prueba en orden: los más
#: específicos primero, porque «BASICO» aparece en varios.
_SETS: list[tuple[str, str]] = [
    ("BASICO DOCUMENTOS DE EXPORTACION (1)", "exportacion_1"),
    ("BASICO DOCUMENTOS DE EXPORTACION (2)", "exportacion_2"),
    ("BASICO LIQUIDACIONES", "liquidacion"),
    ("BASICO CASO GENERAL DE EMISOR DE FACTURA DE COMPRA", "factura_compra"),
    ("LIBRO DE VENTAS", "libro_ventas"),
    ("LIBRO DE COMPRAS", "libro_compras"),
    ("LIBRO DE GUIAS", "libro_guias"),
    ("GUIA DE DESPACHO", "guias"),
    ("FACTURA EXENTA", "exenta"),
    ("BASICO", "basico"),
]

_ENDPOINTS = {
    "basico": "issue-batch",
    "guias": "issue-batch",
    "exenta": "issue-batch",
    "factura_compra": "issue-batch",
    "exportacion_1": "issue-export-batch",
    "exportacion_2": "issue-export-batch",
    "liquidacion": "issue-settlement-batch",
    "libro_ventas": "books",
    "libro_compras": "books",
    "libro_guias": "books/guides",
    "boletas": "boletas",
}

_DOCUMENTOS = {
    "FACTURA ELECTRONICA": 33,
    "FACTURA NO AFECTA O EXENTA ELECTRONICA": 34,
    "FACTURA DE COMPRA ELECTRONICA": 46,
    "GUIA DE DESPACHO": 52,
    "GUIA DE DESPACHO ELECTRONICA": 52,
    "NOTA DE DEBITO ELECTRONICA": 56,
    "NOTA DE CREDITO ELECTRONICA": 61,
    "FACTURA DE EXPORTACION ELECTRONICA": 110,
    "NOTA DE DEBITO DE EXPORTACION ELECTRONICA": 111,
    "NOTA DE CREDITO DE EXPORTACION ELECTRONICA": 112,
    "LIQUIDACION FACTURA ELECTRONICA": 43,
}

_EXPORTACION = {110, 111, 112}

#: Documentos del libro de compras (formato IECV, 4.2).
_DOCUMENTOS_COMPRA = {
    "FACTURA": 30,
    "FACTURA ELECTRONICA": 33,
    "FACTURA DE COMPRA": 45,
    "FACTURA DE COMPRA ELECTRONICA": 46,
    "NOTA DE DEBITO": 55,
    "NOTA DE DEBITO ELECTRONICA": 56,
    "NOTA DE CREDITO": 60,
    "NOTA DE CREDITO ELECTRONICA": 61,
}

#: Referencias externas de exportación → (TpoDocRef, RazonRef). Códigos del
#: formato DTE: 807 DUS, 809 AWB, 810 MIC/DTA, 812 resolución SNA.
_REFERENCIAS_EXTERNAS = {
    "DUS": (807, "DUS"),
    "AWB": (809, "AWB"),
    "MIC (MANIFIESTO INTERNACIONAL)": (810, "MIC"),
    "MIC": (810, "MIC"),
    "RESOLUCION SNA": (812, "RESOLUCION SNA"),
}
_RESOLUCION_SNA = 812
_REF_PASAPORTE = 813

#: IndServicio: 3 servicios calificados por Aduana, 4 hotelería.
_SERVICIOS, _HOTELERIA = 3, 4

#: TpoDocLiq por palabras del nombre de la línea. «99 en caso de anticipo u
#: otras transacciones»; lo que no dice ELECTRÓNICA es documento en papel.
_IVA = Decimal("0.19")

#: Anulado=2 en el Libro de Guías: guía anulada después de enviarla al SII.
_ANULADA_DESPUES_DE_ENVIAR = 2


def _clave(texto: str) -> str:
    """Mayúsculas, sin tildes y con espacios simples: sólo para reconocer."""
    sin = unicodedata.normalize("NFKD", texto)
    sin = "".join(c for c in sin if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin).strip().upper()


def _celdas(linea: str) -> list[str]:
    """Una fila de la hoja: columnas separadas por tabuladores o 2+ espacios."""
    return [c.strip() for c in re.split(r"\t+| {2,}", linea.strip()) if c.strip()]


def _numero(texto: str, linea: int) -> Decimal:
    limpio = texto.replace("%", "").strip()
    try:
        return Decimal(limpio)
    except Exception as ex:  # noqa: BLE001
        raise SheetError(f"se esperaba un número y viene «{texto}»", linea) from ex


def _json(valor: Decimal) -> int | str:
    """Entero si lo es; si no, texto, para no pasar por float."""
    return int(valor) if valor == valor.to_integral_value() else str(valor)


def _codigo(tabla: dict, nombre: str, linea: int) -> int:
    try:
        return code_for(tabla, nombre)
    except KeyError as ex:
        raise SheetError(str(ex.args[0]), linea) from ex


# --------------------------------------------------------------------------- #
#  Entrada
# --------------------------------------------------------------------------- #


def decode(data: bytes) -> str:
    """El SII entrega el archivo en ISO-8859-1; se acepta también UTF-8."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("iso-8859-1")


def parse(texto: str | bytes) -> Sheet:
    """Lee un archivo del SII: el set de pruebas o el set de boletas."""
    if isinstance(texto, bytes):
        texto = decode(texto)
    lineas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if any("SET DE PRUEBA DE BOLETA" in _clave(ln) for ln in lineas[:5]):
        return _boletas(lineas)
    return _set_de_pruebas(lineas)


_ENCABEZADO = re.compile(r"^SET (.+?) - NUMERO DE ATENCION:\s*(\d+)\s*$")


def _set_de_pruebas(lineas: list[str]) -> Sheet:
    hoja = Sheet()
    secciones: list[tuple[str, str, int, list[tuple[int, str]]]] = []
    for n, linea in enumerate(lineas, start=1):
        m = _ENCABEZADO.match(_clave(linea))
        if m:
            nombre, code = m.group(1), m.group(2)
            kind = next((k for clave, k in _SETS if nombre == clave), None)
            if kind is None:
                raise SheetError(f"set desconocido: «{linea.strip()}»", n)
            secciones.append((kind, code, n, []))
        elif secciones:
            secciones[-1][3].append((n, linea))
    if not secciones:
        raise SheetError("no se encontró ningún «SET … - NUMERO DE ATENCION» en el archivo")

    for kind, code, _n, cuerpo in secciones:
        if kind in hoja.sets:
            raise SheetError(f"el set {kind} aparece dos veces", _n)
        if kind == "libro_compras":
            payload = _libro_compras(cuerpo, hoja)
        elif kind == "libro_ventas":
            payload = {"operation_type": "VENTA", "book_type": "ESPECIAL"}
            _notas(cuerpo, hoja, kind)
        elif kind == "libro_guias":
            payload = _libro_guias(cuerpo, hoja)
        else:
            payload = {"documents": _documentos(kind, code, cuerpo, hoja), "validate_xsd": True}
        hoja.sets[kind] = {"code": code, "endpoint": _ENDPOINTS[kind], "payload": payload}
    return hoja


def _notas(cuerpo: list[tuple[int, str]], hoja: Sheet, kind: str) -> None:
    texto = " ".join(
        ln.strip() for _n, ln in cuerpo if ln.strip() and not set(ln.strip()) <= {"-", "="}
    )
    if texto:
        hoja.notas.append(f"{kind}: {texto}")


# --------------------------------------------------------------------------- #
#  Sets de documentos
# --------------------------------------------------------------------------- #

_CASO = re.compile(r"^CASO (\d+)-(\d+)$")
_SEPARADOR = re.compile(r"^[-=]{10,}$")


def _documentos(kind: str, code: str, cuerpo: list[tuple[int, str]], hoja: Sheet) -> list[dict]:
    casos: list[tuple[int, int, list[tuple[int, str]]]] = []
    libre: list[tuple[int, str]] = []
    abierto = False
    for n, linea in cuerpo:
        m = _CASO.match(_clave(linea))
        if m:
            if m.group(1) != code:
                raise SheetError(f"el caso {m.group(1)}-{m.group(2)} no es del set {code}", n)
            casos.append((int(m.group(2)), n, []))
            abierto = True
            continue
        s = linea.strip()
        if abierto and _SEPARADOR.match(s) and not set(s) <= {"="}:
            abierto = False  # fin de los casos: lo que sigue es texto libre
            continue
        if abierto:
            casos[-1][2].append((n, linea))
        else:
            libre.append((n, linea))
    _notas(libre, hoja, kind)

    docs: list[dict] = []
    for esperado, (numero, n, lineas) in enumerate(casos, start=1):
        if numero != esperado:
            raise SheetError(f"se esperaba el caso {code}-{esperado} y viene {code}-{numero}", n)
        docs.append(_caso(kind, code, numero, n, lineas, docs))
    if not docs:
        raise SheetError(f"el set {kind} ({code}) no trae casos")
    return docs


def _caso(kind, code, numero, n_caso, lineas, anteriores: list[dict]) -> dict:
    doc: dict = {}
    items: list[dict] = []
    comisiones: list[dict] = []
    columnas: list[str] | None = None
    tabla: list | None = None
    razon: str | None = None
    ref_caso: int | None = None
    externas: list[dict] = []
    aduana: dict = {}
    cargos: list[dict] = []
    recargo_linea: Decimal | None = None
    descuento_linea: dict[int, Decimal] = {}
    comision_clausula: Decimal | None = None

    for n, linea in lineas:
        s = linea.strip()
        if not s or set(s) <= {"="}:
            tabla = None
            continue
        c = _celdas(linea)
        k = _clave(c[0])

        if (
            tabla is not None
            and not k.endswith(":")
            and k not in ("REFERENCIA", "RAZON REFERENCIA")
        ):
            tabla.append(_fila(c, columnas, n))
            continue

        if k == "DOCUMENTO":
            nombre = _clave(" ".join(c[1:]))
            if nombre not in _DOCUMENTOS:
                raise SheetError(f"tipo de documento desconocido: «{' '.join(c[1:])}»", n)
            doc["type"] = _DOCUMENTOS[nombre]
        elif k == "ITEM":
            columnas, tabla = [_clave(x) for x in c[1:]], items
        elif k.startswith("COMISIONES Y OTROS CARGOS"):
            columnas, tabla = [_clave(x) for x in c[1:]], comisiones
        elif k == "REFERENCIA":
            m = re.search(r"CASO\s+(\d+)-(\d+)", _clave(linea))
            if not m or m.group(1) != code:
                raise SheetError("la referencia no apunta a un caso de este set", n)
            ref_caso = int(m.group(2))
            if not 0 < ref_caso < numero:
                raise SheetError(f"la referencia apunta al caso {ref_caso}, que no es anterior", n)
        elif k == "RAZON REFERENCIA":
            razon = " ".join(c[1:])
        elif k == "REFERENCIA:":
            valor = _clave(" ".join(c[1:]))
            if valor not in _REFERENCIAS_EXTERNAS:
                raise SheetError(f"referencia externa desconocida: «{' '.join(c[1:])}»", n)
            tipo, texto = _REFERENCIAS_EXTERNAS[valor]
            externas.append({"doc_type": tipo, "folio": FOLIO_EXTERNO, "reason": texto})
        elif k == "MOTIVO:":
            valor = _clave(" ".join(c[1:]))
            doc["transfer_type"] = 1 if valor == "VENTA" else 5 if "TRASLADO" in valor else None
            if doc["transfer_type"] is None:
                raise SheetError(f"motivo de traslado desconocido: «{' '.join(c[1:])}»", n)
        elif k == "TRASLADO POR:":
            valor = _clave(" ".join(c[1:]))
            if valor == "CLIENTE":
                doc["dispatch_type"] = 1
            elif "LOCAL DEL CLIENTE" in valor:
                doc["dispatch_type"] = 2
            elif "OTRAS INSTALACIONES" in valor:
                doc["dispatch_type"] = 3
            else:
                raise SheetError(f"traslado desconocido: «{' '.join(c[1:])}»", n)
        elif k.startswith("DESCUENTO GLOBAL"):
            doc.setdefault("global_discounts", []).append(
                {"value": _json(_numero(c[-1], n)), "kind": "D", "reason": c[0]}
            )
        elif m_linea := re.match(r"^DESCUENTO LINEA # ?(\d+):$", k):
            descuento_linea[int(m_linea.group(1))] = _numero(c[-1], n)
        elif m_recargo := re.match(r"^%\s*(\d+) RECARGO EN LA LINEA", k):
            recargo_linea = Decimal(m_recargo.group(1))
        elif k.startswith("COMISIONES EN EL EXTRANJERO"):
            m = re.match(r"^(\d+(?:\.\d+)?)% DEL TOTAL DE LA CLAUSULA$", _clave(" ".join(c[1:])))
            if not m:
                raise SheetError("comisión en el extranjero sin «N% DEL TOTAL DE LA CLAUSULA»", n)
            comision_clausula = Decimal(m.group(1))
        elif k.startswith("EL PRECIO UNITARIO") or k.startswith("LOS PRECIOS UNITARIOS"):
            pass  # es la regla que ya aplica la herencia de las notas
        elif k.endswith(":"):
            _aduana(k, " ".join(c[1:]), n, doc, aduana, cargos)
        else:
            raise SheetError(f"línea no reconocida en el caso {code}-{numero}: «{s}»", n)

    if "type" not in doc:
        raise SheetError(f"el caso {code}-{numero} no dice qué DOCUMENTO es", n_caso)
    tipo = doc["type"]

    items = _items(kind, tipo, items)
    if recargo_linea is not None:
        for item in items:
            item["surcharge_pct"] = _json(recargo_linea)
    for posicion, pct in descuento_linea.items():
        if not 0 < posicion <= len(items):
            raise SheetError(f"descuento en la línea {posicion}, que no existe", n_caso)
        items[posicion - 1]["discount_pct"] = _json(pct)

    if ref_caso is not None:
        if razon is None:
            raise SheetError(f"el caso {code}-{numero} referencia sin RAZON REFERENCIA", n_caso)
        referido = anteriores[ref_caso - 1]
        cod = (
            1
            if _clave(razon).startswith("ANULA")
            else 2
            if _clave(razon).startswith("CORRIGE")
            else 3
        )
        items = _heredar(items, referido, cod, razon, kind, n_caso)
        doc["references"] = [{"batch_index": ref_caso, "code": cod, "reason": razon}]

    if kind == "liquidacion":
        doc["receiver"] = copy.deepcopy(_MANDANTE)
        doc["lines"] = [_linea_liquidacion(i, n_caso) for i in items]
        doc["commissions"] = [_comision(x, n_caso) for x in comisiones]
        doc.pop("type")
        return doc

    doc["items"] = items
    if kind == "factura_compra":
        doc["retentions"] = [{"code": 15}]
        if tipo == 46:
            doc["receiver"] = copy.deepcopy(PROVEEDOR_FACTURA_COMPRA)
    if tipo in _EXPORTACION:
        _exportacion(doc, aduana, cargos, externas, comision_clausula, ref_caso, anteriores, n_caso)
    elif externas or aduana or cargos:
        raise SheetError("datos de aduana en un documento que no es de exportación", n_caso)
    return doc


#: Columnas de texto; las demás tienen que ser números.
_COLUMNAS_DE_TEXTO = {"UNIDAD MEDIDA"}


def _fila(celdas: list[str], columnas: list[str] | None, n: int) -> dict:
    """Una fila de una tabla. Si no calza con el encabezado, no se acepta.

    Una línea suelta dentro de la tabla —una observación, un texto que el SII
    agregó— se leería como un ítem sin cantidad ni precio. Eso es exactamente lo
    que no puede pasar en silencio.
    """
    if columnas is None:
        raise SheetError("fila sin encabezado de columnas", n)
    texto = " | ".join(celdas)
    if len(celdas) < 2:
        raise SheetError(f"fila de tabla sin valores: «{texto}»", n)
    if len(celdas) - 1 > len(columnas):
        raise SheetError(f"la fila tiene más columnas que el encabezado: «{texto}»", n)
    fila: dict = {"_nombre": celdas[0], "_linea": n}
    for col, valor in zip(columnas, celdas[1:], strict=False):
        if col not in _COLUMNAS_DE_TEXTO:
            _numero(valor, n)
        fila[col] = valor
    return fila


def _items(kind: str, tipo: int, filas: list[dict]) -> list[dict]:
    salida = []
    for f in filas:
        n = f["_linea"]
        item: dict = {"name": f["_nombre"]}
        if "CANTIDAD" in f:
            item["quantity"] = _json(_numero(f["CANTIDAD"], n))
        precio = (
            f.get("PRECIO UNITARIO") or f.get("VALOR UNITARIO") or f.get("PRECIO UNITARIO CON IVA")
        )
        if precio is not None:
            item["unit_price"] = _json(_numero(precio, n))
        if "VALOR LINEA" in f:
            # «VALOR LINEA» es el precio de una línea de una unidad: el SII compara
            # cantidad y precio, y una línea con sólo su monto le «No Cuadra».
            item["quantity"], item["unit_price"] = 1, _json(_numero(f["VALOR LINEA"], n))
        if "TOTAL LINEA" in f:
            item["amount"] = _json(_numero(f["TOTAL LINEA"], n))
        if "DESCUENTO ITEM" in f:
            item["discount_pct"] = _json(_numero(f["DESCUENTO ITEM"], n))
        if "UNIDAD MEDIDA" in f:
            item["unit"] = f["UNIDAD MEDIDA"]
        if tipo == 52 and "unit_price" not in item:
            # Una guía sin precio —el traslado entre bodegas— no es venta: precio 0.
            item["unit_price"] = 0
        if tipo == 34 or (kind == "exenta") or "EXENTO" in _clave(f["_nombre"]).split():
            item["exempt"] = True
        if tipo in _EXPORTACION:
            for campo in ("quantity", "unit_price"):
                if campo in item:
                    item[campo] = str(item[campo])
        salida.append(item)
    return salida


def _heredar(items, referido: dict, cod: int, razon: str, kind: str, n: int) -> list[dict]:
    """Lo que una nota toma del documento que modifica.

    - Anula (código 1) sin ítems: los mismos del documento anulado, porque debe
      valer lo mismo —«(REF-2-780)» si no—.
    - Corrige texto (2) sin ítems: una línea con la razón y monto cero.
    - Corrige montos (3): cada ítem completa lo que la hoja no da con el ítem del
      mismo nombre del documento original —«EL PRECIO UNITARIO DEL ITEM DEBE SER
      EL MISMO DE LA FACTURA»—. La unidad sólo se hereda si la nota informa
      cantidad: una devolución mueve unidades; un cambio de monto, no.
    """
    originales = referido.get("items") or []
    if not items:
        if cod == 1:
            return copy.deepcopy(originales)
        if cod == 2:
            linea = {"name": razon, "quantity": 0, "unit_price": 0}
            if originales and all(i.get("exempt") for i in originales):
                linea["exempt"] = True
            return [linea]
        raise SheetError(f"«{razon}» sin ítems: no se sabe qué montos modifica", n)
    salida = []
    for item in items:
        base = next((o for o in originales if _clave(o["name"]) == _clave(item["name"])), None)
        if base is None:
            raise SheetError(f"«{item['name']}» no está en el documento que se modifica", n)
        nuevo = dict(item)
        trae_cantidad = "quantity" in item
        for campo in ("quantity", "unit_price", "discount_pct", "exempt"):
            if campo not in nuevo and campo in base:
                nuevo[campo] = base[campo]
        if trae_cantidad and "unit" not in nuevo and "unit" in base:
            nuevo["unit"] = base["unit"]
        orden = ["name", "quantity", "unit_price", "unit", "discount_pct", "exempt"]
        salida.append(
            {c: nuevo[c] for c in orden if c in nuevo}
            | {c: v for c, v in nuevo.items() if c not in orden}
        )
    return salida


def _tipo_liquidado(nombre: str, n: int) -> str:
    k = _clave(nombre)
    electronica = "ELECTRONICA" in k
    if "ANTICIPO" in k:
        return "99"
    if "LIQUIDACION FACTURA" in k:
        return "43" if electronica else "40"
    if "NOTA DE CREDITO" in k:
        return "61" if electronica else "60"
    if "NOTA DE DEBITO" in k:
        return "56" if electronica else "55"
    if "BOLETA" in k:
        return "39" if electronica else "35"
    if "FACTURA" in k:
        return "33" if electronica else "30"
    raise SheetError(f"no se reconoce qué documento liquida «{nombre}»", n)


def _linea_liquidacion(item: dict, n: int) -> dict:
    if "amount" not in item or "quantity" not in item:
        raise SheetError(f"«{item['name']}» sin CANTIDAD o TOTAL LINEA", n)
    return {
        "liquidated_type": _tipo_liquidado(item["name"], n),
        "name": item["name"],
        "amount": item["amount"],
        "quantity": item["quantity"],
        "exempt": _clave(item["name"]).startswith("EXENTO"),
    }


def _comision(fila: dict, n: int) -> dict:
    nombre = fila["_nombre"]
    if not _clave(nombre).startswith("NETO"):
        raise SheetError(f"comisión «{nombre}»: sólo se reconocen comisiones NETO", n)
    if "TOTAL LINEA" not in fila:
        raise SheetError(f"comisión «{nombre}» sin TOTAL LINEA", n)
    return {"description": nombre, "net_amount": _json(_numero(fila["TOTAL LINEA"], n))}


# --------------------------------------------------------------------------- #
#  Exportación
# --------------------------------------------------------------------------- #


def _aduana(k: str, valor: str, n: int, doc: dict, aduana: dict, cargos: list[dict]) -> None:
    v = valor.strip()
    if k == "MONEDA DE LA OPERACION:":
        doc["currency"] = v
    elif k == "FORMA DE PAGO EXPORTACION:":
        # «SIN PAGO» como forma de pago es S/PAGO (21). El mismo texto en la
        # tabla de modalidades de venta es otro código.
        nombre = "S/PAGO" if _clave(v) == "SIN PAGO" else v
        doc["payment_mode"] = _codigo(PAYMENT_MODES, nombre, n)
    elif k == "MODALIDAD DE VENTA:":
        aduana["sale_mode"] = _codigo(SALE_MODES, v, n)
    elif k == "CLAUSULA DE VENTA DE EXPORTACION:":
        aduana["sale_clause"] = _codigo(SALE_CLAUSES, v, n)
    elif k == "TOTAL CLAUSULA DE VENTA:":
        aduana["clause_total"] = str(_numero(v, n))
    elif k == "VIA DE TRANSPORTE:":
        aduana["transport_route"] = _codigo(TRANSPORT_ROUTES, v, n)
    elif k == "PUERTO DE EMBARQUE:":
        aduana["loading_port"] = _codigo(PORTS, v, n)
    elif k == "PUERTO DE DESEMBARQUE:":
        aduana["unloading_port"] = _codigo(PORTS, v, n)
    elif k == "UNIDAD DE MEDIDA DE TARA:":
        aduana["tare_unit"] = _codigo(MEASURE_UNITS, v, n)
    elif k == "UNIDAD PESO BRUTO:":
        aduana["gross_weight_unit"] = _codigo(MEASURE_UNITS, v, n)
    elif k == "UNIDAD PESO NETO:":
        aduana["net_weight_unit"] = _codigo(MEASURE_UNITS, v, n)
    elif k == "TIPO DE BULTO:":
        # «CONTENEDOR REFRIGERADO» sin medida: el de 20 pies, como el set aprobado.
        nombre = "CONTENEDOR REFRIGERADO 20 PIES" if _clave(v) == "CONTENEDOR REFRIGERADO" else v
        aduana["_bulto"] = (_codigo(PACKAGE_TYPES, nombre, n), _clave(v))
    elif k == "TOTAL BULTOS:":
        aduana["total_packages"] = int(_numero(v, n))
    elif k == "FLETE (**):":
        aduana["freight"] = str(_numero(v, n))
        cargos.append(
            {"value": float(_numero(v, n)), "kind": "R", "value_type": "$", "reason": "FLETE"}
        )
    elif k == "SEGURO (**):":
        aduana["insurance"] = str(_numero(v, n))
        cargos.append(
            {"value": float(_numero(v, n)), "kind": "R", "value_type": "$", "reason": "SEGURO"}
        )
    elif k == "PAIS RECEPTOR Y PAIS DESTINO:":
        aduana["receiver_country"] = aduana["destination_country"] = _codigo(COUNTRIES, v, n)
    elif k == "NACIONALIDAD:":
        doc["receiver_nationality"] = _codigo(COUNTRIES, v, n)
    else:
        raise SheetError(f"dato desconocido: «{k} {v}»", n)


def _exportacion(doc, aduana, cargos, externas, comision_clausula, ref_caso, anteriores, n) -> None:
    doc["receiver"] = copy.deepcopy(_IMPORTADOR)
    referido = anteriores[ref_caso - 1] if ref_caso else None

    if referido is not None:
        # Las notas repiten la moneda y los países del documento que modifican.
        doc.setdefault("currency", referido.get("currency"))
        paises = {
            k: v
            for k, v in (referido.get("customs") or {}).items()
            if k in ("receiver_country", "destination_country")
        }
        if paises and not aduana:
            aduana = dict(paises)
    if not doc.get("currency"):
        raise SheetError("documento de exportación sin MONEDA DE LA OPERACION", n)

    if "_bulto" in aduana:
        codigo, nombre = aduana.pop("_bulto")
        grupo = {"kind_code": codigo, "quantity": aduana.get("total_packages")}
        grupo["marks"] = doc["items"][0]["name"] if doc["items"] else "SIN MARCAS"
        if "CONTENEDOR" in nombre:
            grupo.update(CONTENEDOR)
        aduana["packages"] = [grupo]

    orden = [
        "sale_mode", "sale_clause", "clause_total", "transport_route", "loading_port",
        "unloading_port", "tare_unit", "gross_weight_unit", "net_weight_unit",
        "total_packages", "packages", "freight", "insurance", "receiver_country",
        "destination_country",
    ]  # fmt: skip
    if aduana:
        doc["customs"] = {c: aduana[c] for c in orden if c in aduana}

    if comision_clausula is not None:
        if "clause_total" not in aduana:
            raise SheetError("comisión sobre la cláusula sin TOTAL CLAUSULA DE VENTA", n)
        monto = (Decimal(aduana["clause_total"]) * comision_clausula / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cargos.append(
            {
                "value": float(monto),
                "kind": "R",
                "value_type": "$",
                "reason": "COMISIONES EN EL EXTERIOR",
            }
        )
    if cargos:
        doc["global_charges"] = cargos

    refs = list(doc.get("references") or [])
    if any(r["doc_type"] == _RESOLUCION_SNA for r in externas):
        doc["service_indicator"] = _SERVICIOS
    if "receiver_nationality" in doc and not aduana:
        # Hotelería: el SII exige una segunda referencia, el pasaporte del huésped.
        doc["service_indicator"] = _HOTELERIA
        externas.append(
            {
                "doc_type": _REF_PASAPORTE,
                "folio": PASAPORTE,
                "reason": "PASAPORTE CLIENTE EXTRANJERO",
            }
        )
    refs.extend(externas)
    if refs:
        doc["references"] = refs

    moneda = _clave(doc["currency"])
    if moneda not in TIPO_DE_CAMBIO:
        raise SheetError(
            f"no hay tipo de cambio de referencia para «{doc['currency']}»:"
            " agrégalo en TIPO_DE_CAMBIO",
            n,
        )
    doc["other_currency"] = {"exchange_rate": TIPO_DE_CAMBIO[moneda], "currency": "PESO CL"}


# --------------------------------------------------------------------------- #
#  Libros
# --------------------------------------------------------------------------- #


def _libro_guias(cuerpo: list[tuple[int, str]], hoja: Sheet) -> dict:
    """«EL CASO 3 CORRESPONDE A UNA GUIA ANULADA» → la guía 3 va anulada.

    La guía facturada no se marca: que se facturó ya lo dice su tipo de
    operación (el 1, «facturado o se facturará posteriormente»).
    """
    anuladas = []
    for _n, linea in cuerpo:
        m = re.search(r"EL CASO (\d+) CORRESPONDE A UNA GUIA ANULADA", _clave(linea))
        if m:
            anuladas.append(int(m.group(1)))
    _notas(cuerpo, hoja, "libro_guias")
    return {"submission_type": "TOTAL", "voided_cases": anuladas}


def _libro_compras(cuerpo: list[tuple[int, str]], hoja: Sheet) -> dict:
    """La tabla de documentos de compra: tres filas por documento.

    ``TIPO DOCUMENTO  FOLIO`` · ``OBSERVACIONES`` · ``MONTO EXENTO  MONTO AFECTO``.
    El monto exento va en la primera columna y el afecto en la segunda; una fila
    que empieza con tabulador no trae exento.
    """
    texto = " ".join(_clave(ln) for _n, ln in cuerpo)
    factor = re.search(r"FACTOR DE PROPORCIONALIDAD DEL IVA ES DE (\d+(?:\.\d+)?)", texto)

    marcas = [i for i, (_n, ln) in enumerate(cuerpo) if re.match(r"^={10,}$", ln.strip())]
    if len(marcas) < 3:
        raise SheetError("no se encontró la tabla de documentos del libro de compras")
    filas = [(n, ln) for n, ln in cuerpo[marcas[1] + 1 : marcas[2]] if ln.strip()]
    if len(filas) % 3:
        raise SheetError(
            "la tabla del libro de compras no viene en grupos de 3 filas", filas[-1][0]
        )

    lineas = []
    for i in range(0, len(filas), 3):
        (n1, l1), (n2, l2), (n3, l3) = filas[i : i + 3]
        c1 = _celdas(l1)
        nombre, folio = _clave(" ".join(c1[:-1])), c1[-1]
        if nombre not in _DOCUMENTOS_COMPRA:
            raise SheetError(f"documento de compra desconocido: «{' '.join(c1[:-1])}»", n1)
        montos = _celdas(l3)
        if l3.startswith("\t") or l3.startswith("  \t"):
            exento, afecto = Decimal(0), _numero(montos[0], n3)
        elif len(montos) == 2:
            exento, afecto = _numero(montos[0], n3), _numero(montos[1], n3)
        else:
            exento, afecto = _numero(montos[0], n3), Decimal(0)
        lineas.append(
            _linea_compra(_DOCUMENTOS_COMPRA[nombre], int(folio), exento, afecto, l2.strip(), n2)
        )

    payload: dict = {"operation_type": "COMPRA", "book_type": "ESPECIAL"}
    if factor:
        payload["proportionality_factor"] = float(factor.group(1))
    payload["lines"] = lineas
    return payload


def _linea_compra(tipo: int, folio: int, exento: Decimal, neto: Decimal, obs: str, n: int) -> dict:
    iva = int((neto * _IVA).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    k = _clave(obs)
    linea: dict = {
        "doc_type": tipo,
        "folio": folio,
        **PROVEEDOR_LIBRO_COMPRAS,
        "exempt_amount": int(exento),
        "net_amount": int(neto),
        "vat_amount": iva,
    }
    if "IVA USO COMUN" in k:
        linea["vat_amount"] = 0
        linea["common_use_vat"] = iva
    elif "ENTREGA GRATUITA" in k:
        # Código 4 de IVA no recuperable: «Entregas gratuitas (premios,
        # bonificaciones etc.) recibidas».
        linea["vat_amount"] = 0
        linea["non_recoverable_vat"] = [{"code": 4, "amount": iva}]
    elif "RETENCION TOTAL" in k:
        linea["retained_total_vat"] = iva
    elif not ("DERECHO A CREDITO" in k or "NOTA DE CREDITO" in k or "NOTA DE DEBITO" in k):
        raise SheetError(f"observación de compra no reconocida: «{obs}»", n)
    linea["total_amount"] = (
        int(exento) + int(neto) + linea["vat_amount"] + linea.get("common_use_vat", 0)
        + sum(x["amount"] for x in linea.get("non_recoverable_vat", []))
        - linea.get("retained_total_vat", 0)
    )  # fmt: skip
    return linea


# --------------------------------------------------------------------------- #
#  Set de boletas
# --------------------------------------------------------------------------- #


def _boletas(lineas: list[str]) -> Sheet:
    """El set de boletas: otro archivo, otro formato, casos «CASO-n».

    Ojo con las tildes: este archivo avisa que «Se omitieron las tildes para
    permitir una correcta lectura» y pide los caracteres «tal cual se encuentran
    en el Set». Se copian como vienen, igual que en el set de pruebas.
    """
    hoja = Sheet()
    casos: list[tuple[int, int, list[tuple[int, str]]]] = []
    for n, linea in enumerate(lineas, start=1):
        s = linea.strip()
        m = re.match(r"^CASO-(\d+)$", _clave(s))
        if m:
            casos.append((int(m.group(1)), n, []))
        elif s.startswith("====") and casos and len(s) > 40:
            casos[-1][2].append((n, "\x00fin"))
        elif casos:
            casos[-1][2].append((n, linea))

    boletas = []
    for esperado, (numero, n_caso, cuerpo) in enumerate(casos, start=1):
        if numero != esperado:
            raise SheetError(f"se esperaba CASO-{esperado} y viene CASO-{numero}", n_caso)
        items, columnas, tabla = [], None, False
        for n, linea in cuerpo:
            if linea == "\x00fin":
                break
            s = linea.strip()
            if not s or set(s) <= {"="}:
                tabla = False
                continue
            c = _celdas(linea)
            k = _clave(c[0])
            if k == "ITEM":
                columnas, tabla = [_clave(x) for x in c[1:]], True
            elif tabla:
                items.append(_fila(c, columnas, n))
            elif k.startswith("OBSERVACION"):
                texto = _clave(s)
                for m in re.finditer(r"EL ITEM (\d+) ES UN SERVICIO EXENTO", texto):
                    items_exentos = int(m.group(1))
                    if not 0 < items_exentos <= len(items):
                        raise SheetError(
                            f"la observación marca el ítem {items_exentos}, que no existe", n
                        )
                    items[items_exentos - 1]["_exento"] = True
                # Sobre el texto original y no el normalizado: la unidad se copia
                # tal como la escribe la hoja («Kg», no «KG»).
                m_unidad = re.search(r"(?i)unidad de medida en (\w+)", s)
                if m_unidad:
                    for f in items:
                        f["_unidad"] = m_unidad.group(1)
            else:
                raise SheetError(f"línea no reconocida en CASO-{numero}: «{s}»", n)
        detalle = []
        for f in items:
            item = {
                "name": f["_nombre"],
                "quantity": _json(_numero(f["CANTIDAD"], f["_linea"])),
                "unit_price": _json(_numero(f["PRECIO UNITARIO CON IVA"], f["_linea"])),
            }
            if f.get("_exento"):
                item["exempt"] = True
            if f.get("_unidad"):
                item["unit"] = f["_unidad"]
            detalle.append(item)
        if not detalle:
            raise SheetError(f"CASO-{numero} sin ítems", n_caso)
        # «Debe referenciar el caso correspondiente a cada boleta en el XML.
        # Ejemplo: <CodRef> SET <RazonRef> CASO-1». En la boleta CodRef es el
        # código alfanumérico; TpoDocRef es para documentos y debe ser numérico.
        boletas.append(
            {
                "type": 39,
                "items": detalle,
                "references": [{"code": "SET", "reason": f"CASO-{numero}"}],
            }
        )
    if not boletas:
        raise SheetError("el set de boletas no trae casos")
    hoja.sets["boletas"] = {"code": "", "endpoint": "boletas", "payload": {"receipts": boletas}}
    return hoja
