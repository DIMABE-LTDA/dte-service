"""Endurecimiento para exponer el servicio a internet.

Cubre los tres huecos que se cerraron primero: fuerza bruta sobre X-Admin-Key,
poder apagar la clave de bootstrap y las cabeceras de seguridad.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.security.auth import _admin_failures
from app.security.service_codes import SERVICE_RCV
from app.security.tenant import _customer_quota
from app.services import rcv_service
from tests.conftest import auth_header, grant, headers, make_customer, make_user

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


# --- 4. CORS: el comodín con credenciales no debe poder configurarse ----------


@pytest.mark.parametrize("origins", ["*", "https://a.cl,*"])
def test_cors_rechaza_el_comodin(origins):
    """El API va con allow_credentials=True: '*' es la combinación que el
    navegador rechaza, y arreglarla a mano suele terminar quitando credenciales."""
    with pytest.raises(ValidationError, match="no acepta"):
        Settings(cors_origins=origins)


def test_cors_exige_esquema():
    """El header Origin siempre trae esquema; sin él no casa nunca y el fallo es
    silencioso."""
    with pytest.raises(ValidationError, match="esquema"):
        Settings(cors_origins="dte.dimabe.cl")


@pytest.mark.parametrize("origins", ["", "https://dte.dimabe.cl", "https://a.cl,https://b.cl"])
def test_cors_acepta_origenes_explicitos(origins):
    assert Settings(cors_origins=origins).cors_origins == origins


# --- 5. Cuota del cliente autenticado ----------------------------------------


def _con_cuota(monkeypatch, n):
    """Baja la cuota para no tener que hacer 120 llamadas en un test."""
    assert _customer_quota is not None, "la cuota está apagada en la configuración"
    monkeypatch.setattr(_customer_quota, "max_events", n)
    return _customer_quota


def _cliente_rcv(db, key, apikey):
    c = make_customer(db, key=key)
    grant(db, c, SERVICE_RCV, apikey)
    return c


def test_un_cliente_autenticado_tiene_cuota(client, db, monkeypatch):
    """Antes, un cliente que acertaba su apiKey llamaba sin tope: las operaciones
    caras —firmar, hablar con el SII— las pagaban todos."""
    from tests.test_auth_rcv import _FakeRcv

    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)
    _con_cuota(monkeypatch, 3)
    _cliente_rcv(db, "cust-1", "secret")

    payload = {"period": "202505", "operation": "COMPRA"}
    for _ in range(3):
        assert client.post("/rcv/documents", json=payload, headers=headers()).status_code == 200
    r = client.post("/rcv/documents", json=payload, headers=headers())
    assert r.status_code == 429
    assert "cuota" in r.json()["detail"]


def test_la_cuota_es_por_cliente_no_global(client, db, monkeypatch):
    """Un cliente que se pasa no puede dejar fuera a los demás: por eso la llave
    es el cliente y no la IP."""
    from tests.test_auth_rcv import _FakeRcv

    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)
    _con_cuota(monkeypatch, 2)
    _cliente_rcv(db, "cust-1", "secret")
    _cliente_rcv(db, "cust-2", "otra-clave")

    payload = {"period": "202505", "operation": "COMPRA"}
    for _ in range(2):
        client.post("/rcv/documents", json=payload, headers=headers())
    assert client.post("/rcv/documents", json=payload, headers=headers()).status_code == 429

    otro = headers("cust-2", "otra-clave")
    assert client.post("/rcv/documents", json=payload, headers=otro).status_code == 200


def test_los_fallos_de_autenticacion_no_gastan_la_cuota(client, db, monkeypatch):
    """Si no, cualquiera desde fuera podría agotarle la cuota a un cliente ajeno
    sin conocer su credencial."""
    from tests.test_auth_rcv import _FakeRcv

    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)
    _con_cuota(monkeypatch, 2)
    _cliente_rcv(db, "cust-1", "secret")

    payload = {"period": "202505", "operation": "COMPRA"}
    for _ in range(5):
        mala = client.post("/rcv/documents", json=payload, headers=headers("cust-1", "no-es"))
        assert mala.status_code == 401
    # La cuota sigue entera.
    assert client.post("/rcv/documents", json=payload, headers=headers()).status_code == 200
