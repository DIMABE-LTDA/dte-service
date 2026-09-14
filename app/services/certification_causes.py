"""Qué significa cada respuesta del SII y qué revisar cuando rechaza.

Es conocimiento que se pagó caro y que hasta ahora vivía en un documento que
sólo lee quien ya sabe: diez envíos rechazados «por error en firma» que en
realidad eran un permiso faltante, y trece intentos del Libro de Ventas hasta
dar con la causa. Con un contribuyente eso es una anécdota; certificando a
varios es la diferencia entre cerrar un set en un intento o en dos semanas.

Va en el repositorio y no en la base a propósito: no depende del cliente, se
revisa en git y crece cuando el SII enseña algo nuevo.
"""

from __future__ import annotations

from typing import NamedTuple


class Cause(NamedTuple):
    label: str
    #: Qué significa el código, en una línea.
    meaning: str
    #: Qué suele ser en la práctica. Vacío cuando el código no es un rechazo.
    usually: str = ""
    #: Qué mirar, en el orden en que conviene mirarlo.
    check: tuple[str, ...] = ()
    #: True si el envío quedó bien y no hay nada que hacer.
    ok: bool = False


CAUSES: dict[str, Cause] = {
    # --- Aceptados ---
    "EPR": Cause(
        "Envío procesado",
        "El sobre se procesó. Ojo: puede traer documentos con reparos dentro.",
        usually="",
        check=(
            "Revisa el detalle del envío en Mi SII → Revisar envíos por si algún"
            " documento quedó con reparos.",
            "El SII también avisa por correo a la casilla de contacto de la empresa.",
        ),
        ok=True,
    ),
    "LOK": Cause("Libro aceptado", "El libro llegó y cuadró.", ok=True),
    "SOK": Cause("Schema correcto", "El sobre pasó la validación de esquema.", ok=True),
    "DOK": Cause("Documentos correctos", "Los documentos del sobre son válidos.", ok=True),
    # --- Rechazos ---
    "RFR": Cause(
        "Rechazado por error en firma",
        "El SII dice que la firma no valida.",
        usually="Casi nunca es la firma. Lo habitual es que el RUT que firma no"
        " tenga el permiso de envío en ESE ambiente.",
        check=(
            "Habilita «Enviar Doctos» al RUT del certificado en Administración de"
            " Empresa Autorizada → Mantención de Usuarios, en el ambiente donde"
            " estás enviando.",
            "Los ambientes tienen registros de usuarios SEPARADOS: el permiso de"
            " producción no vale en certificación ni al revés.",
            "Sólo después de descartar eso, verifica la firma del sobre con xmlsec.",
        ),
    ),
    "LRH": Cause(
        "Libro rechazado: descuadrado",
        "Los totales que declara el libro no cuadran con sus líneas.",
        usually="Suele ser un campo del libro equivocado, no una suma mal hecha.",
        check=(
            "TotOpIVARec es del libro de COMPRAS e IVARetTotal del de VENTAS:"
            " cruzarlos descuadra el libro.",
            "En una liquidación factura (43), las comisiones van dentro de"
            " <Liquidaciones>; sin ellas la línea no cierra.",
            "Lee el motivo exacto en Mi SII → Revisar envíos: dice qué total no le"
            " cuadra, en vez de deducirlo a ciegas.",
        ),
    ),
    "LRS": Cause(
        "Libro rechazado por schema",
        "El XML del libro no valida contra el XSD del SII.",
        usually="Un elemento fuera de orden o un campo que no corresponde a ese libro.",
        check=(
            "Valida el libro contra LibroCV_v10.xsd antes de enviarlo; el servicio"
            " lo hace si dejas validate_xsd activado.",
            "Revisa que los tipos de documento del libro sean los que ese libro"
            " admite: la guía 52 va al Libro de Guías, no al de ventas.",
        ),
    ),
    "LRC": Cause(
        "Carátula de envío inválida",
        "La carátula del libro está mal formada.",
        usually="El tipo de libro o el folio de notificación no son los que el set pide.",
        check=(
            "El set de certificación se entrega como libro ESPECIAL con su número"
            " de atención en FolioNotificacion, no como MENSUAL.",
            "RECTIFICA es un TipoLibro, no un TipoEnvio: enviarlo como tipo de"
            " envío devuelve esta misma carátula inválida.",
        ),
    ),
    "LRF": Cause(
        "Libro rechazado por firma",
        "La firma del libro no valida.",
        usually="Mismo caso que RFR: revisa primero el permiso de envío.",
        check=("Verifica «Enviar Doctos» en el ambiente donde estás enviando.",),
    ),
    "LNC": Cause(
        "Tipo de envío de libro no corresponde",
        "El SII no esperaba un libro de ese tipo para ese período.",
        usually="Suele salir al reenviar un período que ya tiene un libro aceptado.",
        check=(
            "Comprueba si ese período ya tiene un libro aceptado: después de un LOK,"
            " los envíos siguientes del mismo período responden LNC.",
            "Para corregir un período ya aceptado hace falta una rectificación, y su"
            " carátula necesita algo más que cambiar el TipoLibro.",
        ),
    ),
    "RCT": Cause(
        "Rechazado por errores de schema",
        "El sobre no valida contra el XSD.",
        usually="Datos que el SII no admite en algún campo del documento.",
        check=(
            "Valida contra los XSD oficiales antes de enviar.",
            "Revisa caracteres fuera de ISO-8859-1 y glosas más largas de lo"
            " permitido: el servicio los rechaza con 422 si validas antes.",
        ),
    ),
    "RCH": Cause(
        "Rechazado",
        "El SII rechazó el sobre.",
        usually="",
        check=("Lee el detalle en Mi SII → Revisar envíos: ahí está el motivo concreto.",),
    ),
    "RSC": Cause(
        "Rechazado por schema",
        "El sobre no valida.",
        usually="",
        check=("Valida contra el XSD oficial antes de reenviar.",),
    ),
}


def for_state(state: str | None) -> Cause | None:
    """La guía del código, si se conoce."""
    return CAUSES.get((state or "").strip().upper()) or None
