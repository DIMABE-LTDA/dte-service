"""Emisión de la Liquidación Factura Electrónica (43)."""

import base64

import pytest

from app.security.service_codes import SERVICE_DTE
from app.services import customer_service, dte_service
from tests.conftest import fake_caf_xml, grant, headers, make_customer

ISSUER = {
    "rut": "76158145-7",
    "business_name": "Demo",
    "activity": "Servicios",
    "economic_activity": 620200,
    "address": "Cordova 298",
    "commune": "Santiago",
    "city": "Santiago",
}
RECEIVER = {
    "rut": "76192083-9",
    "business_name": "Mandante Ltda",
    "activity": "Comercio",
    "address": "Calle Falsa 123",
    "commune": "Santiago",
    "city": "Santiago",
}


@pytest.fixture
def fake_settlement_engine(monkeypatch):
    monkeypatch.setattr(dte_service, "build_settlement", lambda *a: "DOC")
    monkeypatch.setattr(dte_service, "sign_document", lambda *a: "SIGNED")
    monkeypatch.setattr(dte_service, "build_envelope", lambda *a: "ENV")
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")


def _payload(**kwargs):
    cuerpo = {
        "issue_date": "2026-09-23",
        "issuer": ISSUER,
        "receiver": RECEIVER,
        "lines": [{"liquidated_type": "33", "name": "Ventas del período", "amount": 500000}],
        "commissions": [{"description": "Comisión 10%", "net_amount": 50000, "rate": 10}],
        "send": False,
        "validate_xsd": False,
    }
    cuerpo.update(kwargs)
    return cuerpo


def _setup(db):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    customer_service.add_caf(db, customer, base64.b64encode(fake_caf_xml(43, 1, 5)).decode())
    return customer


def test_la_liquidacion_toma_su_propio_folio(client, db, fake_settlement_engine):
    _setup(db)
    r = client.post("/dte/issue-settlement", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == 1


def test_la_liquidacion_emite_con_el_folio_reservado(client, db, fake_settlement_engine):
    """Como el resto: el ERP numera el documento antes de emitirlo.

    Sin esto se gastan dos folios por liquidación, el reservado y el que
    asignaría esta emisión.
    """
    from app.db.models import FolioAssignment
    from app.services import folio_service

    customer = _setup(db)
    folio, _caf = folio_service.next_folio(db, customer.id, 43, "reserva")

    r = client.post("/dte/issue-settlement", json=_payload(folio=folio), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == folio
    assert db.query(FolioAssignment).count() == 1


def test_no_se_liquida_con_un_folio_que_no_se_reservo(client, db, fake_settlement_engine):
    _setup(db)
    r = client.post("/dte/issue-settlement", json=_payload(folio=4), headers=headers())
    assert r.status_code == 409, r.text
    assert "no está reservado" in r.json()["error"]["message"]
