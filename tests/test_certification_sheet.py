"""El lector del archivo del SII contra lo que el SII aprobó.

`definiciones-77262159-0.json` son las definiciones con que CONSTRUCTORA DIMABE
SPA sacó los diez sets en SOK el 16-09-2026. Leer su hoja del set tiene que dar
exactamente eso. Las únicas diferencias aceptadas están listadas aquí con su
porqué; cualquier otra hace fallar el test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import certification_fill, certification_sheet
from app.services.certification_sheet import SheetError

RAIZ = Path(__file__).resolve().parents[1]
HOJA = RAIZ / "tests/fixtures/sii/set_pruebas_77262159-0.txt"
BOLETAS = RAIZ / "tests/fixtures/sii/set_boletas_77262159-0.txt"
APROBADAS = RAIZ / "docs/certificacion/definiciones-77262159-0.json"

#: Lo que completa el sistema al emitir: la definición no lo trae.
_RECEPTOR_DEL_SISTEMA = {33, 34, 52, 56, 61}

#: Diferencias aceptadas con lo aprobado, ruta → por qué.
DIFERENCIAS = {
    # La hoja escribe «FACTURA ELECTRÓNICA», «NOTA DE CRÉDITO», «COMISIÓN»... con
    # tildes, y quien armó el set aprobado las quitó y abrevió una glosa. El SII lo
    # aprobó igual, así que no compara ese campo o lo normaliza: en los dos casos
    # la versión literal también pasa. El lector copia la hoja, que es lo que pide
    # el instructivo, y quitar tildes en el set básico costó siete rechazos.
    "liquidacion": "nombres y glosas literales, con tildes",
    # Marcas del bulto: la hoja no las da. El set aprobado repitió las del set (1).
    "exportacion_2.documents[1].customs.packages[0].marks": "la hoja no trae marcas",
    # Las guías anuladas se leen de la hoja; en lo aprobado estaban fijas en el código.
    "libro_guias.voided_cases": "leído de la hoja",
}


def _normal(valor):
    """Compara contenido, no formato: 872 == "872", false/[] == ausente."""
    if isinstance(valor, dict):
        return {k: _normal(v) for k, v in valor.items() if v not in (False, None, [])}
    if isinstance(valor, list):
        return [_normal(x) for x in valor]
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, int | float):
        return str(Decimal(str(valor)).normalize())
    if isinstance(valor, str):
        try:
            return str(Decimal(valor).normalize())
        except Exception:  # noqa: BLE001 — no es un número
            return valor
    return valor


def _definicion(kind: str, entrada: dict) -> dict:
    payload = certification_fill.strip(entrada["endpoint"], kind, entrada["payload"])
    for doc in payload.get("documents", []):
        if doc.get("type") in _RECEPTOR_DEL_SISTEMA:
            doc.pop("receiver", None)
        doc.pop("transport", None)
    for linea in payload.get("lines", []):
        linea.pop("date", None)
    return _normal(payload)


def _diferencias(a, b, ruta: str) -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        salida = []
        for k in sorted(set(a) | set(b)):
            sub = f"{ruta}.{k}"
            if k not in a or k not in b:
                salida.append(sub)
            else:
                salida += _diferencias(a[k], b[k], sub)
        return salida
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{ruta} (largo {len(a)} vs {len(b)})"]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for d in _diferencias(x, y, f"{ruta}[{i}]")
        ]
    return [] if a == b else [ruta]


def _aceptada(ruta: str) -> bool:
    return any(
        ruta == d or ruta.startswith(d + ".") or ruta.startswith(d + "[") for d in DIFERENCIAS
    )


@pytest.fixture(scope="module")
def leido():
    hoja = certification_sheet.parse(HOJA.read_bytes())
    hoja.sets.update(certification_sheet.parse(BOLETAS.read_bytes()).sets)
    return hoja


@pytest.fixture(scope="module")
def aprobado():
    return json.loads(APROBADAS.read_text(encoding="utf-8"))


def test_lee_los_once_sets(leido, aprobado):
    assert sorted(leido.sets) == sorted(aprobado)
    for kind, entrada in aprobado.items():
        assert leido.sets[kind]["code"] == entrada["code"], kind
        assert leido.sets[kind]["endpoint"] == entrada["endpoint"], kind


@pytest.mark.parametrize(
    "kind",
    [
        "basico", "guias", "exenta", "exportacion_1", "exportacion_2", "liquidacion",
        "factura_compra", "libro_guias", "libro_compras", "libro_ventas", "boletas",
    ],
)  # fmt: skip
def test_cada_set_es_el_que_aprobo_el_sii(leido, aprobado, kind):
    distintas = _diferencias(
        _definicion(kind, leido.sets[kind]), _definicion(kind, aprobado[kind]), kind
    )
    inesperadas = [d for d in distintas if not _aceptada(d)]
    assert inesperadas == []


def test_la_liquidacion_solo_difiere_en_las_tildes(leido, aprobado):
    """La diferencia aceptada no puede esconder otra: sin tildes, es idéntica."""
    import unicodedata

    def sin_tildes(texto):
        return "".join(
            c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
        )

    leida = json.loads(sin_tildes(json.dumps(leido.sets["liquidacion"], ensure_ascii=False)))
    distintas = _diferencias(
        _definicion("liquidacion", leida), _definicion("liquidacion", aprobado["liquidacion"]), ""
    )
    assert distintas == [".documents[3].commissions[1].description"]  # la glosa abreviada


def test_las_reglas_de_contenido_no_objetan_lo_leido(leido):
    from types import SimpleNamespace

    from app.services import certification_checks

    defs = {k: SimpleNamespace(payload=v["payload"]) for k, v in leido.sets.items()}
    assert certification_checks._contenido(defs) == []


def test_da_lo_mismo_en_utf8(leido):
    """Si alguien reguarda el archivo en UTF-8, se lee igual."""
    texto = HOJA.read_bytes().decode("iso-8859-1")
    assert certification_sheet.parse(texto.encode("utf-8")).sets == {
        k: v for k, v in leido.sets.items() if k != "boletas"
    }


# --------------------------------------------------------------------------- #
#  No adivina
# --------------------------------------------------------------------------- #


def _con(reemplazo: tuple[str, str]) -> bytes:
    texto = HOJA.read_bytes().decode("iso-8859-1")
    assert reemplazo[0] in texto
    return texto.replace(reemplazo[0], reemplazo[1], 1).encode("iso-8859-1")


def test_una_linea_suelta_dentro_de_la_tabla_detiene_la_lectura():
    """Antes se leía como un ítem sin cantidad ni precio. La encontró este test."""
    with pytest.raises(
        SheetError, match=r"línea 21: fila de tabla sin valores: «OBSERVACION RARA»"
    ):
        certification_sheet.parse(_con(("Relleno AFECTO", "OBSERVACION RARA\nRelleno AFECTO")))


def test_una_linea_desconocida_en_un_caso_detiene_la_lectura():
    with pytest.raises(SheetError, match=r"línea \d+: línea no reconocida en el caso 5038170-4"):
        certification_sheet.parse(
            _con(
                (
                    "DESCUENTO GLOBAL ITEMES AFECTOS",
                    "RECARGO MISTERIOSO\t10%\nDESCUENTO GLOBAL ITEMES AFECTOS",
                )
            )
        )


def test_un_tipo_de_documento_desconocido_detiene_la_lectura():
    with pytest.raises(SheetError, match="tipo de documento desconocido"):
        certification_sheet.parse(
            _con(("DOCUMENTO\tFACTURA ELECTRONICA", "DOCUMENTO\tFACTURA RARA"))
        )


def test_un_codigo_de_aduana_desconocido_detiene_la_lectura():
    with pytest.raises(SheetError, match="No hay código de Aduana"):
        certification_sheet.parse(_con(("SAN ANTONIO", "PUERTO INVENTADO")))


def test_una_nota_que_modifica_un_item_que_no_existe_detiene_la_lectura():
    with pytest.raises(SheetError, match="no está en el documento que se modifica"):
        certification_sheet.parse(_con(("Pañuelo AFECTO\t\t    247", "Pañuelo RARO\t\t    247")))


def test_un_documento_de_compra_desconocido_detiene_la_lectura():
    with pytest.raises(SheetError, match="documento de compra desconocido"):
        certification_sheet.parse(_con(("FACTURA\t\t\t\t\t781", "BOLETA\t\t\t\t\t781")))


def test_un_archivo_que_no_es_del_sii():
    with pytest.raises(SheetError, match="NUMERO DE ATENCION"):
        certification_sheet.parse(b"hola")


# --------------------------------------------------------------------------- #
#  Reglas que salen de rechazos reales
# --------------------------------------------------------------------------- #


def test_el_anticipo_se_liquida_con_99(leido):
    lineas = leido.sets["liquidacion"]["payload"]["documents"][3]["lines"]
    assert (lineas[0]["name"], lineas[0]["liquidated_type"]) == ("NETO ANTICIPO FACTURACIÓN", "99")


def test_valor_linea_es_cantidad_uno_por_precio(leido):
    item = leido.sets["exportacion_2"]["payload"]["documents"][0]["items"][0]
    assert (item["quantity"], item["unit_price"], item["surcharge_pct"]) == ("1", "14", 10)


def test_la_hoteleria_lleva_el_pasaporte(leido):
    doc = leido.sets["exportacion_2"]["payload"]["documents"][2]
    assert doc["service_indicator"] == 4
    assert [r["doc_type"] for r in doc["references"]] == [813]


def test_los_nombres_se_copian_con_tildes_y_enes(leido):
    basico = leido.sets["basico"]["payload"]["documents"]
    assert basico[0]["items"][0]["name"] == "Cajón AFECTO"
    assert basico[1]["items"][0]["name"] == "Pañuelo AFECTO"


def test_las_boletas_se_copian_sin_agregar_tildes(leido):
    """Ese archivo avisa: «Se omitieron las tildes»; se respeta tal cual."""
    boletas = leido.sets["boletas"]["payload"]["receipts"]
    assert boletas[0]["items"][1]["name"] == "Alineacion y balanceo"
    assert boletas[3]["items"][1].get("exempt") is True
    assert boletas[4]["items"][0]["unit"] == "Kg"


# --------------------------------------------------------------------------- #
#  Carga desde el panel
# --------------------------------------------------------------------------- #


def _archivos(*rutas_y_bytes):
    import base64

    return [
        {"name": nombre, "content_base64": base64.b64encode(datos).decode()}
        for nombre, datos in rutas_y_bytes
    ]


def _op(client, db):
    from tests.conftest import auth_header, make_user

    make_user(db, "op@dimabe.cl", "secret", "operator")
    return auth_header(client, "op@dimabe.cl", "secret")


def test_la_vista_previa_lee_sin_guardar(client, db):
    from app.db.models import CertificationSet
    from tests.conftest import make_customer

    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"/admin/customers/{c.id}/certification/sheet",
        json={"files": _archivos(("set.txt", HOJA.read_bytes()), ("be.txt", BOLETAS.read_bytes()))},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["loaded"] is False
    assert {s["kind"]: s["items"] for s in body["sets"]} == {
        "basico": 8, "libro_ventas": 0, "libro_compras": 7, "guias": 3, "libro_guias": 0,
        "exenta": 8, "exportacion_1": 3, "exportacion_2": 3, "liquidacion": 4,
        "factura_compra": 3, "boletas": 5,
    }  # fmt: skip
    assert body["notes"]  # el texto que no se interpreta se muestra
    assert db.query(CertificationSet).count() == 0


def test_cargar_deja_los_once_sets_definidos_y_sin_errores_de_contenido(client, db):
    from app.db.models import CertificationDefinition, CertificationSet
    from app.services import certification_checks
    from tests.conftest import make_customer

    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"/admin/customers/{c.id}/certification/sheet",
        json={
            "files": _archivos(("set.txt", HOJA.read_bytes()), ("be.txt", BOLETAS.read_bytes())),
            "dry_run": False,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["loaded"] is True
    assert db.query(CertificationSet).count() == 11
    assert db.query(CertificationDefinition).count() == 11
    claves = {
        chk["key"]
        for g in certification_checks.run(db, c)["groups"]
        for chk in g["checks"]
        if chk["key"].startswith("contenido_")
    }
    assert claves == set()


def test_un_archivo_con_errores_responde_con_la_linea(client, db):
    from tests.conftest import make_customer

    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"/admin/customers/{c.id}/certification/sheet",
        json={"files": _archivos(("set.txt", _con(("SAN ANTONIO", "PUERTO INVENTADO"))))},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("set.txt: línea ")
    assert "PUERTO INVENTADO" in r.json()["detail"]
