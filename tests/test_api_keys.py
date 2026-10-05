"""Claves API por cliente: una clave con los servicios que tiene permitidos.

Cubre que el camino nuevo (``<key_id>.<secret>``) y el viejo (una apiKey por
servicio, ``CustomerService.apikey_hash``) convivan sin cortar a nadie, los
endpoints de administración y que la auditoría registre el nombre de la clave.
"""

from __future__ import annotations

from app.security.service_codes import SERVICE_DTE, SERVICE_RCV
from app.services import rcv_service
from tests.conftest import auth_header, grant, headers, make_customer, make_user

ADMIN = {"X-Admin-Key": "test-admin-key-0123456789"}


def _operator(client, db):
    make_user(db, "op@dimabe.cl", "secret", "operator")
    return auth_header(client, "op@dimabe.cl", "secret")


def _customer_with_services(db, *, key="cust-1", codes=(SERVICE_DTE, SERVICE_RCV)):
    """Cliente con los servicios ``codes`` contratados (legacy ``grant``)."""
    c = make_customer(db, key=key)
    for code in codes:
        grant(db, c, code, f"legacy-{code[:8]}")
    return c


def _create_key(client, h, customer_id, name="Odoo producción", service_codes=(SERVICE_DTE,)):
    r = client.post(
        f"/admin/customers/{customer_id}/api-keys",
        json={"name": name, "service_codes": list(service_codes)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- Autenticación: camino nuevo -------------------------------------------


def test_new_key_authenticates_its_allowed_service(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    key = _create_key(client, h, c.id, service_codes=[SERVICE_DTE])
    assert "." in key["api_key"] and key["key_id"] in key["api_key"]

    r = client.get("/dte/folios", headers=headers(c.key, key["api_key"]))
    assert r.status_code == 200, r.text


def test_new_key_gets_403_on_service_it_does_not_have(client, db, monkeypatch):
    from tests.test_auth_rcv import _FakeRcv

    c = _customer_with_services(db)
    h = _operator(client, db)
    key = _create_key(client, h, c.id, service_codes=[SERVICE_DTE])  # sin RCV

    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)
    r = client.post(
        "/rcv/documents",
        json={"period": "202505", "operation": "COMPRA"},
        headers=headers(c.key, key["api_key"]),
    )
    assert r.status_code == 403
    assert "servicio" in r.json()["detail"]


def test_revoked_key_returns_401(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    key = _create_key(client, h, c.id, service_codes=[SERVICE_DTE])

    r_del = client.delete(f"/admin/customers/{c.id}/api-keys/{key['id']}", headers=h)
    assert r_del.status_code == 200
    r = client.get("/dte/folios", headers=headers(c.key, key["api_key"]))
    assert r.status_code == 401


def test_key_of_another_customer_returns_401(client, db):
    h = _operator(client, db)
    c1 = _customer_with_services(db, key="cust-1")
    c2 = _customer_with_services(db, key="cust-2")
    key1 = _create_key(client, h, c1.id, service_codes=[SERVICE_DTE])

    # customerCode de c2 con la apiKey de c1: no autentica.
    r = client.get("/dte/folios", headers=headers(c2.key, key1["api_key"]))
    assert r.status_code == 401


def test_bad_key_format_returns_401(client, db):
    c = _customer_with_services(db)
    r = client.get("/dte/folios", headers=headers(c.key, "noesunaclave"))
    assert r.status_code == 401


# --- Autenticación: camino viejo (deprecado) sigue vivo --------------------


def test_legacy_per_service_key_still_works(client, db):
    c = make_customer(db, key="cust-legacy")
    grant(db, c, SERVICE_DTE, "legacy-secret")
    r = client.get("/dte/folios", headers=headers("cust-legacy", "legacy-secret"))
    assert r.status_code == 200, r.text


# --- Administración ---------------------------------------------------------


def test_create_key_rejects_service_not_contracted(client, db):
    c = make_customer(db, key="cust-2")
    grant(db, c, SERVICE_DTE, "x")  # solo DTE contratado
    h = _operator(client, db)
    r = client.post(
        f"/admin/customers/{c.id}/api-keys",
        json={"name": "n", "service_codes": [SERVICE_RCV]},
        headers=h,
    )
    assert r.status_code == 400


def test_create_key_requires_admin_auth(client, db):
    c = _customer_with_services(db)
    r = client.post(
        f"/admin/customers/{c.id}/api-keys",
        json={"name": "n", "service_codes": [SERVICE_DTE]},
    )
    assert r.status_code == 401


def test_list_keys_never_exposes_secret(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    _create_key(client, h, c.id, service_codes=[SERVICE_DTE])
    rows = client.get(f"/admin/customers/{c.id}/api-keys", headers=h).json()
    assert len(rows) == 1
    assert "secret_hash" not in rows[0] and "api_key" not in rows[0]
    assert rows[0]["service_codes"] == [SERVICE_DTE]


def test_update_services_stays_within_contracted(client, db, monkeypatch):
    c = _customer_with_services(db)  # DTE + RCV contratados
    h = _operator(client, db)
    key = _create_key(client, h, c.id, service_codes=[SERVICE_DTE])

    r = client.patch(
        f"/admin/customers/{c.id}/api-keys/{key['id']}/services",
        json={"service_codes": [SERVICE_DTE, SERVICE_RCV]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["service_codes"]) == sorted([SERVICE_DTE, SERVICE_RCV])

    # Ahora la clave también autentica RCV.
    from tests.test_auth_rcv import _FakeRcv

    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)
    r2 = client.post(
        "/rcv/documents",
        json={"period": "202505", "operation": "COMPRA"},
        headers=headers(c.key, key["api_key"]),
    )
    assert r2.status_code == 200, r2.text


def test_api_key_isolated_per_customer(client, db):
    """Pedir la clave de un cliente con el id de otro no la expone ni la revoca."""
    h = _operator(client, db)
    c1 = _customer_with_services(db, key="cust-1")
    c2 = _customer_with_services(db, key="cust-2")
    key1 = _create_key(client, h, c1.id, service_codes=[SERVICE_DTE])

    r = client.delete(f"/admin/customers/{c2.id}/api-keys/{key1['id']}", headers=h)
    assert r.status_code == 404


# --- Auditoría y last_used_at ------------------------------------------------


def test_audit_records_key_name_on_write(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    _create_key(client, h, c.id, name="Odoo prod", service_codes=[SERVICE_DTE])
    changes = client.get("/audit/changes", headers=h).json()
    assert any(ch["action"] == "api_key.create" and "Odoo prod" in ch["summary"] for ch in changes)


def test_access_log_records_key_name_for_customer_calls(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    key = _create_key(client, h, c.id, name="Odoo prod", service_codes=[SERVICE_DTE])

    r = client.get("/dte/folios", headers=headers(c.key, key["api_key"]))
    assert r.status_code == 200

    rows = client.get("/audit/requests", headers=h).json()
    hit = next(row for row in rows if row["path"] == "/dte/folios")
    assert hit["meta"]["api_key_name"] == "Odoo prod"


def test_last_used_at_is_set_after_use(client, db):
    c = _customer_with_services(db)
    h = _operator(client, db)
    key = _create_key(client, h, c.id, service_codes=[SERVICE_DTE])

    rows_before = client.get(f"/admin/customers/{c.id}/api-keys", headers=h).json()
    assert rows_before[0]["last_used_at"] is None

    assert client.get("/dte/folios", headers=headers(c.key, key["api_key"])).status_code == 200

    rows_after = client.get(f"/admin/customers/{c.id}/api-keys", headers=h).json()
    assert rows_after[0]["last_used_at"] is not None
