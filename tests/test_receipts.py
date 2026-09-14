"""Boleta electrónica (39/41) y consumo de folios (RCOF) por la API."""

import base64

import pytest
from dte_chile.sii_client import SubmissionResult
from lxml import etree

from app.security.service_codes import SERVICE_DTE
from app.services import customer_service, receipt_service
from tests.conftest import fake_caf_xml, grant, headers, make_customer

_ISSUER = {
    "rut": "76158145-7",
    "business_name": "DEMO SPA",
    "activity": "Venta al por menor",
    "economic_activity": 471000,
    "address": "Calle 1",
    "commune": "Santiago",
    "city": "Santiago",
}


def _receipt(items, **over):
    base = {"type": 39, "issue_date": "2026-09-01", "issuer": _ISSUER, "items": items}
    base.update(over)
    return base


def _set_boletas():
    """Los cinco casos del set de prueba de boleta, con precios con IVA."""
    return [
        _receipt(
            [
                {"name": "Cambio de aceite", "quantity": 1, "unit_price": 19900},
                {"name": "Alineacion y balanceo", "quantity": 1, "unit_price": 9900},
            ]
        ),
        _receipt([{"name": "Papel de regalo", "quantity": 17, "unit_price": 120}]),
        _receipt(
            [
                {"name": "Sandwic", "quantity": 2, "unit_price": 1500},
                {"name": "Bebida", "quantity": 2, "unit_price": 550},
            ]
        ),
        _receipt(
            [
                {"name": "item afecto 1", "quantity": 8, "unit_price": 1590},
                {"name": "item exento 2", "quantity": 2, "unit_price": 1000, "exempt": True},
            ]
        ),
        _receipt([{"name": "Arroz", "quantity": 5, "unit_price": 700, "unit": "Kg"}]),
    ]


def _payload(receipts=None, **over):
    body = {
        "receipts": receipts if receipts is not None else _set_boletas(),
        "send": False,
        "validate_xsd": False,
    }
    body.update(over)
    return body


@pytest.fixture
def fake_receipt_engine(monkeypatch):
    """Reemplaza construcción/firma/sobre del motor por stubs."""
    captured: dict = {"receipts": [], "envelopes": 0}

    def _build(dte, caf, ts):
        captured["receipts"].append(dte)
        # Un elemento real, no un string: el servicio lo serializa para guardarlo.
        ns = "http://www.sii.cl/SiiDte"
        node = etree.Element(f"{{{ns}}}DTE", nsmap={None: ns}, version="1.0")
        etree.SubElement(node, f"{{{ns}}}Documento", ID=f"F{dte.folio}T39")
        return node

    def _envelope(signed, cover, cert, ts):
        captured["cover"] = cover
        captured["signed"] = signed
        captured["envelopes"] += 1
        return "ENV"

    monkeypatch.setattr(receipt_service, "build_receipt", _build)
    monkeypatch.setattr(receipt_service, "sign_document", lambda doc, cert: doc)
    monkeypatch.setattr(receipt_service, "build_receipt_envelope", _envelope)
    monkeypatch.setattr(receipt_service, "serialize", lambda env: b"<EnvioBOLETA/>")
    return captured


def _setup(db, types=(39,)):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    for doc_type in types:
        customer_service.add_caf(
            db, customer, base64.b64encode(fake_caf_xml(doc_type, 1, 20)).decode()
        )
    return customer


# --------------------------------------------------------------------------- #
#  Emisión del set
# --------------------------------------------------------------------------- #
def test_whole_set_goes_in_one_envelope(client, db, fake_receipt_engine):
    """El SII exige que el set de boletas viaje en un solo archivo."""
    _setup(db)
    r = client.post("/boletas/issue-batch", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text

    body = r.json()
    assert len(body["receipts"]) == 5
    assert fake_receipt_engine["envelopes"] == 1
    assert [x["folio"] for x in body["receipts"]] == [1, 2, 3, 4, 5]
    assert base64.b64decode(body["xml_base64"]) == b"<EnvioBOLETA/>"


def test_gross_prices_are_split_into_net_and_vat(client, db, fake_receipt_engine):
    """El set da precios con IVA: el neto se despeja, y debe sumar exacto."""
    _setup(db)
    client.post("/boletas/issue-batch", json=_payload(), headers=headers())

    totals = [
        (r.net_amount, r.vat, r.exempt_amount, r.total_amount)
        for r in fake_receipt_engine["receipts"]
    ]
    assert totals == [
        (25042, 4758, 0, 29800),
        (1714, 326, 0, 2040),
        (3445, 655, 0, 4100),
        (10689, 2031, 2000, 14720),
        (2941, 559, 0, 3500),
    ]
    for net, vat, exempt, total in totals:
        assert net + vat + exempt == total


def test_receiver_defaults_to_the_anonymous_consumer(client, db, fake_receipt_engine):
    _setup(db)
    client.post("/boletas/issue-batch", json=_payload(), headers=headers())
    assert fake_receipt_engine["receipts"][0].receiver.rut.value == "66666666-6"


def test_subtotals_group_by_type(client, db, fake_receipt_engine):
    _setup(db, types=(39, 41))
    receipts = _set_boletas() + [
        _receipt([{"name": "Servicio exento", "quantity": 1, "unit_price": 5000}], type=41)
    ]
    client.post("/boletas/issue-batch", json=_payload(receipts), headers=headers())
    assert fake_receipt_engine["cover"].subtotals == [(39, 5), (41, 1)]


def test_foreign_issuer_names_the_offending_receipt(client, db, fake_receipt_engine):
    _setup(db)
    receipts = _set_boletas()
    receipts[2] = _receipt(
        [{"name": "X", "quantity": 1, "unit_price": 1000}],
        issuer=dict(_ISSUER, rut="11111111-1"),
    )
    r = client.post("/boletas/issue-batch", json=_payload(receipts), headers=headers())
    assert r.status_code == 400, r.text
    assert "Boleta 3" in r.json()["error"]["message"]


def test_bad_receipt_burns_no_folio(client, db, fake_receipt_engine):
    _setup(db)
    receipts = _set_boletas()
    receipts.append(_receipt([{"name": "Ítem — sucio", "quantity": 1, "unit_price": 100}]))
    r = client.post("/boletas/issue-batch", json=_payload(receipts), headers=headers())
    assert r.status_code == 422, r.text

    r = client.post("/boletas/issue-batch", json=_payload(), headers=headers())
    assert r.status_code == 200 and r.json()["receipts"][0]["folio"] == 1


def test_empty_batch_is_rejected(client, db):
    _setup(db)
    r = client.post("/boletas/issue-batch", json=_payload([]), headers=headers())
    assert r.status_code == 422, r.text


def test_upload_returns_the_track_id(client, db, fake_receipt_engine, monkeypatch):
    _setup(db)

    class _FakeClient:
        def __init__(self, *a, **k):
            self.session = type("S", (), {"close": lambda self: None})()

        def send_receipts(self, xml, issuer_rut, sender_rut):
            return SubmissionResult(track_id="123456789012345", status="0", detail="OK")

    monkeypatch.setattr(receipt_service, "ReceiptClient", _FakeClient)
    r = client.post("/boletas/issue-batch", json=_payload(send=True), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["track_id"] == "123456789012345"


# --------------------------------------------------------------------------- #
#  Consumo de folios
# --------------------------------------------------------------------------- #
_REPORT = {
    "start_date": "2026-09-01",
    "end_date": "2026-09-01",
    "sequence": 1,
    "lines": [
        {
            "doc_type": 39,
            "folio": 1,
            "net_amount": 25042,
            "vat_amount": 4758,
            "total_amount": 29800,
        },
        {"doc_type": 39, "folio": 2, "net_amount": 1714, "vat_amount": 326, "total_amount": 2040},
        {"doc_type": 39, "folio": 6, "voided": True},
    ],
    "send": False,
    "validate_xsd": False,
}


@pytest.fixture
def fake_report_engine(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        receipt_service,
        "build_folio_report",
        lambda cover, cert, ts: captured.setdefault("cover", cover),
    )
    monkeypatch.setattr(receipt_service, "serialize_report", lambda x: b"<ConsumoFolios/>")
    return captured


def test_folio_report_is_built(client, db, fake_report_engine):
    _setup(db)
    r = client.post("/boletas/folio-report", json=_REPORT, headers=headers())
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["xml_base64"]) == b"<ConsumoFolios/>"


def test_folio_report_carries_the_lines_and_voided_flag(client, db, fake_report_engine):
    _setup(db)
    client.post("/boletas/folio-report", json=_REPORT, headers=headers())

    cover = fake_report_engine["cover"]
    assert len(cover.lines) == 3
    assert cover.lines[2].voided is True
    assert cover.sequence == 1


def test_backwards_period_is_rejected(client, db, fake_report_engine):
    """Se ataja en el schema, no sólo en el motor: es un invariante del request."""
    _setup(db)
    payload = dict(_REPORT, start_date="2026-09-02", end_date="2026-09-01")
    r = client.post("/boletas/folio-report", json=payload, headers=headers())
    assert r.status_code == 422, r.text
    assert "termina antes de empezar" in r.text


def test_report_needs_lines(client, db, fake_report_engine):
    _setup(db)
    r = client.post("/boletas/folio-report", json=dict(_REPORT, lines=[]), headers=headers())
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
#  Almacenamiento para la consulta pública
# --------------------------------------------------------------------------- #
def test_issued_receipts_are_stored_for_the_consumer(client, db, fake_receipt_engine):
    """Sin guardarlas, boletas.dimabe.cl no tendría qué mostrar."""
    from app.db.models import IssuedReceipt

    _setup(db)
    r = client.post("/boletas/issue-batch", json=_payload(validate_xsd=False), headers=headers())
    assert r.status_code == 200, r.text

    rows = db.query(IssuedReceipt).order_by(IssuedReceipt.folio).all()
    assert [x.folio for x in rows] == [1, 2, 3, 4, 5]
    assert [x.total_amount for x in rows] == [29800, 2040, 4100, 14720, 3500]
    assert all(x.doc_type == 39 for x in rows)


def test_stored_xml_is_the_single_document_not_the_envelope(client, db, fake_receipt_engine):
    """Guardar el sobre expondría las boletas de otros compradores."""
    from app.core import crypto
    from app.db.models import IssuedReceipt

    _setup(db)
    client.post("/boletas/issue-batch", json=_payload(validate_xsd=False), headers=headers())

    row = db.query(IssuedReceipt).filter(IssuedReceipt.folio == 1).one()
    xml = crypto.decrypt(row.xml_encrypted)
    assert b"<DTE" in xml
    assert b"EnvioBOLETA" not in xml
    assert xml.count(b"<Documento") == 1
