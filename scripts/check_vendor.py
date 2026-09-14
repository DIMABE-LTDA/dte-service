"""El motor vendorizado tiene que ser el tag que fija pyproject.

Se ejecuta en CI, donde el motor además está instalado desde ese mismo tag: así
la comprobación no se cree la etiqueta escrita en un archivo, sino que compara
el código vendorizado con el que se instaló desde git.

Sin esto, `vendor/` se edita a mano o se olvida regenerar, y Docker construye
con un motor distinto al que se probó.
"""

from __future__ import annotations

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
VENDOR = RAIZ / "vendor" / "dte_chile"


def tag_fijado() -> str:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'engine\s*=\s*\["dte_chile @ git\+[^@"]+@([^"]+)"\]', texto)
    if not m:
        raise SystemExit("No encuentro el tag del motor en el extra 'engine' de pyproject.toml")
    return m.group(1)


def tag_vendorizado() -> str:
    version = VENDOR / "VERSION"
    if not version.exists():
        raise SystemExit(f"Falta {version}: regenera con scripts/vendor_engine.sh")
    return version.read_text(encoding="utf-8").splitlines()[0].strip()


def _contenido(ruta: pathlib.Path) -> bytes:
    """El archivo con finales de línea normalizados.

    `git archive` entrega LF y en Windows el árbol de trabajo puede tener CRLF:
    comparar byte a byte avisaría en falso en cada equipo Windows, y un aviso
    que salta sin motivo deja de mirarse.
    """
    return ruta.read_bytes().replace(b"\r\n", b"\n")


def diferencias(instalado: pathlib.Path) -> list[str]:
    """Archivos .py que no coinciden entre lo vendorizado y lo instalado."""
    vendor_src = VENDOR / "src" / "dte_chile"
    problemas = []
    nuestros = {p.relative_to(vendor_src) for p in vendor_src.rglob("*.py")}
    suyos = {
        p.relative_to(instalado) for p in instalado.rglob("*.py") if "__pycache__" not in p.parts
    }
    for falta in sorted(suyos - nuestros):
        problemas.append(f"falta en vendor/: {falta}")
    for sobra in sorted(nuestros - suyos):
        problemas.append(f"sobra en vendor/: {sobra}")
    for comun in sorted(nuestros & suyos):
        if _contenido(vendor_src / comun) != _contenido(instalado / comun):
            problemas.append(f"distinto: {comun}")
    return problemas


def main() -> int:
    fijado, vendorizado = tag_fijado(), tag_vendorizado()
    if fijado != vendorizado:
        print(f"pyproject fija el motor {fijado} y vendor/ trae {vendorizado}.")
        print(f"Regenera:  bash scripts/vendor_engine.sh {fijado}")
        return 1

    try:
        import dte_chile
    except ImportError:
        print(f"Motor {fijado} vendorizado; no está instalado, no se compara el código.")
        return 0

    instalado = pathlib.Path(dte_chile.__file__).parent
    if instalado.is_relative_to(VENDOR):
        print("El motor instalado ES el vendorizado; nada que comparar.")
        return 0
    problemas = diferencias(instalado)
    if problemas:
        print(f"El motor vendorizado no coincide con {fijado}:")
        for p in problemas:
            print("  -", p)
        print(f"Regenera:  bash scripts/vendor_engine.sh {fijado}")
        return 1
    print(f"Motor vendorizado = {fijado}, idéntico al instalado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
