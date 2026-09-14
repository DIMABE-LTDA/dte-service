"""Los diez sets de certificación, con la forma que define el SII.

El Servicio entrega a cada contribuyente un PDF con *sus* casos: sus diez
números de atención y, por cada caso, los ítems y las cantidades. Eso cambia de
una empresa a otra y hay que transcribirlo.

Lo que **no** cambia es la forma: qué sets hay, qué documentos lleva cada uno,
en qué orden, y cómo se referencian entre sí —la nota de crédito que anula la
tercera factura del lote la referencia por posición, no por folio, porque
cuando se arma el sobre el folio todavía no existe—. Esa estructura sale del
«Instructivo para la construcción de documentos con los datos del set de
pruebas» y es idéntica para todos.

Sin esta plantilla, poner en marcha una certificación nueva exigía clonar la de
otro contribuyente o pegar un JSON armado a mano: quien no supiera de memoria
que el set básico son cuatro facturas, tres notas de crédito y una de débito
—y a qué documento apunta cada una— no podía empezar.

Los nombres de ítem y los montos son de relleno **a propósito**. Son justo lo
que el operador tiene que reemplazar con su PDF, y un monto heredado de otro
contribuyente que pasara inadvertido es el error que el SII devuelve semanas
después, cuando ya no se recuerda de dónde salió. Que se vean obviamente falsos
es la única forma de que nadie los dé por buenos.
"""

from __future__ import annotations

from typing import Any

#: Precio de relleno. Un número redondo y evidente: nadie lo confunde con un
#: dato del SII, y si llegara a enviarse tal cual, salta a la vista en la previa.
_PRECIO = 1000


def _item(nombre: str, *, exento: bool = False, precio: int = _PRECIO, unidad: str = "") -> dict:
    fila: dict[str, Any] = {"name": nombre, "quantity": 1, "unit_price": precio}
    if exento:
        fila["exempt"] = True
    if unidad:
        fila["unit"] = unidad
    return fila


def _afectos(cuantos: int) -> list[dict]:
    return [_item(f"ITEM {i} AFECTO") for i in range(1, cuantos + 1)]


def _ref(indice: int, code: int, reason: str) -> dict:
    """Referencia a otro documento del mismo lote, por posición.

    ``batch_index`` es 1-based sobre ``documents``: el lote se firma entero y
    los folios se asignan al emitir, así que dentro del sobre no hay otra forma
    de apuntar a un documento que todavía no tiene folio.
    """
    return {"batch_index": indice, "code": code, "reason": reason}


def _ref_externa(doc_type: int, reason: str) -> dict:
    """Referencia a un documento de fuera del sobre (DUS, MIC, AWB, SNA).

    Sin fecha: la pone el sistema al emitir, igual que la de emisión.
    """
    return {"doc_type": doc_type, "folio": "1", "reason": reason}


#: Receptor extranjero de los sets de exportación. Es dato del caso —el SII lo
#: da en el set— y no se reemplaza por un cliente del contribuyente.
_IMPORTADOR = {
    "rut": "55555555-5",
    "business_name": "IMPORTADORA EXTRANJERA SA",
    "activity": "IMPORTACION",
    "address": "CALLE EXTRANJERA 1",
    "commune": "BARCELONA",
    "city": "BARCELONA",
}

#: Mandante de las liquidaciones factura. También es dato del caso.
_MANDANTE = {
    "rut": "17099910-K",
    "business_name": "MANDANTE EJEMPLO LIMITADA",
    "activity": "Comercio al por mayor",
    "address": "Calle Mandante 456",
    "commune": "Providencia",
    "city": "Santiago",
}


def _aduana() -> dict:
    """Bloque de aduana del set de exportación.

    Códigos del SII (modalidad de venta, cláusula, puertos, países). Se dejan
    puestos porque son estructura del caso; los valores en moneda extranjera
    son de relleno como todo lo demás.
    """
    return {
        "sale_mode": 4,
        "sale_clause": 2,
        "clause_total": "1000",
        "transport_route": 1,
        "loading_port": 906,
        "unloading_port": 563,
        "tare_unit": 17,
        "gross_weight_unit": 9,
        "net_weight_unit": 9,
        "total_packages": 1,
        # IdContainer y Sello: el XSD los declara opcionales (minOccurs="0")
        # pero el SII los exige igual —«(HED-2-804) Exportacion : Campo
        # obligatorio»— apenas el documento trae un grupo de bultos.
        "packages": [
            {
                "kind_code": 13,
                "quantity": 1,
                "marks": "SIN MARCAS",
                "container_id": "CONTENEDOR RELLENO",
                "seal": "SELLO DE RELLENO",
            }
        ],
        "freight": "100",
        "insurance": "50",
        "receiver_country": 517,
        "destination_country": 517,
    }


def _cargos(*motivos: str) -> list[dict]:
    """Flete, seguro y comisiones: cargos globales de la factura de exportación."""
    return [{"value": 100, "kind": "R", "value_type": "$", "reason": m} for m in motivos]


# --------------------------------------------------------------------------- #
#  Los diez sets
# --------------------------------------------------------------------------- #

SETS: list[dict[str, Any]] = [
    {
        "kind": "basico",
        "label": "Set básico",
        "endpoint": "issue-batch",
        "help": (
            "Cuatro facturas afectas (la cuarta con descuento global y un ítem exento),"
            " tres notas de crédito y una nota de débito que anula la primera nota."
        ),
        "payload": {
            "documents": [
                {"type": 33, "items": _afectos(2)},
                {"type": 33, "items": _afectos(2)},
                {"type": 33, "items": _afectos(3)},
                {
                    "type": 33,
                    "items": _afectos(2) + [_item("ITEM 3 SERVICIO EXENTO", exento=True)],
                    "global_discounts": [
                        {"value": 20, "kind": "D", "reason": "DESCUENTO GLOBAL ITEMES AFECTOS"}
                    ],
                },
                {
                    "type": 61,
                    "items": [_item("CORRIGE GIRO DEL RECEPTOR", precio=0)],
                    "references": [_ref(1, 2, "CORRIGE GIRO DEL RECEPTOR")],
                },
                {
                    "type": 61,
                    "items": _afectos(2),
                    "references": [_ref(2, 3, "DEVOLUCION DE MERCADERIAS")],
                },
                {
                    "type": 61,
                    "items": _afectos(3),
                    "references": [_ref(3, 1, "ANULA FACTURA")],
                },
                {
                    "type": 56,
                    # Mismo monto que la nota que anula (0): si difieren, el
                    # SII acepta con reparo «(REF-2-780) Anulación presenta
                    # diff. de monto con doc. referenciado».
                    "items": [_item("ANULA NOTA DE CREDITO ELECTRONICA", precio=0)],
                    "references": [_ref(5, 1, "ANULA NOTA DE CREDITO ELECTRONICA")],
                },
            ]
        },
    },
    {
        "kind": "guias",
        "label": "Set de guías de despacho",
        "endpoint": "issue-batch",
        "help": (
            "Una guía de traslado interno (no es venta: va al propio emisor) y dos de"
            " venta, una por cuenta del vendedor y otra del comprador."
        ),
        "payload": {
            "documents": [
                {
                    "type": 52,
                    "transfer_type": 5,
                    "transport": {
                        "dest_address": "BODEGA 2",
                        "dest_commune": "",
                    },
                    "items": [_item(f"ITEM {i}", precio=0) for i in (1, 2, 3)],
                },
                {"type": 52, "transfer_type": 1, "dispatch_type": 2, "items": _afectos(2)},
                {"type": 52, "transfer_type": 1, "dispatch_type": 1, "items": _afectos(2)},
            ]
        },
    },
    {
        "kind": "exenta",
        "label": "Set de facturas exentas",
        "endpoint": "issue-batch",
        "help": (
            "Tres facturas exentas con sus notas de crédito, y dos notas de débito:"
            " una anula una nota de crédito y la otra modifica un monto."
        ),
        "payload": {
            "documents": [
                {"type": 34, "items": [_item("HORAS PROGRAMADOR", exento=True, unidad="Hora")]},
                {
                    "type": 61,
                    "items": [_item("MODIFICA MONTO", exento=True)],
                    "references": [_ref(1, 3, "MODIFICA MONTO")],
                },
                {
                    "type": 34,
                    "items": [
                        _item("ITEM 1 EXENTO", exento=True),
                        _item("ITEM 2 EXENTO", exento=True),
                    ],
                },
                {
                    "type": 61,
                    "items": [_item("CORRIGE GIRO DEL RECEPTOR", exento=True, precio=0)],
                    "references": [_ref(3, 2, "CORRIGE GIRO DEL RECEPTOR")],
                },
                {
                    "type": 56,
                    # Mismo monto que la nota que anula (0).
                    "items": [_item("ANULA NOTA DE CREDITO ELECTRONICA", exento=True, precio=0)],
                    "references": [_ref(4, 1, "ANULA NOTA DE CREDITO ELECTRONICA")],
                },
                {
                    "type": 34,
                    "items": [
                        _item("ITEM 1 EXENTO", exento=True),
                        _item("ITEM 2 EXENTO", exento=True),
                    ],
                },
                {
                    "type": 61,
                    "items": [_item("MODIFICA MONTO", exento=True)],
                    "references": [_ref(6, 3, "MODIFICA MONTO")],
                },
                {
                    "type": 56,
                    "items": [_item("MODIFICA MONTO", exento=True)],
                    "references": [_ref(6, 3, "MODIFICA MONTO")],
                },
            ]
        },
    },
    {
        "kind": "exportacion_1",
        "label": "Set de exportación (1)",
        "endpoint": "issue-export-batch",
        "help": (
            "Factura de exportación de mercancías con flete y seguro, su nota de"
            " débito y la nota de crédito que la anula. Pon el tipo de cambio real:"
            " el SII exige los montos también en pesos y viene de relleno en 1."
        ),
        "payload": {
            "documents": [
                {
                    "type": 110,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    "items": [_item("MERCANCIA 1", unidad="LT")],
                    "customs": _aduana(),
                    "global_charges": _cargos("FLETE", "SEGURO"),
                    "payment_mode": 21,
                    "references": [_ref_externa(810, "MIC")],
                },
                {
                    "type": 112,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    "items": [_item("DEVOLUCION DE MERCADERIA")],
                    "customs": _aduana(),
                    "references": [_ref(1, 3, "DEVOLUCION DE MERCADERIA")],
                },
                {
                    "type": 111,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    # Mismas líneas que la nota de crédito que anula: con montos
                    # distintos el SII responde «(REF-2-780) Anulación presenta
                    # diff. de monto con doc. referenciado».
                    "items": [_item("DEVOLUCION DE MERCADERIA")],
                    "customs": _aduana(),
                    "references": [_ref(2, 1, "ANULA NOTA DE CREDITO")],
                },
            ]
        },
    },
    {
        "kind": "exportacion_2",
        "label": "Set de exportación (2)",
        "endpoint": "issue-export-batch",
        "help": (
            "Tres facturas de exportación: servicios con resolución del SNA,"
            " mercancías con DUS y AWB, y servicios a un receptor extranjero"
            " del que se declara la nacionalidad. Pon el tipo de cambio real:"
            " el SII exige los montos también en pesos y viene de relleno en 1."
        ),
        "payload": {
            "documents": [
                {
                    "type": 110,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    "items": [_item("SERVICIO EXPORTADO 1")],
                    "service_indicator": 3,
                    "payment_mode": 11,
                    "customs": _aduana(),
                    "references": [_ref_externa(812, "RESOLUCION SNA")],
                },
                {
                    "type": 110,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    "items": [_item("MERCANCIA 1"), _item("MERCANCIA 2")],
                    "payment_mode": 21,
                    "customs": _aduana(),
                    "global_charges": _cargos("FLETE", "SEGURO", "COMISIONES EN EL EXTERIOR"),
                    "references": [_ref_externa(807, "DUS"), _ref_externa(809, "AWB")],
                },
                {
                    "type": 110,
                    "receiver": dict(_IMPORTADOR),
                    "currency": "DOLAR USA",
                    "other_currency": {"exchange_rate": 1},
                    "items": [_item("SERVICIO EXPORTADO 1")],
                    "service_indicator": 4,
                    "receiver_nationality": 563,
                },
            ]
        },
    },
    {
        "kind": "liquidacion",
        "label": "Set de liquidaciones factura",
        "endpoint": "issue-settlement-batch",
        "help": (
            "Cuatro liquidaciones al mandante; las dos últimas con comisiones."
            " Se entregan en un solo sobre: el SII espera un envío por set."
        ),
        "payload": {
            "documents": [
                {
                    "receiver": dict(_MANDANTE),
                    "lines": [
                        {
                            "liquidated_type": "33",
                            "name": "NETO FACTURAS",
                            "amount": _PRECIO,
                            "quantity": 1,
                            "exempt": False,
                        }
                    ],
                    "commissions": [],
                },
                {
                    "receiver": dict(_MANDANTE),
                    "lines": [
                        {
                            "liquidated_type": "33",
                            "name": "NETO FACTURAS",
                            "amount": _PRECIO,
                            "quantity": 1,
                            "exempt": False,
                        }
                    ],
                    "commissions": [],
                },
                {
                    "receiver": dict(_MANDANTE),
                    "lines": [
                        {
                            "liquidated_type": "33",
                            "name": "NETO FACTURAS",
                            "amount": _PRECIO,
                            "quantity": 1,
                            "exempt": False,
                        }
                    ],
                    "commissions": [
                        {"description": "NETO COMISION FIJA", "net_amount": 100},
                        {"description": "NETO COMISION VARIABLE", "net_amount": 100},
                    ],
                },
                {
                    "receiver": dict(_MANDANTE),
                    "lines": [
                        {
                            "liquidated_type": "33",
                            "name": "NETO FACTURAS",
                            "amount": _PRECIO,
                            "quantity": 1,
                            "exempt": False,
                        }
                    ],
                    "commissions": [
                        {"description": "NETO COMISION CONSIGNACION", "net_amount": 100},
                    ],
                },
            ]
        },
    },
    {
        "kind": "factura_compra",
        "label": "Set de facturas de compra",
        "endpoint": "issue-batch",
        "help": (
            "Factura de compra con retención total (código 15), su nota de crédito"
            " y la nota de débito que la anula. Las tres llevan el mismo código de"
            " retención."
        ),
        "payload": {
            "documents": [
                {"type": 46, "items": _afectos(2), "retentions": {"code": 15}},
                {
                    "type": 61,
                    "items": _afectos(2),
                    "retentions": {"code": 15},
                    "references": [_ref(1, 3, "DEVOLUCION DE MERCADERIA ITEMS 1 Y 2")],
                },
                {
                    "type": 56,
                    # Una anulación revierte el documento entero, así que lleva
                    # sus mismas líneas: si el total no coincide, el SII acepta
                    # con reparo «(REF-2-780) Anulación presenta diff. de monto
                    # con doc. referenciado».
                    "items": _afectos(2),
                    "retentions": {"code": 15},
                    "references": [_ref(2, 1, "ANULA NOTA DE CREDITO ELECTRONICA")],
                },
            ]
        },
    },
    {
        "kind": "libro_ventas",
        "label": "Libro de ventas",
        "endpoint": "books",
        "help": (
            "No se transcribe: sus líneas las arma el sistema con los documentos"
            " que el SII aceptó de los sets de ventas. Sólo necesita su número"
            " de atención."
        ),
        "payload": {"operation_type": "VENTA", "book_type": "ESPECIAL"},
    },
    {
        "kind": "libro_compras",
        "label": "Libro de compras",
        "endpoint": "books",
        "help": (
            "Éste sí se transcribe entero: sus documentos los entrega el SII en el"
            " propio set (no los emite el contribuyente). Agrega una línea por cada"
            " documento del set, y el factor de proporcionalidad que indique."
        ),
        "payload": {
            "operation_type": "COMPRA",
            "book_type": "ESPECIAL",
            "proportionality_factor": 0,
            "lines": [],
        },
    },
    {
        "kind": "libro_guias",
        "label": "Libro de guías de despacho",
        "endpoint": "books/guides",
        "help": (
            "Como el de ventas: sus líneas salen de las guías que el SII aceptó."
            " Sólo necesita su número de atención."
        ),
        "payload": {"submission_type": "TOTAL"},
    },
]

#: Los sets en el orden en que conviene trabajarlos, por su clave.
KINDS: tuple[str, ...] = tuple(s["kind"] for s in SETS)


def sets_for_import(codes: dict[str, str]) -> dict[str, dict]:
    """El cuerpo que espera ``/import``, con los números de atención dados.

    Sólo entran los sets para los que hay número: el SII no siempre asigna los
    diez —el de boletas va por otro trámite— y dar de alta un set sin su número
    de atención deja un expediente que no se puede enviar.
    """
    salida: dict[str, dict] = {}
    for plantilla in SETS:
        code = str(codes.get(plantilla["kind"], "") or "").strip()
        if not code:
            continue
        salida[plantilla["kind"]] = {
            "code": code,
            "endpoint": plantilla["endpoint"],
            "payload": plantilla["payload"],
        }
    return salida
