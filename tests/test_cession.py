"""Cesión electrónica de facturas: el AEC se arma con el documento archivado."""

import base64
import datetime as dt

import pytest

from app.core import crypto
from app.db.models import IssuedDocument
from app.security.service_codes import SERVICE_DTE
from app.services import cession_service
from tests.conftest import grant, headers, make_customer

DTE_ARCHIVADO = b"""<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0">
 <Documento ID="F108T33"><Encabezado>
  <IdDoc><TipoDTE>33</TipoDTE><Folio>108</Folio><FchEmis>2026-09-01</FchEmis></IdDoc>
  <Emisor><RUTEmisor>76158145-7</RUTEmisor></Emisor>
  <Receptor><RUTRecep>76192083-9</RUTRecep></Receptor>
  <Totales><MntTotal>119000</MntTotal></Totales>
 </Encabezado></Documento>
</DTE>"""


def _payload(**kwargs):
    cuerpo = {
        "doc_type": 33,
        "folio": 108,
        "assignor": {
            "rut": "76158145-7",
            "name": "Demo",
            "address": "Cordova 298",
            "email": "pagos@demo.cl",
        },
        "assignee": {
            "rut": "96667560-8",
            "name": "FACTORING DE PRUEBA S.A.",
            "address": "Apoquindo 1000",
            "email": "ops@factoring.cl",
        },
        "signatory": {"rut": "12291733-9", "name": "ARTURO MUNOZ"},
        "amount": 119000,
        "due_date": "2026-11-30",
        "send": False,
    }
    cuerpo.update(kwargs)
    return cuerpo


def _setup(db, doc_type=33, folio=108, xml=DTE_ARCHIVADO):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    db.add(
        IssuedDocument(
            customer_id=customer.id,
            doc_type=doc_type,
            folio=folio,
            xml_encrypted=crypto.encrypt(xml),
        )
    )
    db.commit()
    return customer


@pytest.fixture
def fake_cession_engine(monkeypatch):
    """El armado del AEC lo prueba el motor; aquí importa el flujo."""
    visto = {}

    def _build(cession, cert, ts):
        visto["cession"] = cession
        return "AEC"

    monkeypatch.setattr(cession_service, "build_aec", _build)
    monkeypatch.setattr(cession_service, "serialize", lambda aec: b"<AEC/>")
    return visto


def test_la_cesion_usa_el_documento_archivado(client, db, fake_cession_engine):
    """El ERP dice qué folio cede; el XML lo pone el facturador.

    Un AEC que lleva un documento distinto del emitido lo rechaza el SII, y
    para cuando lo rechaza el factoring ya pagó.
    """
    _setup(db)
    r = client.post("/cession", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["xml_base64"]) == b"<AEC/>"

    cesion = fake_cession_engine["cession"]
    assert cesion.dte.findtext(".//{http://www.sii.cl/SiiDte}Folio") == "108"
    assert cesion.assignee.rut == "96667560-8"
    assert cesion.amount == 119000
    assert cesion.due_date == dt.date(2026, 11, 30)


def test_no_se_cede_un_documento_que_no_se_emitio(client, db, fake_cession_engine):
    _setup(db)
    r = client.post("/cession", json=_payload(folio=999), headers=headers())
    assert r.status_code == 400, r.text
    assert "folio 999" in r.json()["error"]["message"]


def test_no_se_cede_una_factura_ajena(client, db, fake_cession_engine):
    """El cedente tiene que ser el propio contribuyente."""
    _setup(db)
    ajeno = dict(_payload()["assignor"], rut="77262159-0")
    r = client.post("/cession", json=_payload(assignor=ajeno), headers=headers())
    assert r.status_code == 400, r.text
    assert "sólo se ceden facturas propias" in r.json()["error"]["message"]


def test_el_monto_cedido_tiene_que_ser_positivo(client, db, fake_cession_engine):
    _setup(db)
    r = client.post("/cession", json=_payload(amount=0), headers=headers())
    assert r.status_code == 422, r.text


def test_la_cesion_exige_credencial_de_dte(client, db, fake_cession_engine):
    make_customer(db)  # sin grant
    r = client.post("/cession", json=_payload(), headers=headers())
    assert r.status_code == 401
