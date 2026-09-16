"""Compara el contenido de dos sobres de certificación, sin lo que cambia al emitir.

Dos emisiones del mismo set nunca dan los mismos bytes: cambian los folios, las
fechas, la hora de firma, el timbre y las firmas. Lo que el SII revisa al
contrastar un set contra su enunciado es todo lo demás —ítems, cantidades,
precios, descuentos, montos, referencias, aduana, líneas y resúmenes de los
libros—, y eso tiene que coincidir.

Se usa para comprobar que un set armado desde la hoja del SII emite lo mismo que
un set que el SII ya aprobó.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from lxml import etree

#: Lo que cambia en cada emisión y no es contenido del caso.
_IGNORAR = {
    "Signature",  # firmas
    "TED",  # timbre: depende del folio, la fecha y el CAF
    "TmstFirma",
    "TmstFirmaEnv",
    "FchEmis",
    "FchRef",
    "FchDoc",
    "FchVenc",
    "PeriodoTributario",  # el mes de la emisión
}

#: Documentos dentro de un EnvioDTE.
_DOCUMENTOS = ("Documento", "Liquidacion", "Exportaciones")


@dataclass
class Diferencia:
    ruta: str
    aprobado: str | None
    emitido: str | None

    def __str__(self) -> str:
        return f"{self.ruta}: aprobado {self.aprobado!r} · emitido {self.emitido!r}"


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _texto(nodo, nombre: str) -> str | None:
    for h in nodo.iter():
        if _local(h.tag) == nombre:
            return (h.text or "").strip()
    return None


def _aplanar(nodo, ruta: str, folios: dict, salida: list[tuple[str, str]]) -> None:
    """Hojas del árbol como (ruta, texto), con los folios cambiados por su posición."""
    contadores: dict[str, int] = {}
    for hijo in nodo:
        nombre = _local(hijo.tag)
        if not nombre or nombre in _IGNORAR:
            continue
        contadores[nombre] = contadores.get(nombre, 0) + 1
        sub = f"{ruta}/{nombre}[{contadores[nombre]}]"
        if len(hijo):
            _aplanar(hijo, sub, folios, salida)
            continue
        texto = (hijo.text or "").strip()
        if nombre in ("Folio", "NroDoc"):
            tipo = _texto(nodo, "TipoDTE") or _texto(nodo, "TpoDoc") or ""
            texto = folios.get((tipo, texto), texto)
        elif nombre in ("FolioRef", "FolioDocRef"):
            tipo = _texto(nodo, "TpoDocRef") or ""
            texto = folios.get((tipo, texto), texto)
        salida.append((sub, texto))


def _folios_del_sobre(raiz) -> dict[tuple[str, str], str]:
    """(tipo, folio) → «tipo#n»: el enésimo documento de ese tipo en el sobre."""
    mapa: dict[tuple[str, str], str] = {}
    cuenta: dict[str, int] = {}
    for nodo in raiz.iter():
        nombre = _local(nodo.tag)
        if nombre in _DOCUMENTOS:
            tipo, folio = _texto(nodo, "TipoDTE"), _texto(nodo, "Folio")
        elif nombre == "Detalle" and _texto(nodo, "TpoDoc") is not None:
            tipo = _texto(nodo, "TpoDoc")
            folio = _texto(nodo, "NroDoc") or _texto(nodo, "Folio")
        elif nombre == "Detalle" and _local(nodo.getparent().tag) == "EnvioLibro":
            tipo, folio = "52", _texto(nodo, "Folio")  # libro de guías: sin TpoDoc
        else:
            continue
        if tipo is None or folio is None or (tipo, folio) in mapa:
            continue
        cuenta[tipo] = cuenta.get(tipo, 0) + 1
        mapa[(tipo, folio)] = f"{tipo}#{cuenta[tipo]}"
    return mapa


def contenido(xml: bytes) -> list[tuple[str, str]]:
    """El contenido revisable de un sobre (EnvioDTE, EnvioBOLETA o libro)."""
    raiz = etree.fromstring(xml)
    salida: list[tuple[str, str]] = []
    _aplanar(raiz, "", _folios_del_sobre(raiz), salida)
    return salida


def comparar(
    aprobado: bytes | list[tuple[str, str]], emitido: bytes | list[tuple[str, str]]
) -> list[Diferencia]:
    """Las diferencias de contenido entre dos sobres, campo por campo.

    Acepta el XML o su contenido ya extraído con ``contenido``.
    """
    a = dict(contenido(aprobado) if isinstance(aprobado, bytes) else aprobado)
    e = dict(contenido(emitido) if isinstance(emitido, bytes) else emitido)
    return [
        Diferencia(ruta, a.get(ruta), e.get(ruta))
        for ruta in sorted(set(a) | set(e))
        if a.get(ruta) != e.get(ruta)
    ]


# --------------------------------------------------------------------------- #
#  Diferencias conocidas con lo que aprobó el SII
# --------------------------------------------------------------------------- #


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _presente(d: Diferencia) -> bool:
    """Un dato que la hoja no trae: basta con que los dos lo informen."""
    return bool((d.aprobado or "").strip()) and bool((d.emitido or "").strip())


def _literal_con_tildes(d: Diferencia) -> bool:
    """Lo emitido es la hoja literal; lo aprobado, lo mismo sin tildes o abreviado.

    Abreviado quiere decir palabra por palabra: «LIQ FACT ELECT» de «LIQUIDACIÓN
    FACTURA ELECTRÓNICA». Cualquier otra cosa —otra cifra, otra palabra— no pasa.
    """
    if d.aprobado is None or d.emitido is None:
        return False
    a, e = _sin_tildes(d.aprobado).split(), _sin_tildes(d.emitido).split()
    return len(a) == len(e) and all(
        x == y or (len(x) >= 3 and y.startswith(x)) for x, y in zip(a, e, strict=True)
    )


#: (set, patrón de la ruta, regla, por qué). Una diferencia sólo se acepta si
#: calza con la ruta Y cumple su regla.
CONOCIDAS: list[tuple[str, str, Callable[[Diferencia], bool], str]] = [
    (
        "guias",
        r"/Transporte\[1\]/DirDest\[1\]$",
        _presente,
        "destino del traslado interno: la hoja no lo trae",
    ),
    (
        "exportacion_2",
        r"/TipoBultos\[1\]/Marcas\[1\]$",
        _presente,
        "marcas del bulto: la hoja no las trae",
    ),
    (
        "liquidacion",
        r"/(NmbItem|Glosa)\[1\]$",
        _literal_con_tildes,
        "la hoja trae tildes que el set aprobado quitó; se copia literal",
    ),
]


def clasificar(
    kind: str, diferencias: list[Diferencia]
) -> tuple[list[tuple[Diferencia, str]], list[Diferencia]]:
    """Separa las diferencias aceptadas —con su motivo— de las inesperadas."""
    aceptadas, inesperadas = [], []
    for d in diferencias:
        motivo = next(
            (
                por_que
                for set_, patron, regla, por_que in CONOCIDAS
                if set_ == kind and re.search(patron, d.ruta) and regla(d)
            ),
            None,
        )
        if motivo:
            aceptadas.append((d, motivo))
        else:
            inesperadas.append(d)
    return aceptadas, inesperadas
