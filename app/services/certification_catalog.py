"""Catálogo del trámite de certificación ante el SII.

Es lo que **no** cambia entre contribuyentes: qué sets pide el Servicio, qué
documentos lleva cada uno y en qué orden va el trámite. Lo que sí cambia —el
número de atención de cada set y el contenido de sus casos— es dato del cliente.

Existe para que el expediente sepa **qué falta**, no sólo qué llegó. Sin este
catálogo, un set que nunca se envió simplemente no aparece, y es justo el que
hay que ver.
"""

from __future__ import annotations

from typing import NamedTuple


class SetType(NamedTuple):
    kind: str
    label: str
    doc_types: tuple[int, ...]
    hint: str
    #: False si el set NO va en el formulario «Declarar avance» de Mi SII. El
    #: de boletas se emite y se envía como los demás, pero su avance no se
    #: informa ahí: es el paso 2 del trámite y sus TrackID se comunican aparte.
    #: Sin esta distinción el contador diría "de 11" y el formulario tiene 10.
    declarable: bool = True


# Los diez sets del trámite. El orden es el que conviene seguir: los documentos
# antes que los libros, porque un libro declara lo que los documentos emitieron.
SET_TYPES: tuple[SetType, ...] = (
    SetType(
        "basico",
        "Set básico",
        (33, 56, 61),
        "Facturas con sus notas de crédito y débito, en un solo sobre: las notas"
        " referencian a facturas del propio envío.",
    ),
    SetType(
        "exenta",
        "Factura exenta",
        (34, 56, 61),
        "Mismo patrón que el básico, con documentos exentos de IVA.",
    ),
    SetType(
        "guias",
        "Guías de despacho",
        (52,),
        "Las guías van también al Libro de Guías, que es un set aparte.",
    ),
    SetType(
        "exportacion_1",
        "Documentos de exportación (1)",
        (110, 111, 112),
        "Va en un sobre SEPARADO del segundo set de exportación. Los montos son"
        " en moneda extranjera.",
    ),
    SetType(
        "exportacion_2",
        "Documentos de exportación (2)",
        (110,),
        "Sobre aparte del primero, aunque compartan tipo de documento.",
    ),
    SetType(
        "liquidacion",
        "Liquidación factura",
        (43,),
        "Sus comisiones van dentro de <Liquidaciones>, no colgando del detalle.",
    ),
    SetType(
        "factura_compra",
        "Factura de compra",
        (46, 56, 61),
        "La emite el comprador. En el Libro de Ventas no va: es una compra.",
    ),
    SetType(
        "libro_guias",
        "Libro de guías",
        (),
        "Registro que exige la Res. Ex. N.º 154 mientras el SII no ponga en"
        " marcha su Registro de Guías.",
    ),
    SetType(
        "libro_compras",
        "Libro de compras",
        (),
        "IECV de compras del período.",
    ),
    SetType(
        "libro_ventas",
        "Libro de ventas",
        (),
        "Se entrega como libro ESPECIAL con su número de atención, no MENSUAL.",
    ),
    SetType(
        "boletas",
        "Set de boletas",
        (39,),
        "Las cinco boletas en UN SOLO sobre, más el reporte de consumo de folios,"
        " dentro de las 24 horas siguientes a bajar el CAF. No va en el formulario"
        " de «Declarar avance»: sus TrackID se informan aparte.",
        declarable=False,
    ),
)

BY_KIND = {s.kind: s for s in SET_TYPES}


class Step(NamedTuple):
    key: str
    label: str
    detail: str
    # True si el sistema puede saber solo si está cumplido. El resto son
    # trámites fuera del servicio y los marca el operador.
    automatic: bool


# Los seis pasos de la postulación. El expediente lleva el 1 con los sets; los
# demás son hitos que el operador confirma, porque ocurren en el sitio del SII o
# por correo y no hay forma de saberlo desde aquí.
STEPS: tuple[Step, ...] = (
    Step(
        "sets",
        "Set de pruebas",
        "Enviar los sets y declarar su avance en Mi SII.",
        True,
    ),
    Step(
        "boletas",
        "Set de boletas",
        "Al pedir el CAF corren 24 horas para enviarlo completo en un solo envío,"
        " más el reporte de consumo de folios.",
        False,
    ),
    Step(
        "simulacion",
        "Simulación",
        "Un envío con facturación real de los últimos dos meses: entre 10 y 100 documentos.",
        False,
    ),
    Step(
        "intercambio",
        "Intercambio de información",
        "Responder acuses de recibo y respuestas de DTE recibidos de terceros.",
        False,
    ),
    Step(
        "impresion",
        "Muestras de impresión",
        "PDF con todos los documentos del set más 10 de la simulación, con timbre"
        " PDF417, a sii_dte_impresos@sii.cl.",
        False,
    ),
    Step(
        "cumplimiento",
        "Declaración de cumplimiento",
        "La firma el representante legal en el sitio del SII. Después de eso el"
        " Servicio autoriza a operar.",
        False,
    ),
)

STEP_KEYS = tuple(s.key for s in STEPS)
