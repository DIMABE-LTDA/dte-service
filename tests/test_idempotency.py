"""Reintentar una emisión no debe emitir dos veces.

El escenario que esto cierra: el servicio toma el folio, firma y sube el
documento al SII, y la respuesta se pierde de vuelta. El ERP ve un error, el
usuario vuelve a pulsar «Emitir» y salía un segundo documento, con otro folio,
por la misma venta.
"""

import base64

import pytest
from dte_chile.sii_client import SubmissionResult
from lxml import etree

from app.security.service_codes import SERVICE_DTE
from app.services import customer_service, dte_service
from tests.conftest import fake_caf_xml, grant, headers, make_customer

_EMISOR = {
    "rut": "76158145-7",
    "business_name": "DEMO SPA",
    "activity": "Venta al por menor",
    "economic_activity": 471000,
    "address": "Calle 1",
    "commune": "Santiago",
    "city": "Santiago",
}
_RECEPTOR = {
    "rut": "69507000-4",
    "business_name": "CLIENTE",
    "activity": "Comercio",
    "address": "Alameda 1",
    "commune": "Santiago",
}


def _documento(**over) -> dict:
    cuerpo = {
        "type": 33,
        "issue_date": "2026-09-21",
        "issuer": _EMISOR,
        "receiver": _RECEPTOR,
        "items": [{"name": "Servicio", "quantity": 1, "unit_price": 10000}],
        "send": False,
        "validate_xsd": False,
    }
    cuerpo.update(over)
    return cuerpo


@pytest.fixture(autouse=True)
def motor_sin_firma(monkeypatch):
    """El documento se construye de verdad —con su timbre— pero no se firma.

    Firmar exige un certificado real; el resto de los tests reemplaza el motor
    entero por cadenas, y aquí hace falta XML de verdad para comprobar que el
    documento emitido queda archivado y se puede recuperar.
    """
    ns = "http://www.sii.cl/SiiDte"

    def _envelope(signed, cover, cert, ts):
        root = etree.Element(f"{{{ns}}}EnvioDTE", nsmap={None: ns}, version="1.0")
        set_dte = etree.SubElement(root, f"{{{ns}}}SetDTE", ID="SetDoc")
        for node in signed:
            # Cada documento va dentro de su <DTE>, como en el sobre real.
            dte = etree.SubElement(set_dte, f"{{{ns}}}DTE", version="1.0")
            dte.append(node)
        return root

    monkeypatch.setattr(dte_service, "sign_document", lambda node, cert: node)
    monkeypatch.setattr(dte_service, "build_envelope", _envelope)


@pytest.fixture
def emisor(db):
    customer = make_customer(db, key="erp")
    grant(db, customer, SERVICE_DTE)
    for tipo in (33,):
        customer_service.add_caf(db, customer, base64.b64encode(fake_caf_xml(tipo, 1, 50)).decode())
    return customer


def _clave(valor="odoo:demo:1") -> dict:
    return {**headers("erp"), "Idempotency-Key": valor}


def test_el_reintento_devuelve_el_mismo_documento_y_no_gasta_otro_folio(client, emisor, db):
    from app.db.models import FolioAssignment

    primera = client.post("/dte/issue", json=_documento(), headers=_clave())
    assert primera.status_code == 200, primera.text

    segunda = client.post("/dte/issue", json=_documento(), headers=_clave())
    assert segunda.status_code == 200, segunda.text
    assert segunda.json() == primera.json()
    assert db.query(FolioAssignment).count() == 1


def test_sin_clave_cada_peticion_emite_su_documento(client, emisor):
    """La clave es opcional: quien no la manda, sigue como antes."""
    uno = client.post("/dte/issue", json=_documento(), headers=headers("erp"))
    otro = client.post("/dte/issue", json=_documento(), headers=headers("erp"))
    assert uno.json()["folio"] == 1 and otro.json()["folio"] == 2


def test_claves_distintas_emiten_documentos_distintos(client, emisor):
    uno = client.post("/dte/issue", json=_documento(), headers=_clave("odoo:demo:1"))
    otro = client.post("/dte/issue", json=_documento(), headers=_clave("odoo:demo:2"))
    assert uno.json()["folio"] == 1 and otro.json()["folio"] == 2


def test_la_respuesta_perdida_se_recupera_al_reintentar(client, emisor, db, monkeypatch):
    """El caso real: el documento se emitió y la respuesta no llegó.

    Se simula cortando la conexión justo después de subir al SII: el folio ya
    se gastó y el documento existe. El reintento con la misma clave tiene que
    devolver ese documento, no emitir otro.
    """
    from app.db.models import FolioAssignment, IssuedDocument

    enviado = {}

    def _subir(customer, cert, xml, issuer_rut, settings):
        enviado["xml"] = xml
        return SubmissionResult(track_id="0259000001", status="0", detail="OK")

    monkeypatch.setattr(dte_service, "_send", _subir)

    # Primera petición: emite y sube; el cliente "no recibe" la respuesta.
    primera = client.post("/dte/issue", json=_documento(send=True), headers=_clave())
    assert primera.status_code == 200 and enviado
    folio = primera.json()["folio"]

    # El usuario vuelve a pulsar «Emitir».
    segunda = client.post("/dte/issue", json=_documento(send=True), headers=_clave())
    assert segunda.json()["folio"] == folio
    assert segunda.json()["submission"]["track_id"] == "0259000001"
    assert db.query(FolioAssignment).count() == 1
    assert db.query(IssuedDocument).count() == 1


def test_un_error_de_datos_se_puede_corregir_y_reintentar_con_la_misma_clave(client, emisor):
    """Un documento mal armado no gasta nada: la clave queda libre."""
    linea = {"name": "X", "quantity": 1, "unit_price": 10000, "discount_pct": 200}
    malo = _documento(items=[linea])
    r = client.post("/dte/issue", json=malo, headers=_clave())
    assert r.status_code in (400, 422)

    bueno = client.post("/dte/issue", json=_documento(), headers=_clave())
    assert bueno.status_code == 200, bueno.text


def test_si_el_envio_al_sii_falla_la_clave_no_se_reusa_sola(client, emisor, monkeypatch):
    """Ahí sí pudo quedar un folio gastado: reintentar a ciegas duplicaría."""
    from dte_chile.errors import SiiUploadError

    def _falla(customer, cert, xml, issuer_rut, settings):
        raise SiiUploadError("el SII no respondió")

    monkeypatch.setattr(dte_service, "_send", _falla)
    primera = client.post("/dte/issue", json=_documento(send=True), headers=_clave())
    assert primera.status_code >= 400

    segunda = client.post("/dte/issue", json=_documento(send=True), headers=_clave())
    assert segunda.status_code == 409
    assert "revisa el inventario de folios" in segunda.json()["detail"]


def test_el_documento_emitido_queda_guardado_y_se_puede_recuperar(client, emisor, db):
    r = client.post("/dte/issue", json=_documento(), headers=_clave())
    folio = r.json()["folio"]

    x = client.get(f"/dte/33/{folio}/xml", headers=headers("erp"))
    assert x.status_code == 200, x.text
    assert x.headers["content-type"].startswith("application/xml")
    assert b"<DTE" in x.content and b"EnvioDTE" not in x.content

    assert client.get("/dte/33/999/xml", headers=headers("erp")).status_code == 404


def test_el_documento_de_otra_empresa_no_se_puede_recuperar(client, emisor, db):
    otra = make_customer(db, rut="11111111-1", key="erp-2")
    grant(db, otra, SERVICE_DTE, apikey="otra")

    r = client.post("/dte/issue", json=_documento(), headers=_clave())
    folio = r.json()["folio"]

    ajeno = client.get(f"/dte/33/{folio}/xml", headers=headers("erp-2", "otra"))
    assert ajeno.status_code == 404


def test_la_clave_es_de_cada_empresa(client, emisor, db):
    """Dos ERP distintos pueden usar la misma clave sin estorbarse."""
    otra = make_customer(db, rut="11111111-1", key="erp-2")
    grant(db, otra, SERVICE_DTE, apikey="otra")
    customer_service.add_caf(
        db, otra, base64.b64encode(fake_caf_xml(33, 1, 50, rut="11111111-1")).decode()
    )

    mio = client.post("/dte/issue", json=_documento(), headers=_clave("misma-clave"))
    suyo_emisor = {**_EMISOR, "rut": "11111111-1"}
    suyo = client.post(
        "/dte/issue",
        json=_documento(issuer=suyo_emisor),
        headers={**headers("erp-2", "otra"), "Idempotency-Key": "misma-clave"},
    )
    assert mio.status_code == 200 and suyo.status_code == 200, suyo.text


def test_emitir_en_lote_tambien_es_idempotente(client, emisor, db):
    from app.db.models import FolioAssignment

    lote = {
        "documents": [_documento(), _documento()],
        "send": False,
        "validate_xsd": False,
    }
    for d in lote["documents"]:
        d.pop("send", None)
        d.pop("validate_xsd", None)

    primera = client.post("/dte/issue-batch", json=lote, headers=_clave("lote-1"))
    assert primera.status_code == 200, primera.text
    segunda = client.post("/dte/issue-batch", json=lote, headers=_clave("lote-1"))
    assert segunda.json() == primera.json()
    assert db.query(FolioAssignment).count() == 2


def test_el_impreso_sale_del_documento_archivado(client, emisor, db):
    """El ERP no tiene que devolver el sobre para reimprimir."""
    r = client.post("/dte/issue", json=_documento(), headers=_clave())
    folio = r.json()["folio"]

    html = client.get(f"/dte/33/{folio}/print?format=html", headers=headers("erp"))
    assert html.status_code == 200, html.text
    assert "FACTURA ELECTRÓNICA" in html.text
    assert f"N° {folio}" in html.text
    # Las dos copias: la tributaria y la cedible.
    assert html.text.count("<div class=\"doc\">") == 2

    solo_tributario = client.get(
        f"/dte/33/{folio}/print?format=html&copies=tax", headers=headers("erp")
    )
    assert solo_tributario.text.count("<div class=\"doc\">") == 1
    assert "CEDIBLE" not in solo_tributario.text

    assert client.get("/dte/33/9999/print", headers=headers("erp")).status_code == 404
