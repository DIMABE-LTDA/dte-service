import base64
import pathlib

import pytest

from app.security.service_codes import SERVICE_EXCHANGE
from app.services import exchange_service
from tests.conftest import grant, headers, make_customer


@pytest.fixture
def fake_exchange_engine(monkeypatch):
    monkeypatch.setattr(exchange_service, "parse_envelope", lambda data: "ENV")
    monkeypatch.setattr(exchange_service, "build_receipt_acknowledgment", lambda env, cert, ts: "X")
    monkeypatch.setattr(exchange_service, "build_result_response", lambda *a, **k: "X")
    monkeypatch.setattr(exchange_service, "build_receipts_envelope", lambda *a, **k: "X")
    monkeypatch.setattr(exchange_service, "serialize", lambda x: b"<RespuestaDTE/>")


def _setup(db):
    customer = make_customer(db)
    grant(db, customer, SERVICE_EXCHANGE)


def test_acknowledgment(client, db, fake_exchange_engine):
    _setup(db)
    payload = {"envelope_base64": base64.b64encode(b"<EnvioDTE/>").decode()}
    r = client.post("/exchange/ack", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["xml_base64"]) == b"<RespuestaDTE/>"


def test_result_rejection(client, db, fake_exchange_engine):
    _setup(db)
    payload = {
        "envelope_base64": base64.b64encode(b"<EnvioDTE/>").decode(),
        "accept": False,
        "rejection_label": "monto incorrecto",
    }
    r = client.post("/exchange/result", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["xml_base64"]) == b"<RespuestaDTE/>"


def test_receipts(client, db, fake_exchange_engine):
    _setup(db)
    payload = {"envelope_base64": base64.b64encode(b"<EnvioDTE/>").decode(), "location": "Bodega"}
    r = client.post("/exchange/receipts", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["xml_base64"]) == b"<RespuestaDTE/>"


def test_inspect_dice_que_trae_el_sobre(client, db):
    """Leer el sobre recibido: de quién es, qué documento y por cuánto.

    Sin esto, quien recibe un DTE de su proveedor no puede ni registrarlo:
    tendría que aprender a parsear un EnvioDTE dentro del ERP.
    """
    # El sobre del set va dirigido a este RUT: el cliente tiene que ser ese,
    # o ninguno de los documentos sería suyo.
    customer = make_customer(db, rut="77262159-0")
    grant(db, customer, SERVICE_EXCHANGE)
    sobre = pathlib.Path("tests/fixtures/sii/set_intercambio_77262159-0.xml").read_bytes()
    payload = {"envelope_base64": base64.b64encode(sobre).decode()}

    r = client.post("/exchange/inspect", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["receiver_rut"] == "77262159-0"
    assert [d["folio"] for d in datos["documents"]] == [52298, 52299]
    primero = datos["documents"][0]
    assert primero["doc_type"] == 33
    assert primero["total_amount"] > 0
    # El set del SII trae a propósito un documento dirigido a otro receptor
    # (69507000-4): ese no se recibe, se rechaza y no lleva recibo de
    # mercaderías. Distinguirlo es justo lo que hace falta al registrarlo.
    assert [d["addressed_to_me"] for d in datos["documents"]] == [True, False]


def test_inspect_rechaza_un_sobre_ilegible(client, db):
    _setup(db)
    payload = {"envelope_base64": base64.b64encode(b"esto no es XML").decode()}
    r = client.post("/exchange/inspect", json=payload, headers=headers())
    assert r.status_code == 400, r.text
    assert "no se pudo leer" in r.json()["error"]["message"]


def test_exchange_requires_service(client, db, fake_exchange_engine):
    make_customer(db)  # sin grant de EXCHANGE
    payload = {"envelope_base64": base64.b64encode(b"<EnvioDTE/>").decode()}
    r = client.post("/exchange/ack", json=payload, headers=headers())
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
#  Registro de aceptación o reclamo ante el SII (Ley 19.983)
# --------------------------------------------------------------------------- #
class _FalsoRegistro:
    """Se queda con lo que se le pidió al SII, sin llamarlo."""

    ultimo = {}

    def __init__(self, cert, environment, timeout=30):
        self.session = type("S", (), {"close": lambda self: None})()

    def register(self, issuer_rut, doc_type, folio, action):
        from dte_chile.claim import CLAIMABLE_TYPES, ClaimResult

        if doc_type not in CLAIMABLE_TYPES:
            raise ValueError(f"El documento tipo {doc_type} no admite aceptación ni reclamo.")
        _FalsoRegistro.ultimo = {
            "issuer_rut": issuer_rut,
            "doc_type": doc_type,
            "folio": folio,
            "action": str(action),
        }
        return ClaimResult(code=0, detail="EVENTO REGISTRADO")

    def history(self, issuer_rut, doc_type, folio):
        from dte_chile.claim import ClaimEvent, ClaimResult

        _FalsoRegistro.ultimo = {"issuer_rut": issuer_rut, "doc_type": doc_type, "folio": folio}
        return ClaimResult(
            code=0,
            detail="OK",
            events=[ClaimEvent(code="ERM", label="Otorga Recibo", date="2026-09-20")],
        )


@pytest.fixture
def fake_claim(monkeypatch):
    monkeypatch.setattr(exchange_service, "ClaimClient", _FalsoRegistro)


def test_el_reclamo_va_al_sii_con_el_rut_del_proveedor(client, db, fake_claim):
    """Lo que corre el plazo de ocho días es este registro, no el correo.

    Y el documento se identifica por su emisor: con el RUT propio, el SII
    registraría el evento sobre un documento que no existe.
    """
    _setup(db)
    payload = {"issuer_rut": "88888888-8", "doc_type": 33, "folio": 52298, "action": "RCD"}

    r = client.post("/exchange/claim", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert _FalsoRegistro.ultimo == {
        "issuer_rut": "88888888-8",
        "doc_type": 33,
        "folio": 52298,
        "action": "RCD",
    }


def test_una_accion_inventada_no_se_manda(client, db, fake_claim):
    _setup(db)
    payload = {"issuer_rut": "88888888-8", "doc_type": 33, "folio": 1, "action": "XXX"}
    r = client.post("/exchange/claim", json=payload, headers=headers())
    assert r.status_code == 400, r.text
    assert "ACD" in r.json()["error"]["message"]


def test_una_boleta_no_se_reclama_en_el_sii(client, db, fake_claim):
    _setup(db)
    payload = {"issuer_rut": "88888888-8", "doc_type": 39, "folio": 1, "action": "RCD"}
    r = client.post("/exchange/claim", json=payload, headers=headers())
    assert r.status_code == 400, r.text


def test_el_historial_dice_lo_que_ya_se_registro(client, db, fake_claim):
    _setup(db)
    payload = {"issuer_rut": "88888888-8", "doc_type": 33, "folio": 52298}
    r = client.post("/exchange/claim/history", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert [e["code"] for e in r.json()["events"]] == ["ERM"]
