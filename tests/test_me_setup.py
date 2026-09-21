"""Puesta en marcha desde el ERP: certificado, CAF, folios y clave tributaria.

El ERP (Odoo) tiene que poder registrar todo lo del contribuyente con la
credencial del propio contribuyente. La alternativa era guardar en cada
instalación la clave de administración del servicio, que no está acotada a
ningún cliente: quien la tuviera podría operar sobre todos los inquilinos.
"""

import base64
import datetime as dt
import hashlib

import pytest

from app.security.service_codes import SERVICE_BHE, SERVICE_DTE
from app.services import certificate_service
from tests.conftest import fake_caf_xml, grant, headers, make_customer


def _describe(data: bytes, password: str) -> dict:
    return {
        "rut": "12291733-9",
        "due_date": dt.date(2030, 1, 1),
        "holder": "TITULAR DE PRUEBA",
        "issuer": "CA DE PRUEBA",
        "thumbprint": hashlib.sha256(data).hexdigest(),
    }


@pytest.fixture
def emisor(db):
    customer = make_customer(db, key="erp")
    grant(db, customer, SERVICE_DTE)
    return customer


def _caf(doc_type=33, desde=1, hasta=50, **kw) -> dict:
    return {"xml_base64": base64.b64encode(fake_caf_xml(doc_type, desde, hasta, **kw)).decode()}


def test_el_erp_carga_el_certificado_y_lo_ve_listado(client, emisor, monkeypatch):
    monkeypatch.setattr(certificate_service, "describe", _describe)

    r = client.post(
        "/me/certificate",
        json={"file_base64": base64.b64encode(b"un-pfx").decode(), "password": "pw"},
        headers=headers("erp"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["due_date"] == "2030-01-01"

    listado = client.get("/me/certificates", headers=headers("erp")).json()
    # El de la ficha de prueba y el recién cargado.
    assert len(listado) == 2
    nuevo = [c for c in listado if c["holder"] == "TITULAR DE PRUEBA"]
    assert nuevo and nuevo[0]["expired"] is False and nuevo[0]["rut"]


def test_el_erp_carga_un_caf_y_consulta_sus_folios(client, emisor):
    r = client.post("/me/caf", json=_caf(33, 1, 50), headers=headers("erp"))
    assert r.status_code == 200, r.text
    assert (r.json()["doc_type"], r.json()["folio_from"], r.json()["folio_to"]) == (33, 1, 50)

    cafs = client.get("/me/cafs", headers=headers("erp")).json()
    assert [(c["doc_type"], c["folio_from"], c["last_folio"]) for c in cafs] == [(33, 1, 0)]

    folios = client.get("/me/folios", headers=headers("erp")).json()
    assert folios[0]["doc_type"] == 33 and folios[0]["usable_remaining"] == 50


def test_el_caf_que_el_sii_rechazaria_no_entra(client, emisor):
    """Mismas guardas que por el portal: aquí, un CAF de otro ambiente."""
    r = client.post("/me/caf", json=_caf(33, 1, 50, idk=300), headers=headers("erp"))
    assert r.status_code == 400
    assert "producción" in r.text


def test_el_erp_retira_un_caf_para_estrenar_el_siguiente(client, emisor):
    viejo = client.post("/me/caf", json=_caf(33, 1, 50), headers=headers("erp")).json()
    client.post("/me/caf", json=_caf(33, 51, 60), headers=headers("erp"))

    r = client.post(f"/me/cafs/{viejo['id']}/retire", headers=headers("erp"))
    assert r.status_code == 200, r.text
    assert r.json()["exhausted"] is True

    folios = client.get("/me/folios", headers=headers("erp")).json()
    estados = {c["folio_from"]: c["state"] for c in folios[0]["cafs"]}
    # El segundo rango queda «por estrenar»: el puntero sigue en cero.
    assert estados == {1: "retired", 51: "pending"}


def test_la_clave_tributaria_se_guarda_y_no_se_devuelve(client, db):
    customer = make_customer(db, key="erp-bhe")
    grant(db, customer, SERVICE_BHE)

    assert client.get("/me/sii-key", headers=headers("erp-bhe")).json() == {"configured": False}

    r = client.post("/me/sii-key", json={"password": "clave-del-sii"}, headers=headers("erp-bhe"))
    assert r.status_code == 200 and r.json()["configured"] is True
    assert "clave-del-sii" not in r.text

    assert client.get("/me/sii-key", headers=headers("erp-bhe")).json() == {"configured": True}
    assert client.delete("/me/sii-key", headers=headers("erp-bhe")).json() == {"configured": False}


def test_cada_empresa_solo_registra_lo_suyo(client, db, emisor):
    """Lo que hace distinta a esta vía de la de administración."""
    otra = make_customer(db, rut="11111111-1", key="erp-2")
    grant(db, otra, SERVICE_DTE, apikey="otra")

    client.post("/me/caf", json=_caf(33, 1, 50), headers=headers("erp"))
    client.post(
        "/me/caf",
        json=_caf(61, 1, 9, rut="11111111-1"),
        headers=headers("erp-2", "otra"),
    )

    mios = client.get("/me/cafs", headers=headers("erp")).json()
    suyos = client.get("/me/cafs", headers=headers("erp-2", "otra")).json()
    assert [c["doc_type"] for c in mios] == [33]
    assert [c["doc_type"] for c in suyos] == [61]

    # Y no se puede retirar el CAF de la otra empresa ni conociendo su id.
    r = client.post(f"/me/cafs/{mios[0]['id']}/retire", headers=headers("erp-2", "otra"))
    assert r.status_code == 400


def test_sin_credencial_no_hay_puesta_en_marcha(client, emisor):
    assert client.get("/me/cafs").status_code in (401, 403, 422)
    assert client.post("/me/caf", json=_caf()).status_code in (401, 403, 422)


def test_el_registro_queda_en_la_auditoria(client, emisor, db):
    from app.db.models import AdminAudit

    client.post("/me/caf", json=_caf(33, 1, 50), headers=headers("erp"))

    fila = db.query(AdminAudit).filter(AdminAudit.action == "caf.upload").one()
    assert fila.actor_user_id is None  # lo hizo una máquina, no una persona
    assert "API del cliente" in fila.summary and "tipo 33" in fila.summary
