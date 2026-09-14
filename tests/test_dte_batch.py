"""Emisión en lote: N documentos dentro de UN solo EnvioDTE.

Es lo que exige el set de certificación del SII: cada set se entrega como un
único envío. El caso duro es el SET BASICO 5038170, donde las notas referencian
facturas emitidas en el mismo lote, cuyo folio recién se conoce al asignarlo.
"""

import base64

import pytest
from dte_chile.sii_client import SubmissionResult

from app.security.service_codes import SERVICE_DTE
from app.services import customer_service, dte_service, sii_upload
from tests.conftest import fake_caf_xml, grant, headers, make_customer

_ISSUER = {
    "rut": "76158145-7",
    "business_name": "DEMO SPA",
    "activity": "Venta al por menor de maquinaria",
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


class _FakeSession:
    def close(self):
        pass


def _invoice(items, **over):
    doc = {
        "type": 33,
        "issue_date": "2026-08-28",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "items": items,
    }
    doc.update(over)
    return doc


def _note(doc_type, batch_index, code, reason, items):
    return _invoice(
        items,
        type=doc_type,
        references=[{"batch_index": batch_index, "code": code, "reason": reason}],
    )


def _item(name, quantity, unit_price, **over):
    return {"name": name, "quantity": quantity, "unit_price": unit_price, **over}


def set_basico():
    """Los 8 casos del SET BASICO 5038170, en el orden del set."""
    return [
        # 1
        _invoice([_item("Cajon AFECTO", 161, 3071), _item("Relleno AFECTO", 68, 5105)]),
        # 2 — descuentos por línea
        _invoice(
            [
                _item("Panuelo AFECTO", 673, 5230, discount_pct=9),
                _item("ITEM 2 AFECTO", 615, 4283, discount_pct=20),
            ]
        ),
        # 3 — con ítem exento
        _invoice(
            [
                _item("Pintura B&W AFECTO", 53, 6134),
                _item("ITEM 2 AFECTO", 222, 3807),
                _item("ITEM 3 SERVICIO EXENTO", 1, 35196, exempt=True),
            ]
        ),
        # 4 — descuento global sobre los afectos
        _invoice(
            [
                _item("ITEM 1 AFECTO", 360, 5226),
                _item("ITEM 2 AFECTO", 152, 6258),
                _item("ITEM 3 SERVICIO EXENTO", 2, 6822, exempt=True),
            ],
            global_discounts=[
                {"value": 20, "kind": "D", "reason": "DESCUENTO GLOBAL ITEMES AFECTOS"}
            ],
        ),
        # 5 — NC sobre el caso 1: corrige giro del receptor
        _note(61, 1, 2, "CORRIGE GIRO DEL RECEPTOR", [_item("CORRIGE GIRO", 1, 0)]),
        # 6 — NC sobre el caso 2: devolución de mercaderías
        _note(
            61,
            2,
            3,
            "DEVOLUCION DE MERCADERIAS",
            [
                _item("Panuelo AFECTO", 247, 5230, discount_pct=9),
                _item("ITEM 2 AFECTO", 417, 4283, discount_pct=20),
            ],
        ),
        # 7 — NC sobre el caso 3: anula la factura
        _note(
            61,
            3,
            1,
            "ANULA FACTURA",
            [
                _item("Pintura B&W AFECTO", 53, 6134),
                _item("ITEM 2 AFECTO", 222, 3807),
                _item("ITEM 3 SERVICIO EXENTO", 1, 35196, exempt=True),
            ],
        ),
        # 8 — ND sobre el caso 5: anula la nota de crédito
        _note(56, 5, 1, "ANULA NOTA DE CREDITO ELECTRONICA", [_item("ANULA NC", 1, 0)]),
    ]


def _payload(documents=None, **over):
    body = {
        "documents": documents if documents is not None else set_basico(),
        "send": False,
        # El motor va stubbeado: el XML que devuelve no es un sobre real.
        "validate_xsd": False,
    }
    body.update(over)
    return body


@pytest.fixture
def captured(monkeypatch):
    """Captura los DTE de dominio y la carátula que recibe el motor."""
    seen: dict = {"dtes": [], "cover": None, "envelopes": 0}

    def _build_document(dte, caf, ts):
        seen["dtes"].append(dte)
        return f"DOC{dte.folio}"

    def _build_envelope(signed, cover, cert, ts):
        seen["cover"] = cover
        seen["signed"] = signed
        seen["envelopes"] += 1
        return "ENV"

    monkeypatch.setattr(dte_service, "build_document", _build_document)
    monkeypatch.setattr(dte_service, "sign_document", lambda doc, cert: doc)
    monkeypatch.setattr(dte_service, "build_envelope", _build_envelope)
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")
    return seen


def _setup(db, types=(33, 56, 61)):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    for doc_type in types:
        customer_service.add_caf(
            db, customer, base64.b64encode(fake_caf_xml(doc_type, 1, 50)).decode()
        )
    return customer


# --------------------------------------------------------------------------- #
#  El set completo en un envío
# --------------------------------------------------------------------------- #
def test_whole_set_goes_in_a_single_envelope(client, db, captured):
    _setup(db)
    r = client.post("/dte/issue-batch", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text

    body = r.json()
    assert len(body["documents"]) == 8
    assert captured["envelopes"] == 1  # UN sobre, no ocho
    assert len(captured["signed"]) == 8
    assert base64.b64decode(body["xml_base64"]) == b"<EnvioDTE/>"


def test_folios_are_sequential_per_document_type(client, db, captured):
    _setup(db)
    r = client.post("/dte/issue-batch", json=_payload(), headers=headers())

    documents = r.json()["documents"]
    assert [d["index"] for d in documents] == list(range(1, 9))
    # Cuatro facturas, tres NC y una ND, cada tipo con su propia numeración.
    assert [(d["type"], d["folio"]) for d in documents] == [
        (33, 1),
        (33, 2),
        (33, 3),
        (33, 4),
        (61, 1),
        (61, 2),
        (61, 3),
        (56, 1),
    ]


def test_cover_subtotals_are_grouped_by_type(client, db, captured):
    _setup(db)
    client.post("/dte/issue-batch", json=_payload(), headers=headers())
    assert captured["cover"].subtotals == [(33, 4), (56, 1), (61, 3)]


def test_intra_batch_references_resolve_to_assigned_folios(client, db, captured):
    """La NC del caso 5 debe apuntar al folio real que le tocó a la factura 1."""
    _setup(db)
    client.post("/dte/issue-batch", json=_payload(), headers=headers())

    dtes = captured["dtes"]
    credit_note = dtes[4]  # caso 5
    reference = credit_note.references[0]
    assert reference.doc_type == 33
    assert reference.folio == str(dtes[0].folio)
    assert reference.date == dtes[0].issue_date
    assert reference.reason == "CORRIGE GIRO DEL RECEPTOR"

    # Caso 8: la ND referencia a la NC del caso 5, no a una factura.
    debit_note = dtes[7]
    assert debit_note.references[0].doc_type == 61
    assert debit_note.references[0].folio == str(dtes[4].folio)


def test_set_totals_match_the_certification_set(client, db, captured):
    """Los montos del set deben salir tal cual, con descuentos aplicados."""
    _setup(db)
    client.post("/dte/issue-batch", json=_payload(), headers=headers())

    totals = [(d.exempt_amount, d.net_amount, d.vat, d.total_amount) for d in captured["dtes"][:4]]
    assert totals == [
        (0, 841571, 159898, 1001469),
        (0, 5310245, 1008947, 6319192),
        (35196, 1170256, 222349, 1427801),
        (13644, 2266061, 430552, 2710257),
    ]


# --------------------------------------------------------------------------- #
#  Referencias inválidas
# --------------------------------------------------------------------------- #
def test_self_reference_is_rejected(client, db, captured):
    _setup(db)
    documents = [_note(61, 1, 1, "ANULA", [_item("A", 1, 1000)])]
    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 400, r.text
    assert "sí mismo" in r.json()["error"]["message"]


def test_reference_beyond_the_batch_is_rejected(client, db, captured):
    _setup(db)
    documents = [
        _invoice([_item("A", 1, 1000)]),
        _note(61, 9, 1, "ANULA", [_item("A", 1, 1000)]),
    ]
    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 400, r.text
    assert "posición 9" in r.json()["error"]["message"]


def test_reference_cannot_mix_batch_index_with_explicit_folio(client, db):
    _setup(db)
    documents = [
        _invoice([_item("A", 1, 1000)]),
        _invoice(
            [_item("A", 1, 1000)],
            type=61,
            references=[
                {
                    "batch_index": 1,
                    "doc_type": 33,
                    "folio": "5",
                    "date": "2026-08-28",
                    "code": 1,
                    "reason": "ANULA",
                }
            ],
        ),
    ]
    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 422, r.text


def test_explicit_reference_to_a_previous_envelope_still_works(client, db, captured):
    """No todo es intra-lote: referenciar un folio ya emitido debe seguir andando."""
    _setup(db)
    documents = [
        _invoice(
            [_item("A", 1, 1000)],
            type=61,
            references=[
                {"doc_type": 33, "folio": "417", "date": "2026-07-01", "code": 1, "reason": "ANULA"}
            ],
        )
    ]
    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 200, r.text
    assert captured["dtes"][0].references[0].folio == "417"


# --------------------------------------------------------------------------- #
#  Folios: nada se quema si el lote no sale entero
# --------------------------------------------------------------------------- #
def test_malformed_document_burns_no_folio(client, db, captured):
    """Un documento inválido invalida el lote ANTES de pedir folios."""
    _setup(db)
    documents = set_basico()
    documents.append(_invoice([_item("A", 1, 1000)], type=61, references=[]))  # nota sin referencia

    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 400, r.text
    assert "Documento 9" in r.json()["error"]["message"]

    # Ningún folio se consumió: el lote bueno arranca en 1.
    r = client.post("/dte/issue-batch", json=_payload(), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["documents"][0]["folio"] == 1


def test_foreign_issuer_names_the_offending_document(client, db, captured):
    _setup(db)
    documents = set_basico()
    documents[2] = _invoice([_item("A", 1, 1000)], issuer=dict(_ISSUER, rut="11111111-1"))

    r = client.post("/dte/issue-batch", json=_payload(documents), headers=headers())
    assert r.status_code == 400, r.text
    assert "Documento 3" in r.json()["error"]["message"]


def test_send_failure_marks_every_folio_of_the_batch_as_failed(client, db, captured, monkeypatch):
    _setup(db)

    class _BoomSII:
        def __init__(self, *a, **k):
            self.session = _FakeSession()

        def send_dte(self, *a):
            raise RuntimeError("SII caído")

    monkeypatch.setattr(sii_upload, "SIIClient", _BoomSII)
    r = client.post("/dte/issue-batch", json=_payload(send=True), headers=headers())
    assert r.status_code == 500

    from app.db.models import FolioAssignment

    rows = db.query(FolioAssignment).all()
    assert len(rows) == 8
    assert {row.status for row in rows} == {"failed"}


def test_successful_batch_marks_every_folio_as_issued(client, db, captured, monkeypatch):
    _setup(db)

    class _OkSII:
        def __init__(self, *a, **k):
            self.session = _FakeSession()

        def send_dte(self, *a):
            return SubmissionResult(track_id="TRACK-1", status="OK", detail="")

    monkeypatch.setattr(sii_upload, "SIIClient", _OkSII)
    r = client.post("/dte/issue-batch", json=_payload(send=True), headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["track_id"] == "TRACK-1"

    from app.db.models import FolioAssignment

    rows = db.query(FolioAssignment).all()
    assert len(rows) == 8
    assert {row.status for row in rows} == {"issued"}


def test_missing_caf_for_one_type_fails_the_whole_batch(client, db, captured):
    """Sin CAF de nota de crédito el lote entero cae, y lo ya asignado queda trazado."""
    _setup(db, types=(33,))
    r = client.post("/dte/issue-batch", json=_payload(), headers=headers())
    assert r.status_code >= 400

    from app.db.models import FolioAssignment

    rows = db.query(FolioAssignment).all()
    assert {row.status for row in rows} == {"failed"}


def test_empty_batch_is_rejected(client, db):
    _setup(db)
    r = client.post("/dte/issue-batch", json=_payload([]), headers=headers())
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
#  Lote de exportación
# --------------------------------------------------------------------------- #
@pytest.fixture
def export_captured(monkeypatch):
    """Como `captured`, pero para la raíz <Exportaciones>."""
    seen: dict = {"documents": [], "cover": None, "envelopes": 0}

    def _build_export(document, caf, ts):
        seen["documents"].append(document)
        return f"EXP{document.folio}"

    def _build_envelope(signed, cover, cert, ts):
        seen["cover"] = cover
        seen["envelopes"] += 1
        return "ENV"

    monkeypatch.setattr(dte_service, "build_export", _build_export)
    monkeypatch.setattr(dte_service, "sign_document", lambda doc, cert: doc)
    monkeypatch.setattr(dte_service, "build_envelope", _build_envelope)
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")
    return seen


def _export(doc_type, **over):
    doc = {
        "type": doc_type,
        "issue_date": "2026-08-31",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "currency": "LIBRA EST",
        "items": [{"name": "CHATARRA DE ALUMINIO", "quantity": "872", "unit_price": "177"}],
    }
    doc.update(over)
    return doc


def _export_set():
    """SET EXPORTACION (1) 5038176: factura, NC que devuelve, ND que anula la NC."""
    return {
        "documents": [
            _export(110),
            _export(
                112,
                items=[{"name": "CHATARRA DE ALUMINIO", "quantity": "291", "unit_price": "177"}],
                references=[{"batch_index": 1, "code": 3, "reason": "DEVOLUCION DE MERCADERIA"}],
            ),
            _export(
                111,
                items=[{"name": "ANULA NOTA DE CREDITO", "amount": "0"}],
                references=[{"batch_index": 2, "code": 1, "reason": "ANULA NOTA DE CREDITO"}],
            ),
        ],
        "send": False,
        "validate_xsd": False,
    }


def test_export_set_goes_in_a_single_envelope(client, db, export_captured):
    _setup(db, types=(110, 111, 112))
    r = client.post("/dte/issue-export-batch", json=_export_set(), headers=headers())
    assert r.status_code == 200, r.text

    assert len(r.json()["documents"]) == 3
    assert export_captured["envelopes"] == 1  # el set entero es UN envío


def test_export_notes_reference_the_folio_assigned_in_the_batch(client, db, export_captured):
    """El folio recién se conoce al asignarlo, por eso se referencia por posición."""
    _setup(db, types=(110, 111, 112))
    r = client.post("/dte/issue-export-batch", json=_export_set(), headers=headers())
    assert r.status_code == 200, r.text

    factura, nota_credito, nota_debito = export_captured["documents"]
    assert nota_credito.references[0].folio == str(factura.folio)
    assert nota_credito.references[0].doc_type == 110
    assert nota_debito.references[0].folio == str(nota_credito.folio)
    assert nota_debito.references[0].doc_type == 112


def test_a_malformed_export_burns_no_folio_from_the_others(client, db, export_captured):
    customer = _setup(db, types=(110, 111, 112))
    payload = _export_set()
    payload["documents"][2]["references"] = []  # la ND de exportación exige referencia

    assert (
        client.post("/dte/issue-export-batch", json=payload, headers=headers()).status_code == 400
    )
    assert customer_service.folio_pointers(db, customer.id).get(110, 0) == 0


# --------------------------------------------------------------------------- #
#  Lote de liquidaciones
# --------------------------------------------------------------------------- #
@pytest.fixture
def settlement_captured(monkeypatch):
    seen: dict = {"settlements": [], "envelopes": 0}

    def _build_settlement(settlement, caf, ts):
        seen["settlements"].append(settlement)
        return f"LIQ{settlement.folio}"

    def _build_envelope(signed, cover, cert, ts):
        seen["envelopes"] += 1
        return "ENV"

    monkeypatch.setattr(dte_service, "build_settlement", _build_settlement)
    monkeypatch.setattr(dte_service, "sign_document", lambda doc, cert: doc)
    monkeypatch.setattr(dte_service, "build_envelope", _build_envelope)
    monkeypatch.setattr(dte_service, "serialize", lambda env: b"<EnvioDTE/>")
    return seen


def _settlement():
    return {
        "issue_date": "2026-09-01",
        "issuer": _ISSUER,
        "receiver": _RECEIVER,
        "lines": [
            {"liquidated_type": "33", "name": "NETO FACTURAS", "amount": 180269, "quantity": 4}
        ],
    }


def test_the_settlement_set_goes_in_a_single_envelope(client, db, settlement_captured):
    """El SET BASICO LIQUIDACIONES se entrega como un envío, no como cuatro."""
    _setup(db, types=(43,))
    payload = {"documents": [_settlement() for _ in range(4)], "send": False, "validate_xsd": False}

    r = client.post("/dte/issue-settlement-batch", json=payload, headers=headers())
    assert r.status_code == 200, r.text

    assert len(r.json()["documents"]) == 4
    assert settlement_captured["envelopes"] == 1


def test_every_settlement_of_the_batch_gets_its_own_folio(client, db, settlement_captured):
    _setup(db, types=(43,))
    payload = {"documents": [_settlement() for _ in range(4)], "send": False, "validate_xsd": False}

    r = client.post("/dte/issue-settlement-batch", json=payload, headers=headers())
    folios = [d["folio"] for d in r.json()["documents"]]
    assert folios == [1, 2, 3, 4]
    assert all(d["type"] == 43 for d in r.json()["documents"])
