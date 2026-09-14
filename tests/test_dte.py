import base64

import pytest
from dte_chile.sii_client import SubmissionResult

from app.security.service_codes import SERVICE_DTE
from app.services import customer_service, dte_service, sii_upload
from tests.conftest import fake_caf_xml, grant, headers, make_customer


class _FakeSession:
    """Stub de la requests.Session que el endpoint cierra tras usar el SIIClient."""

    def close(self):
        pass


_ISSUER = {
    "rut": "76158145-7",
    "business_name": "DEMO SPA",
    "activity": "Venta",
    "economic_activity": 471000,
    "address": "Calle 1",
    "commune": "Santiago",
    "city": "Santiago",
}
_RECEIVER = {
    "rut": "77073851-2",
    "business_name": "CLIENTE SPA",
    "activity": "Compra",
    "address": "Calle 2",
    "commune": "Santiago",
    "city": "Santiago",
}


def _payload(**over):
    base = {
        "type": 33,
        "issue_date": "2026-06-09",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "items": [{"name": "Producto", "quantity": 1, "unit_price": 1000, "exempt": False}],
        "references": [],
        "validate_xsd": False,
    }
    base.update(over)
    return base


@pytest.fixture
def fake_dte_engine(monkeypatch):
    """Reemplaza build/firma/sobre/serialización del motor por stubs."""
    monkeypatch.setattr(dte_service, "build_document", lambda *a: "DOC")
    monkeypatch.setattr(dte_service, "sign_document", lambda *a: "SIGNED")
    monkeypatch.setattr(dte_service, "build_envelope", lambda *a: "ENV")
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")


def _setup(db, doc_type=33, folio_from=1, folio_to=5):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    customer_service.add_caf(
        db, customer, base64.b64encode(fake_caf_xml(doc_type, folio_from, folio_to)).decode()
    )
    return customer


def test_issue_allocates_folio_without_send(client, db, fake_dte_engine):
    _setup(db)
    r = client.post("/dte/issue", json=_payload(send=False), headers=headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["folio"] == 1
    assert body["type"] == 33
    assert body["submission"] is None
    assert base64.b64decode(body["xml_base64"]) == b"<EnvioDTE/>"


def test_issue_increments_folio_no_duplicates(client, db, fake_dte_engine):
    _setup(db)
    f1 = client.post("/dte/issue", json=_payload(send=False), headers=headers()).json()["folio"]
    f2 = client.post("/dte/issue", json=_payload(send=False), headers=headers()).json()["folio"]
    assert (f1, f2) == (1, 2)


def test_issue_with_send_returns_submission(client, db, fake_dte_engine, monkeypatch):
    _setup(db)

    class _FakeSII:
        def __init__(self, cert, environment, timeout=30):
            self.session = _FakeSession()

        def send_dte(self, xml, issuer_rut, sender_rut):
            return SubmissionResult(track_id="T123", status="OK", detail="recibido")

    monkeypatch.setattr(sii_upload, "SIIClient", _FakeSII)

    r = client.post("/dte/issue", json=_payload(send=True), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["track_id"] == "T123"


def test_issue_requires_dte_service(client, db, fake_dte_engine):
    make_customer(db)  # sin grant de DTE
    r = client.post("/dte/issue", json=_payload(send=False), headers=headers())
    assert r.status_code == 401


def test_status_query_ok(client, db, monkeypatch):
    """GET /dte/status consulta el estado en el SII y cierra la sesión HTTP."""
    _setup(db)  # grant DTE + cert

    import app.routers.dte as dte_router

    class _FakeSII:
        def __init__(self, *a, **k):
            self.session = _FakeSession()

        def query_status(self, track_id, rut):
            return SubmissionResult(track_id=track_id, status="ACEPTADO", detail="ok")

    monkeypatch.setattr(dte_router, "SIIClient", _FakeSII)
    r = client.get("/dte/status/T999", headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACEPTADO" and r.json()["track_id"] == "T999"


def test_issue_records_folio_assignment(client, db, fake_dte_engine):
    customer = _setup(db)
    client.post("/dte/issue", json=_payload(send=False), headers=headers())

    from app.db.models import FolioAssignment

    rows = db.query(FolioAssignment).filter_by(customer_id=customer.id).all()
    assert len(rows) == 1
    assert rows[0].folio == 1 and rows[0].doc_type == 33 and rows[0].status == "issued"


def test_issue_send_failure_marks_folio_failed_but_consumed(
    client, db, fake_dte_engine, monkeypatch
):
    """Si el envío al SII falla, el folio queda trazado como 'failed' y consumido."""
    customer = _setup(db)

    class _BoomSII:
        def __init__(self, *a, **k):
            self.session = _FakeSession()

        def send_dte(self, *a):
            raise RuntimeError("SII caído")

    monkeypatch.setattr(sii_upload, "SIIClient", _BoomSII)
    r = client.post("/dte/issue", json=_payload(send=True), headers=headers())
    assert r.status_code == 500

    from app.db.models import FolioAssignment

    row = db.query(FolioAssignment).filter_by(customer_id=customer.id, folio=1).one()
    assert row.status == "failed"

    # El folio 1 se quemó: la siguiente emisión (sin envío) usa el folio 2.
    f = client.post("/dte/issue", json=_payload(send=False), headers=headers()).json()["folio"]
    assert f == 2


def test_unhandled_500_is_logged(client, db, fake_dte_engine, monkeypatch):
    """Una excepción no controlada (500) también debe quedar en el access-log."""
    _setup(db)

    class _BoomSII:
        def __init__(self, *a, **k):
            self.session = _FakeSession()

        def send_dte(self, *a):
            raise RuntimeError("SII caído")

    monkeypatch.setattr(sii_upload, "SIIClient", _BoomSII)
    r = client.post("/dte/issue", json=_payload(send=True), headers=headers())
    assert r.status_code == 500

    from app.db.models import RequestLog

    row = (
        db.query(RequestLog)
        .filter(RequestLog.path == "/dte/issue")
        .order_by(RequestLog.id.desc())
        .first()
    )
    assert row is not None
    assert row.status_code == 500 and row.outcome == "error"


def test_issue_rejects_foreign_issuer_rut_without_burning_folio(client, db, fake_dte_engine):
    """El RUT emisor debe ser el del cliente; un mismatch es 400 y NO consume folio."""
    _setup(db)
    bad_issuer = dict(_ISSUER, rut="11111111-1")
    r = client.post("/dte/issue", json=_payload(send=False, issuer=bad_issuer), headers=headers())
    assert r.status_code == 400, r.text
    assert "no corresponde al cliente" in r.json()["error"]["message"]

    # El folio 1 sigue disponible para la emisión legítima.
    r = client.post("/dte/issue", json=_payload(send=False), headers=headers())
    assert r.status_code == 200 and r.json()["folio"] == 1


# --------------------------------------------------------------------------- #
#  Guía de despacho (52) — Res. Ex. N°154
# --------------------------------------------------------------------------- #
_TRANSPORT = {
    "plate": "ABCD12",
    "trailer_plate": "WXYZ99",
    "carrier_rut": "76158145-7",
    "driver": {"rut": "77073851-2", "name": "Juan Perez Soto"},
    "dest_address": "Calle 2",
    "dest_commune": "Santiago",
    "dest_city": "Santiago",
    "departure_date": "2026-11-03",
    "departure_time": "08:30:00",
    "arrival_date": "2026-11-03",
}


def _guide_payload(**over):
    base = _payload(
        type=52,
        issue_date="2026-11-03",
        dispatch_type=2,
        transfer_type=1,
        transport=_TRANSPORT,
        items=[{"name": "ITEM 1", "quantity": 290, "unit_price": 6055}],
    )
    base.update(over)
    return base


def test_issue_dispatch_note(client, db, fake_dte_engine):
    _setup(db, doc_type=52)
    r = client.post("/dte/issue", json=_guide_payload(send=False), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["type"] == 52
    assert r.json()["folio"] == 1


def test_dispatch_note_without_res154_transport_is_400_and_keeps_folio(client, db, fake_dte_engine):
    """Desde el 2026-11-01 falta transporte ⇒ 400, y el folio no se quema."""
    _setup(db, doc_type=52)
    r = client.post(
        "/dte/issue", json=_guide_payload(send=False, transport=None), headers=headers()
    )
    assert r.status_code == 400, r.text
    assert "Res. Ex. N" in r.json()["error"]["message"]

    r = client.post("/dte/issue", json=_guide_payload(send=False), headers=headers())
    assert r.status_code == 200 and r.json()["folio"] == 1


def test_dispatch_note_without_transfer_type_is_400(client, db, fake_dte_engine):
    _setup(db, doc_type=52)
    r = client.post(
        "/dte/issue", json=_guide_payload(send=False, transfer_type=None), headers=headers()
    )
    assert r.status_code == 400, r.text
    assert "tipo de traslado" in r.json()["error"]["message"]


def test_internal_transfer_requires_receiver_equal_to_issuer(client, db, fake_dte_engine):
    """Caso 5038173-1: el receptor de un traslado interno es el propio emisor."""
    _setup(db, doc_type=52)
    payload = _guide_payload(send=False, transfer_type=5)
    r = client.post("/dte/issue", json=payload, headers=headers())
    assert r.status_code == 400, r.text
    assert "traslado interno" in r.json()["error"]["message"]

    payload["receiver"] = dict(_RECEIVER, rut=_ISSUER["rut"])
    r = client.post("/dte/issue", json=payload, headers=headers())
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  Liquidación factura (43)
# --------------------------------------------------------------------------- #
def _settlement_payload(**over):
    base = {
        "issue_date": "2026-08-31",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "lines": [
            {"liquidated_type": "33", "name": "NETO FACTURAS", "amount": 180269, "quantity": 4},
            {
                "liquidated_type": "61",
                "name": "NETO NOTA DE CREDITO 328",
                "amount": -20536,
                "quantity": 1,
            },
        ],
        "commissions": [{"description": "NETO COMISION FIJA", "net_amount": 866}],
        "validate_xsd": False,
        "send": False,
    }
    base.update(over)
    return base


@pytest.fixture
def fake_settlement_engine(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dte_service,
        "build_settlement",
        lambda s, caf, ts: captured.setdefault("settlement", s),
    )
    monkeypatch.setattr(dte_service, "sign_document", lambda doc, cert: "SIGNED")
    monkeypatch.setattr(dte_service, "build_envelope", lambda *a: "ENV")
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")
    return captured


def test_issue_settlement(client, db, fake_settlement_engine):
    _setup(db, doc_type=43)
    r = client.post("/dte/issue-settlement", json=_settlement_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["type"] == 43
    assert r.json()["folio"] == 1


def test_settlement_keeps_negative_lines_and_subtracts_commission(
    client, db, fake_settlement_engine
):
    _setup(db, doc_type=43)
    r = client.post("/dte/issue-settlement", json=_settlement_payload(), headers=headers())
    assert r.status_code == 200, r.text

    settlement = fake_settlement_engine["settlement"]
    assert settlement.lines[1].amount == -20536  # la NC entra restando
    assert settlement.net_amount == 180269 - 20536
    assert settlement.commission_net == 866
    # El total baja por la comisión y su IVA.
    assert settlement.total_amount == (
        settlement.net_amount + settlement.vat - 866 - settlement.commission_vat
    )


def test_settlement_without_lines_is_422(client, db, fake_settlement_engine):
    _setup(db, doc_type=43)
    r = client.post("/dte/issue-settlement", json=_settlement_payload(lines=[]), headers=headers())
    assert r.status_code == 422, r.text


def test_settlement_rejects_a_too_long_liquidated_type(client, db, fake_settlement_engine):
    _setup(db, doc_type=43)
    payload = _settlement_payload(lines=[{"liquidated_type": "3333", "name": "A", "amount": 1000}])
    r = client.post("/dte/issue-settlement", json=payload, headers=headers())
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
#  Exportación (110 / 111 / 112)
# --------------------------------------------------------------------------- #
def _export_payload(**over):
    base = {
        "type": 110,
        "issue_date": "2026-08-31",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "currency": "LIBRA EST",
        "items": [
            {
                "name": "CHATARRA DE ALUMINIO",
                "quantity": "872",
                "unit_price": "177",
                "unit": "LT",
            }
        ],
        "customs": {
            "sale_clause": 1,
            "clause_total": "4631.13",
            "freight": "3574.57",
            "insurance": "2759.05",
            "receiver_country": 224,
        },
        "global_charges": [
            {"value": 3574.57, "kind": "R", "value_type": "$", "reason": "FLETE"},
            {"value": 2759.05, "kind": "R", "value_type": "$", "reason": "SEGURO"},
        ],
        "validate_xsd": False,
        "send": False,
    }
    base.update(over)
    return base


@pytest.fixture
def fake_export_engine(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dte_service, "build_export", lambda d, caf, ts: captured.setdefault("document", d)
    )
    monkeypatch.setattr(dte_service, "sign_document", lambda doc, cert: "SIGNED")
    monkeypatch.setattr(dte_service, "build_envelope", lambda *a: "ENV")
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")
    return captured


def test_issue_export_invoice(client, db, fake_export_engine):
    _setup(db, doc_type=110)
    r = client.post("/dte/issue-export", json=_export_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["type"] == 110


def test_export_amounts_keep_decimals_through_the_api(client, db, fake_export_engine):
    """Las cifras en moneda extranjera no deben pasar por float."""
    from decimal import Decimal

    _setup(db, doc_type=110)
    r = client.post("/dte/issue-export", json=_export_payload(), headers=headers())
    assert r.status_code == 200, r.text

    document = fake_export_engine["document"]
    assert document.currency == "LIBRA EST"
    assert document.base_amount == Decimal("154344")
    assert document.total_amount == Decimal("160677.62")
    assert document.customs.clause_total == Decimal("4631.13")


def test_export_credit_note_needs_a_reference(client, db, fake_export_engine):
    _setup(db, doc_type=112)
    r = client.post(
        "/dte/issue-export", json=_export_payload(type=112, references=[]), headers=headers()
    )
    assert r.status_code == 400, r.text
    assert "Referencia" in r.json()["error"]["message"]


def test_export_rejects_a_non_export_type(client, db, fake_export_engine):
    _setup(db, doc_type=110)
    r = client.post("/dte/issue-export", json=_export_payload(type=33), headers=headers())
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
#  Datos que el SII rechazaría (caracteres y largos)
# --------------------------------------------------------------------------- #
def test_typographic_characters_are_rejected_before_sending(client, db, fake_dte_engine):
    """El apóstrofo de Word no existe en ISO-8859-1: se ataja acá, no en el SII."""
    _setup(db)
    issuer = dict(_ISSUER, business_name="COMERCIAL O’HIGGINS LTDA")
    r = client.post("/dte/issue", json=_payload(send=False, issuer=issuer), headers=headers())
    assert r.status_code == 422, r.text
    body = r.json()["error"]
    assert body["type"] == "DocumentDataError"
    assert any("Emisor.RznSoc" in d and "U+2019" in d for d in body["details"])


def test_over_long_field_is_rejected_with_the_limit(client, db, fake_dte_engine):
    _setup(db)
    receiver = dict(_RECEIVER, activity="G" * 60)  # GiroRecep admite 40
    r = client.post("/dte/issue", json=_payload(send=False, receiver=receiver), headers=headers())
    assert r.status_code == 422, r.text
    details = r.json()["error"]["details"]
    assert any("Receptor.GiroRecep" in d and "máximo de 40" in d for d in details)


def test_every_problem_is_reported_in_one_response(client, db, fake_dte_engine):
    """Para corregir todo de una vez en vez de ir descubriéndolos por reintento."""
    _setup(db)
    payload = _payload(
        send=False,
        issuer=dict(_ISSUER, business_name="X" * 120),
        receiver=dict(_RECEIVER, activity="G" * 60),
        items=[{"name": "Producto — especial", "quantity": 1, "unit_price": 1000}],
    )
    r = client.post("/dte/issue", json=payload, headers=headers())
    assert r.status_code == 422, r.text
    details = r.json()["error"]["details"]
    assert len(details) >= 3
    assert any("Emisor.RznSoc" in d for d in details)
    assert any("Receptor.GiroRecep" in d for d in details)
    assert any("Detalle[1].NmbItem" in d for d in details)


def test_bad_data_burns_no_folio(client, db, fake_dte_engine):
    _setup(db)
    issuer = dict(_ISSUER, business_name="X" * 200)
    r = client.post("/dte/issue", json=_payload(send=False, issuer=issuer), headers=headers())
    assert r.status_code == 422

    r = client.post("/dte/issue", json=_payload(send=False), headers=headers())
    assert r.status_code == 200 and r.json()["folio"] == 1


def test_spanish_accents_are_accepted(client, db, fake_dte_engine):
    """La ñ y las tildes sí existen en ISO-8859-1: no deben molestar."""
    _setup(db)
    issuer = dict(_ISSUER, business_name="MUÑOZ Y PEÑALOLÉN LTDA", commune="Ñuñoa")
    r = client.post("/dte/issue", json=_payload(send=False, issuer=issuer), headers=headers())
    assert r.status_code == 200, r.text


def test_export_declares_the_foreign_receiver(client, db, fake_export_engine):
    """Un servicio a un extranjero declara su nacionalidad (código de país)."""
    _setup(db, doc_type=110)
    payload = _export_payload(foreign_id="DE-99887766", receiver_nationality=563)

    r = client.post("/dte/issue-export", json=payload, headers=headers())
    assert r.status_code == 200, r.text

    document = fake_export_engine["document"]
    assert document.foreign_id == "DE-99887766"
    assert document.receiver_nationality == 563
