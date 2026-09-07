"""Endurecimiento para exponer el servicio a internet.

Cubre los tres huecos que se cerraron primero: fuerza bruta sobre X-Admin-Key,
poder apagar la clave de bootstrap y las cabeceras de seguridad.
"""

from app.core.config import get_settings
from app.security.auth import _admin_failures
from tests.conftest import auth_header, make_user

_ENV_KEY = "test-admin-key-0123456789"  # el de conftest


def _machine_key(client, db, role="operator"):
    make_user(db, "su@dimabe.cl", "secret", "superadmin")
    h = auth_header(client, "su@dimabe.cl", "secret")
    r = client.post("/machine-keys", json={"name": "odoo", "role": role}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["api_key"]


# --- 1. Límite de intentos sobre X-Admin-Key ---------------------------------


def test_admin_key_brute_force_is_limited(client):
    bad = {"X-Admin-Key": "no-es"}
    for _ in range(_admin_failures.max_events):
        assert client.get("/admin/customers", headers=bad).status_code == 401
    r = client.get("/admin/customers", headers=bad)
    assert r.status_code == 429


def test_lockout_also_blocks_the_valid_key(client):
    """La ventana es por IP: quien agotó los intentos no entra ni acertando."""
    bad = {"X-Admin-Key": "no-es"}
    for _ in range(_admin_failures.max_events):
        client.get("/admin/customers", headers=bad)
    r = client.get("/admin/customers", headers={"X-Admin-Key": _ENV_KEY})
    assert r.status_code == 429


def test_role_denial_is_not_a_failed_attempt(client, db):
    """Un auditor que intenta escribir presenta una credencial VÁLIDA: 403 sin
    contarle intentos, o el uso normal terminaría bloqueándose solo."""
    key = _machine_key(client, db, role="auditor")
    hk = {"X-Admin-Key": key}
    for _ in range(_admin_failures.max_events + 5):
        r = client.post(
            "/admin/customers", json={"name": "A", "key": "k1", "rut": "76158145-7"}, headers=hk
        )
        assert r.status_code == 403
    assert client.get("/admin/customers", headers=hk).status_code == 200


# --- 2. Apagar la clave de bootstrap ----------------------------------------


def test_bootstrap_key_can_be_disabled(client, db, monkeypatch):
    settings = get_settings()
    key = _machine_key(client, db)  # una MachineKey que la reemplace
    assert client.get("/admin/customers", headers={"X-Admin-Key": _ENV_KEY}).status_code == 200

    monkeypatch.setattr(settings, "admin_bootstrap_key_enabled", False)
    assert client.get("/admin/customers", headers={"X-Admin-Key": _ENV_KEY}).status_code == 401
    # La clave de máquina sigue entrando: apagar la de entorno no deja fuera a Odoo.
    assert client.get("/admin/customers", headers={"X-Admin-Key": key}).status_code == 200


def test_disabled_bootstrap_key_counts_as_failed_attempt(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_bootstrap_key_enabled", False)
    for _ in range(_admin_failures.max_events):
        assert client.get("/admin/customers", headers={"X-Admin-Key": _ENV_KEY}).status_code == 401
    assert client.get("/admin/customers", headers={"X-Admin-Key": _ENV_KEY}).status_code == 429


# --- 3. Cabeceras de seguridad ----------------------------------------------


def test_security_headers_on_every_response(client):
    h = client.get("/health").headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer"


def test_security_headers_on_errors(client):
    """También en los 4xx: un 401 embebido en un iframe sigue siendo el portal."""
    h = client.get("/admin/customers").headers
    assert h["X-Frame-Options"] == "DENY"


def test_no_hsts_without_tls(client):
    """En dev (http) HSTS fijaría el navegador a https contra localhost."""
    assert "Strict-Transport-Security" not in client.get("/health").headers


def test_hsts_when_there_is_tls(monkeypatch):
    """``cookie_secure`` es la señal de que hay TLS delante (Traefik en Dokploy)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    hsts = TestClient(create_app()).get("/health").headers["Strict-Transport-Security"]
    assert "max-age=31536000" in hsts and "includeSubDomains" in hsts
