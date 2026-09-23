"""Emisión de documentos de exportación (110/111/112)."""

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
    "rut": "55555555-5",  # extranjero: el comprador no tiene RUT chileno
    "business_name": "Comprador Extranjero",
    "activity": "Importador",
    "address": "Gran Via 1",
    "commune": "Madrid",
    "city": "Madrid",
}


@pytest.fixture
def fake_export_engine(monkeypatch):
    monkeypatch.setattr(dte_service, "build_export", lambda *a: "DOC")
    monkeypatch.setattr(dte_service, "sign_document", lambda *a: "SIGNED")
    monkeypatch.setattr(dte_service, "build_envelope", lambda *a: "ENV")
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")


def _payload(**kwargs):
    cuerpo = {
        "type": 110,
        "issue_date": "2026-09-23",
        "issuer": ISSUER,
        "receiver": RECEIVER,
        "currency": "DOLAR USA",
        "items": [{"name": "Servicio", "quantity": "2", "unit_price": "1500.50"}],
        "customs": {"sale_mode": 1, "sale_clause": 5, "destination_country": 517},
        "other_currency": {"exchange_rate": "950.50"},
        "send": False,
        "validate_xsd": False,
    }
    cuerpo.update(kwargs)
    return cuerpo


def _setup(db, doc_type=110):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    customer_service.add_caf(db, customer, base64.b64encode(fake_caf_xml(doc_type, 1, 5)).decode())
    return customer


def test_la_exportacion_toma_su_propio_folio(client, db, fake_export_engine):
    _setup(db)
    r = client.post("/dte/issue-export", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == 1


def test_la_exportacion_emite_con_el_folio_reservado(client, db, fake_export_engine):
    """El ERP numera el documento antes de emitirlo, como en el DTE nacional.

    Sin esto se gastan dos folios por documento: el que reservó el ERP para
    numerarlo y el que asignaría esta emisión.
    """
    from app.db.models import FolioAssignment
    from app.services import folio_service

    customer = _setup(db)
    folio, _caf = folio_service.next_folio(db, customer.id, 110, "reserva")

    r = client.post("/dte/issue-export", json=_payload(folio=folio), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == folio
    assert db.query(FolioAssignment).count() == 1


def test_no_se_emite_con_un_folio_que_no_se_reservo(client, db, fake_export_engine):
    """Emitir con un folio que nadie reservó rompería la trazabilidad."""
    _setup(db)
    r = client.post("/dte/issue-export", json=_payload(folio=4), headers=headers())
    assert r.status_code == 409, r.text
    assert "no está reservado" in r.json()["error"]["message"]
