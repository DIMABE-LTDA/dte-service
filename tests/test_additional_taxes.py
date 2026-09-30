"""ILA y demás impuestos adicionales: de la petición al DTE del motor, y al libro.

El XML en sí (``<ImptoReten>``, ``<CodImpAdic>``, ``<OtrosImp>`` y su validación
contra el XSD) lo prueba el motor. Aquí: que el servicio los pase tal cual, que
el total los sume y que una combinación que el SII no acepta se detenga antes
de gastar un folio.
"""

import pytest
from pydantic import ValidationError

from app.schemas.book import BookLineIn
from app.schemas.dte import DteIssueRequest
from app.services import book_service, dte_service
from tests.conftest import headers
from tests.test_dte import _payload, _setup, fake_dte_engine  # noqa: F401

_ILA_ITEMS = [
    {"name": "Vino tinto", "quantity": 12, "unit_price": 3000, "additional_tax_code": 25},
    {"name": "Bebida cola", "quantity": 10, "unit_price": 1000, "additional_tax_code": 271},
    {"name": "Servilletas", "quantity": 5, "unit_price": 500},
]
_ILA_TAXES = [{"code": 25, "rate": 20.5}, {"code": 271, "rate": 18}]


def test_request_maps_to_domain_with_ila_added_to_total():
    req = DteIssueRequest(**_payload(items=_ILA_ITEMS, additional_taxes=_ILA_TAXES))
    dte = dte_service._domain_dte(req)
    assert [i.additional_tax_code for i in dte.items] == [25, 271, None]
    assert dte.additional_tax_amount == 7380 + 1800
    assert dte.total_amount == 48500 + 9215 + 9180


def test_issue_with_ila_uses_a_folio(client, db, fake_dte_engine):  # noqa: F811
    _setup(db)
    r = client.post(
        "/dte/issue",
        json=_payload(items=_ILA_ITEMS, additional_taxes=_ILA_TAXES, send=False),
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["folio"] == 1


def test_undeclared_ila_is_rejected_before_taking_a_folio(client, db, fake_dte_engine):  # noqa: F811
    _setup(db)
    r = client.post(
        "/dte/issue",
        json=_payload(items=_ILA_ITEMS, additional_taxes=[{"code": 25, "rate": 20.5}], send=False),
        headers=headers(),
    )
    assert r.status_code == 400, r.text
    assert "no está declarado" in r.text
    # El folio sigue libre: el siguiente documento válido se lleva el 1.
    ok = client.post("/dte/issue", json=_payload(send=False), headers=headers())
    assert ok.json()["folio"] == 1


def test_book_line_carries_other_taxes():
    line = book_service._book_line(
        BookLineIn(
            doc_type=33,
            folio=10,
            date="2026-09-30",
            rut="17099910-K",
            business_name="Cliente",
            net_amount=48500,
            vat_amount=9215,
            total_amount=66895,
            other_taxes=[{"code": 25, "amount": 7380, "rate": 20.5}],
        )
    )
    assert [(t.code, t.amount, t.rate) for t in line.other_taxes] == [(25, 7380, 20.5)]


def test_book_line_in_foreign_currency_rejects_ila():
    with pytest.raises(ValidationError, match="ILA"):
        BookLineIn(
            doc_type=110,
            folio=1,
            date="2026-09-30",
            rut="55555555-5",
            business_name="Extranjero",
            exempt_amount=10,
            total_amount=10,
            currency="DOLAR USA",
            exchange_rate=950,
            other_taxes=[{"code": 25, "amount": 1}],
        )
