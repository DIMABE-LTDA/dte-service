"""Tests del endpoint de BTE emitidas y de la cuota de consultas al portal del SII."""

import datetime as dt

from dte_chile.bhe import BteDocument

from app.security.service_codes import SERVICE_BHE
from app.services import bhe_service, sii_credential_service
from tests.conftest import grant, headers, make_customer

_PAYLOAD = {"period": "202605"}


class _FakeBte:
    def __init__(self, rut, password):
        assert rut and password  # se resuelven del tenant + clave guardada

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch_issued_bte(self, year, month):
        period = f"{year:04d}-{month:02d}"
        return [
            BteDocument(
                provider_rut="12345678-5",
                provider_name="Juan Prestador",
                folio=130,
                issue_date=dt.date(year, month, 6),
                gross_amount=94_395,
                retention_amount=14_395,
                net_amount=80_000,
                status="vigente",
                period=period,
            ),
            BteDocument(
                provider_rut="7654321-K",
                provider_name="Maria Prestadora",
                folio=131,
                issue_date=dt.date(year, month, 7),
                gross_amount=28_319,
                retention_amount=4_319,
                net_amount=24_000,
                status="anulada",
                period=period,
            ),
        ]

    def fetch_received(self, year, month):
        return []


def _ready_customer(db, monkeypatch, **kwargs):
    c = make_customer(db, **kwargs)
    grant(db, c, SERVICE_BHE, apikey="secret")
    sii_credential_service.store_sii_password(db, c, "clave-sii")
    monkeypatch.setattr(bhe_service, "BheClient", _FakeBte)
    return c


def test_bte_issued_requires_service(client, db):
    make_customer(db)  # sin grant: BTE usa el mismo servicio que las BHE
    assert client.post("/bte/issued", json=_PAYLOAD, headers=headers()).status_code == 401


def test_bte_issued_without_sii_key_returns_409(client, db):
    c = make_customer(db)
    grant(db, c, SERVICE_BHE, apikey="secret")
    assert client.post("/bte/issued", json=_PAYLOAD, headers=headers()).status_code == 409


def test_bte_issued_rejects_bad_period(client, db, monkeypatch):
    _ready_customer(db, monkeypatch)
    r = client.post("/bte/issued", json={"period": "2026-05"}, headers=headers())
    assert r.status_code == 422


def test_bte_issued_ok_includes_cancelled(client, db, monkeypatch):
    _ready_customer(db, monkeypatch)
    r = client.post("/bte/issued", json=_PAYLOAD, headers=headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issuer_rut"] == "76158145-7"  # del cliente, no del body
    assert body["count"] == 2
    vig, anul = body["documents"]
    assert vig["provider_rut"] == "12345678-5"
    assert (vig["gross_amount"], vig["retention_amount"], vig["net_amount"]) == (
        94_395,
        14_395,
        80_000,
    )
    assert vig["issue_date"] == "2026-05-06"
    assert anul["status"] == "anulada"


def test_sii_portal_quota_per_customer(client, db, monkeypatch):
    """Tope de consultas al portal por cliente, compartido entre BHE y BTE."""
    from app.core.config import get_settings

    _ready_customer(db, monkeypatch)
    limit = get_settings().sii_portal_queries_per_minute
    paths = ["/bhe/received", "/bte/issued"]
    codes = [
        client.post(paths[i % 2], json=_PAYLOAD, headers=headers()).status_code
        for i in range(limit)
    ]
    assert codes == [200] * limit
    r = client.post("/bte/issued", json=_PAYLOAD, headers=headers())
    assert r.status_code == 429
    assert "portal del SII" in r.json()["detail"]


def test_sii_portal_quota_is_per_customer(client, db, monkeypatch):
    """Que un cliente agote su cuota no frena a otro."""
    from app.core.config import get_settings

    _ready_customer(db, monkeypatch, rut="76158145-7", key="cust-a")
    b = make_customer(db, rut="11111111-1", key="cust-b")
    grant(db, b, SERVICE_BHE, apikey="key-b")
    sii_credential_service.store_sii_password(db, b, "clave-b")
    a_headers = {"customerCode": "cust-a", "apiKey": "secret"}
    for _ in range(get_settings().sii_portal_queries_per_minute):
        client.post("/bte/issued", json=_PAYLOAD, headers=a_headers)
    assert client.post("/bte/issued", json=_PAYLOAD, headers=a_headers).status_code == 429
    b_headers = {"customerCode": "cust-b", "apiKey": "key-b"}
    assert client.post("/bte/issued", json=_PAYLOAD, headers=b_headers).status_code == 200


_ADMIN = {"X-Admin-Key": "test-admin-key-0123456789"}


def test_operator_bte_uses_stored_key(client, db, monkeypatch):
    """El operador (Odoo con X-Admin-Key) consulta las BTE de un cliente con su clave."""
    c = _ready_customer(db, monkeypatch)
    r = client.post(f"/admin/customers/{c.id}/bte", json=_PAYLOAD, headers=_ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2


def test_operator_bte_without_admin_key_is_rejected(client, db, monkeypatch):
    c = _ready_customer(db, monkeypatch)
    assert client.post(f"/admin/customers/{c.id}/bte", json=_PAYLOAD).status_code == 401
