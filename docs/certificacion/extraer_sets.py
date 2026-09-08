"""Extrae el payload de cada script del set SIN emitir nada.

Se ejecuta el script real con ``post`` interceptado: el cuerpo lo construye el
mismo código que el SII ya aceptó, así que no hay transcripción a mano y por
tanto no hay dígitos mal copiados.
"""
import json, pathlib, runpy, sys

ORIGEN = pathlib.Path(sys.argv[1])
DESTINO = pathlib.Path(sys.argv[2])
DESTINO.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ORIGEN))

import emisor  # noqa: E402

capturado = {}


def _post(path, payload, *a, **k):
    capturado["path"] = path
    capturado["payload"] = payload
    # Devuelve la forma mínima que los scripts esperan para no reventar.
    raise SystemExit(0)


emisor.post = _post

SETS = {
    "set_basico.py": ("basico", "5038170"),
    "set_5038173_guias.py": ("guias", "5038173"),
    "set_5038175_exenta.py": ("exenta", "5038175"),
    "set_5038176_exportacion.py": ("exportacion_1", "5038176"),
    "set_5038177_exportacion2.py": ("exportacion_2", "5038177"),
    "set_5038178.py": ("liquidacion", "5038178"),
    "set_5038180.py": ("factura_compra", "5038180"),
    "set_5038174_libro_guias.py": ("libro_guias", "5038174"),
    "set_5038172_libro_compras.py": ("libro_compras", "5038172"),
    "set_5038171_libro_ventas.py": ("libro_ventas", "5038171"),
    "set_boletas.py": ("boletas", ""),
}

ENDPOINT = {
    "/dte/issue-batch": "issue-batch",
    "/dte/issue-export-batch": "issue-export-batch",
    "/dte/issue-settlement-batch": "issue-settlement-batch",
    "/books": "books",
    "/books/guides": "books/guides",
    "/boletas/issue-batch": "boletas",
}

salida = {}
for archivo, (kind, code) in SETS.items():
    ruta = ORIGEN / archivo
    if not ruta.exists():
        print(f"  {archivo:34} NO ESTÁ")
        continue
    capturado.clear()
    sys.argv = [str(ruta)]  # sin --send
    try:
        runpy.run_path(str(ruta), run_name="__main__")
    except SystemExit:
        pass
    except Exception as ex:
        print(f"  {archivo:34} ERROR {type(ex).__name__}: {str(ex)[:70]}")
        continue
    if "payload" not in capturado:
        print(f"  {archivo:34} no llegó a postear")
        continue
    cuerpo = dict(capturado["payload"])
    cuerpo.pop("send", None)
    salida[kind] = {
        "code": code,
        "endpoint": ENDPOINT.get(capturado["path"], capturado["path"]),
        "payload": cuerpo,
    }
    n = len(cuerpo.get("documents") or cuerpo.get("lines") or [])
    print(f"  {archivo:34} OK  {kind:16} {n} elementos")

(DESTINO / "definiciones.json").write_text(
    json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"\n{len(salida)} definiciones extraídas")
