"""La plantilla de los diez sets: estructura correcta y sin datos del emisor."""

import pytest

from app.db.models import CertificationDefinition, CertificationSet
from app.services import certification_fill, certification_template
from tests.conftest import auth_header, make_customer, make_user


def _op(client, db):
    make_user(db, "op@dimabe.cl", "secret", "operator")
    return auth_header(client, "op@dimabe.cl", "secret")


def _base(cid):
    return f"/admin/customers/{cid}/certification"


def _documentos(plantilla):
    return plantilla["payload"].get("documents", [])


# --------------------------------------------------------------------------- #
#  Invariantes de la plantilla (sin BD)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plantilla", certification_template.SETS, ids=lambda p: p["kind"])
def test_la_plantilla_no_trae_nada_que_ponga_el_sistema(plantilla):
    """Ni emisor, ni fecha, ni la referencia al caso.

    Es el error que costó una certificación completa: definiciones con el
    emisor escrito dentro emitían a nombre del contribuyente anterior. La
    plantilla es de donde salen todos los expedientes nuevos, así que si se
    cuela aquí, se cuela en todos.
    """
    for doc in _documentos(plantilla):
        assert "issuer" not in doc
        assert "issue_date" not in doc
        for ref in doc.get("references", []):
            assert str(ref.get("doc_type")) != "SET"
    assert "period" not in plantilla["payload"]
    assert "notification_folio" not in plantilla["payload"]


@pytest.mark.parametrize("plantilla", certification_template.SETS, ids=lambda p: p["kind"])
def test_las_referencias_apuntan_a_un_documento_del_mismo_sobre(plantilla):
    """``batch_index`` es 1-based y no puede salirse del lote.

    Una nota que referencia la posición 9 de un sobre de 8 documentos se emite
    igual y el SII la rechaza después: aquí se ve al instante.
    """
    docs = _documentos(plantilla)
    for n, doc in enumerate(docs, start=1):
        for ref in doc.get("references", []):
            indice = ref.get("batch_index")
            if indice is None:
                continue
            assert 1 <= indice <= len(docs), f"doc{n} referencia la posición {indice}"
            assert indice < n, f"doc{n} referencia un documento posterior ({indice})"


@pytest.mark.parametrize("plantilla", certification_template.SETS, ids=lambda p: p["kind"])
def test_guardar_la_plantilla_no_le_quita_nada(plantilla):
    """``strip`` se aplica al importar: la plantilla debe pasar intacta.

    Si ``strip`` le quitara algo, el expediente creado no sería el que dice
    esta plantilla y nadie se enteraría hasta emitir.
    """
    limpia = certification_fill.strip(
        plantilla["endpoint"], plantilla["kind"], plantilla["payload"]
    )
    assert limpia == plantilla["payload"]


def test_los_libros_generados_no_traen_lineas():
    """Ventas y guías arman sus líneas con lo que el SII aceptó.

    Traerlas escritas las dejaría apuntando a folios que no existen.
    """
    for plantilla in certification_template.SETS:
        if plantilla["kind"] in certification_fill.GENERATED_BOOKS:
            assert "lines" not in plantilla["payload"]


def test_sets_for_import_omite_los_que_no_tienen_numero():
    sets = certification_template.sets_for_import({"basico": "5038170", "guias": "  "})
    assert list(sets) == ["basico"]
    assert sets["basico"]["endpoint"] == "issue-batch"


# --------------------------------------------------------------------------- #
#  Por API
# --------------------------------------------------------------------------- #


def test_el_catalogo_lista_los_diez_sets(client, db):
    c = make_customer(db)
    r = client.get(f"{_base(c.id)}/template", headers=_op(client, db))
    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo) == len(certification_template.SETS)

    por_kind = {s["kind"]: s for s in cuerpo}
    # Los libros de ventas y de guías no se transcriben: sólo piden su número.
    assert por_kind["libro_ventas"]["transcribe"] is False
    assert por_kind["libro_guias"]["transcribe"] is False
    # El de compras sí: sus documentos los entrega el SII en el propio set.
    assert por_kind["libro_compras"]["transcribe"] is True
    assert por_kind["basico"]["transcribe"] is True
    assert por_kind["basico"]["label"]
    assert por_kind["basico"]["help"]


def test_crear_desde_la_plantilla_deja_el_expediente_listo(client, db):
    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"{_base(c.id)}/template",
        json={"codes": {"basico": "5038170", "libro_ventas": "5038171"}},
        headers=h,
    )
    assert r.status_code == 200

    creados = {s.code: s for s in db.query(CertificationSet).filter_by(customer_id=c.id)}
    assert set(creados) == {"5038170", "5038171"}
    assert creados["5038170"].kind == "basico"

    definicion = (
        db.query(CertificationDefinition)
        .filter(CertificationDefinition.set_id == creados["5038170"].id)
        .one()
    )
    assert definicion.endpoint == "issue-batch"
    docs = definicion.payload["documents"]
    assert [d["type"] for d in docs] == [33, 33, 33, 33, 61, 61, 61, 56]
    # La nota de débito anula la primera nota de crédito, que es el documento 5.
    assert docs[7]["references"][0]["batch_index"] == 5


def test_crear_desde_la_plantilla_sin_ningun_numero_no_crea_nada(client, db):
    c = make_customer(db)
    r = client.post(f"{_base(c.id)}/template", json={"codes": {}}, headers=_op(client, db))
    assert r.status_code == 400
    assert db.query(CertificationSet).filter_by(customer_id=c.id).count() == 0


def test_la_plantilla_no_pisa_el_numero_de_atencion_de_otro_set(client, db):
    """Crear dos veces con el mismo número reutiliza el set, no lo duplica."""
    c = make_customer(db)
    h = _op(client, db)
    for _ in range(2):
        r = client.post(f"{_base(c.id)}/template", json={"codes": {"basico": "5038170"}}, headers=h)
        assert r.status_code == 200
    assert db.query(CertificationSet).filter_by(customer_id=c.id, code="5038170").count() == 1
