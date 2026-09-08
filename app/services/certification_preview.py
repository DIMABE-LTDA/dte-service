"""Leer, en cristiano, qué se va a emitir y qué se emitió.

Un bloque de JSON no es una revisión. Emitir consume folios y no se deshace, así
que el paso de "revisar antes de enviar" sólo sirve si se entiende sin abrir el
XML: qué documento, a quién, con qué ítems y por cuánto.

Hay dos momentos y son distintos:

- **Antes de emitir** sólo existe la definición. Se puede mostrar el tipo, el
  receptor y los ítems con su monto de línea, pero **no el total del
  documento**: los descuentos globales y el IVA los calcula el motor, y
  reproducir esa aritmética aquí significaría que la vista podría mentir.
- **Después de emitir** existe el documento firmado. Ahí se leen del propio XML
  el folio y los totales reales, que es lo que de verdad se va a enviar.
"""

from __future__ import annotations

from decimal import Decimal

from lxml import etree

#: Etiquetas de los tipos que aparecen en la certificación.
DOC_LABELS = {
    33: "Factura electrónica",
    34: "Factura exenta",
    39: "Boleta electrónica",
    41: "Boleta exenta",
    43: "Liquidación factura",
    46: "Factura de compra",
    52: "Guía de despacho",
    56: "Nota de débito",
    61: "Nota de crédito",
    110: "Factura de exportación",
    111: "Nota de débito de exportación",
    112: "Nota de crédito de exportación",
}

#: Códigos de referencia del SII que usa el set de pruebas.
REF_CODES = {1: "Anula documento", 2: "Corrige texto", 3: "Corrige montos"}

#: Documentos que no son DTE pero se referencian desde uno. Los de exportación
#: son obligatorios —el SII pide declarar el DUS y el documento de transporte—,
#: así que en la previa tienen que leerse por su nombre y no por su número.
REF_DOC_LABELS = {
    801: "Orden de compra",
    802: "Nota de pedido",
    803: "Contrato",
    804: "Resolución",
    807: "DUS",
    808: "Conocimiento de embarque",
    809: "Carta de porte aéreo (AWB)",
    810: "MIC/DTA",
    811: "Carta de porte",
    812: "Resolución del SNA",
    813: "Pasaporte",
}


def label(doc_type: int | None) -> str:
    if doc_type is None:
        return "—"
    return DOC_LABELS.get(int(doc_type), f"Tipo {doc_type}")


def _line_amount(item: dict) -> Decimal | None:
    """Monto de la línea: cantidad × precio, menos el descuento de esa línea.

    Es aritmética sin ambigüedad. Lo que NO se calcula aquí es el total del
    documento, que depende de descuentos globales e IVA.
    """
    if item.get("amount") is not None:
        return Decimal(str(item["amount"]))
    cantidad, precio = item.get("quantity"), item.get("unit_price")
    if cantidad is None or precio is None:
        return None
    monto = Decimal(str(cantidad)) * Decimal(str(precio))
    if item.get("discount_pct"):
        monto -= monto * Decimal(str(item["discount_pct"])) / 100
    if item.get("surcharge_pct"):
        monto += monto * Decimal(str(item["surcharge_pct"])) / 100
    return monto


def _reference(ref: dict) -> str:
    """Una referencia, dicha entera: a qué apunta y por qué.

    Son dos cosas distintas y hay que distinguirlas. Una referencia interna
    apunta a otro documento del mismo sobre, que todavía no tiene folio, así que
    sólo se puede nombrar por su posición. Una externa apunta a un documento que
    ya existe fuera —el DUS, el conocimiento de embarque— y ahí lo que importa
    es su nombre y su folio, no una posición que no tiene.
    """
    razon = f" — {ref['reason']}" if ref.get("reason") else ""
    destino = ref.get("batch_index")
    if destino:
        codigo = REF_CODES.get(ref.get("code"), "Referencia")
        return f"{codigo} n.º {destino} de este mismo envío{razon}"
    tipo = ref.get("doc_type")
    if tipo is not None:
        nombre = REF_DOC_LABELS.get(int(tipo)) or DOC_LABELS.get(int(tipo)) or f"Documento {tipo}"
        folio = f" n.º {ref['folio']}" if ref.get("folio") else ""
        fecha = f" del {ref['date']}" if ref.get("date") else ""
        return f"{nombre}{folio}{fecha}{razon}"
    return (REF_CODES.get(ref.get("code"), "Referencia") + razon).strip()


def _document(doc: dict, position: int) -> dict:
    receptor = doc.get("receiver") or {}
    items = []
    for item in doc.get("items", []):
        monto = _line_amount(item)
        items.append(
            {
                "name": item.get("name", ""),
                "quantity": item.get("quantity"),
                "unit_price": item.get("unit_price"),
                "discount_pct": item.get("discount_pct"),
                "exempt": bool(item.get("exempt")),
                "amount": float(monto) if monto is not None else None,
            }
        )
    referencias = [_reference(r) for r in doc.get("references", [])]
    afectos = sum(i["amount"] or 0 for i in items if not i["exempt"])
    exentos = sum(i["amount"] or 0 for i in items if i["exempt"])
    return {
        "position": position,
        "doc_type": doc.get("type"),
        "doc_label": label(doc.get("type")),
        "receiver": receptor.get("business_name", ""),
        "receiver_rut": receptor.get("rut", ""),
        "currency": doc.get("currency", ""),
        "items": items,
        "lines_affect": afectos,
        "lines_exempt": exentos,
        "references": referencias,
        "global_discounts": [
            f"{g.get('kind', 'D')} {g.get('value')}{g.get('value_type', '%')}"
            + (f" — {g['reason']}" if g.get("reason") else "")
            for g in doc.get("global_charges", []) or doc.get("global_discounts", [])
        ],
    }


def _book_line(line: dict, position: int) -> dict:
    return {
        "position": position,
        "doc_type": line.get("doc_type"),
        "doc_label": label(line.get("doc_type")),
        "folio": line.get("folio"),
        "receiver": line.get("business_name", ""),
        "receiver_rut": line.get("rut", ""),
        "net": line.get("net_amount", 0),
        "exempt": line.get("exempt_amount", 0),
        "vat": line.get("vat_amount", 0),
        "total": line.get("total_amount", 0),
        "currency": line.get("currency", ""),
    }


def definition(endpoint: str, payload: dict) -> dict:
    """Qué se va a emitir, legible, a partir de la definición del set."""
    if endpoint in ("books", "books/guides"):
        lineas = payload.get("lines", [])
        return {
            "kind": "libro",
            "summary": f"Libro de {payload.get('period', '—')} con {len(lineas)} línea(s)",
            "detail": (
                f"Tipo {payload.get('book_type', 'MENSUAL')}"
                f" · folio de notificación {payload.get('notification_folio', '—')}"
            ),
            "documents": [_book_line(linea, i) for i, linea in enumerate(lineas, start=1)],
            "note": "",
        }
    documentos = payload.get("documents", [])
    return {
        "kind": "documentos",
        "summary": f"{len(documentos)} documento(s) en un solo sobre",
        "detail": "Las referencias apuntan a documentos del propio envío.",
        "documents": [_document(d, i) for i, d in enumerate(documentos, start=1)],
        # Se dice explícitamente para que nadie lea la suma de líneas como el
        # total del documento.
        "note": "Los montos son la suma de las líneas. El total del documento —con"
        " descuentos globales e IVA— lo calcula el motor y se ve tras emitir.",
    }


def _text(node, *names: str) -> str | None:
    for hijo in node.iter():
        if str(hijo.tag).rsplit("}", 1)[-1] in names and hijo.text:
            return hijo.text.strip()
    return None


def envelope(xml: bytes) -> list[dict]:
    """Qué contiene de verdad un sobre ya emitido, leído de su XML firmado.

    Aquí sí están el folio y los totales reales: es el documento que se va a
    enviar, no una previsión de él.
    """
    raiz = etree.fromstring(xml)
    salida = []
    for nodo in raiz.iter():
        if str(nodo.tag).rsplit("}", 1)[-1] != "Documento":
            continue
        tipo = _text(nodo, "TipoDTE")
        salida.append(
            {
                "doc_type": int(tipo) if tipo and tipo.isdigit() else None,
                "doc_label": label(int(tipo)) if tipo and tipo.isdigit() else "—",
                "folio": _text(nodo, "Folio"),
                "receiver": _text(nodo, "RznSocRecep") or "",
                "receiver_rut": _text(nodo, "RUTRecep") or "",
                "net": _text(nodo, "MntNeto") or "0",
                "exempt": _text(nodo, "MntExe") or "0",
                "vat": _text(nodo, "IVA") or "0",
                "total": _text(nodo, "MntTotal") or "0",
            }
        )
    return salida
