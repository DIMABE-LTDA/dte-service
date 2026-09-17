"""HTML → PDF, para los impresos de los DTE.

El motor entrega el impreso como HTML (con el timbre PDF417 embebido en PNG).
Aquí se convierte a PDF con WeasyPrint, que respeta ``@page`` —el tamaño de la
hoja que fija el manual de muestras impresas del SII— y deja el texto como
texto y el timbre como imagen, que es lo que su aplicación de revisión lee.

WeasyPrint necesita Pango en el sistema (``apt-get install libpango-1.0-0
libpangoft2-1.0-0``). Se importa al usarlo: sin Pango el resto del servicio
funciona igual y sólo este paso falla, con un mensaje que dice qué falta.
"""

from __future__ import annotations


class PdfUnavailableError(RuntimeError):
    """El servidor no tiene lo necesario para generar PDF."""


def html_to_pdf(html: str) -> bytes:
    return render_pdf(html)[0]


def render_pdf(html: str) -> tuple[bytes, int]:
    """El PDF y cuántas páginas tiene.

    El conteo sale del documento ya maquetado: buscar «/Type /Page» en los bytes
    no sirve, porque WeasyPrint comprime los objetos del PDF. Y el conteo
    importa: la aplicación de muestras del SII rechaza las de más de una página.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as ex:  # OSError: falta Pango en el sistema
        raise PdfUnavailableError(
            "este servidor no puede generar PDF: falta WeasyPrint o su dependencia"
            f" de sistema Pango ({ex.__class__.__name__})"
        ) from ex
    document = HTML(string=html).render()
    return document.write_pdf(), len(document.pages)
