import base64
import datetime as dt

from app.security.service_codes import SERVICE_RCV
from app.services import certificate_service, rcv_service
from tests.conftest import auth_header, fake_caf_xml, make_customer, make_user
from tests.test_auth_rcv import _FakeRcv

ADMIN = {"X-Admin-Key": "test-admin-key-0123456789"}


def _create(client, key="c1", rut="76158145-7"):
    r = client.post(
        "/admin/customers",
        json={"name": "Demo", "key": key, "rut": rut},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_admin_requires_auth(client):
    r = client.post("/admin/customers", json={"name": "A", "key": "k", "rut": "76158145-7"})
    assert r.status_code == 401  # ni Bearer ni X-Admin-Key


def test_admin_wrong_key(client):
    r = client.post(
        "/admin/customers",
        json={"name": "A", "key": "k", "rut": "76158145-7"},
        headers={"X-Admin-Key": "nope"},
    )
    assert r.status_code == 401


def test_admin_via_operator_jwt(client, db):
    make_user(db, "op@dimabe.cl", "secret", "operator")
    h = auth_header(client, "op@dimabe.cl", "secret")
    r = client.post(
        "/admin/customers", json={"name": "Demo", "key": "jwtc", "rut": "76158145-7"}, headers=h
    )
    assert r.status_code == 200, r.text
    # el cambio quedó auditado con el actor (operador)
    changes = client.get("/audit/changes", headers=h).json()
    assert any(c["action"] == "customer.create" for c in changes)


def test_admin_auditor_cannot_write(client, db):
    make_user(db, "aud@dimabe.cl", "secret", "auditor")
    h = auth_header(client, "aud@dimabe.cl", "secret")
    r = client.post(
        "/admin/customers", json={"name": "A", "key": "k", "rut": "76158145-7"}, headers=h
    )
    assert r.status_code == 403  # auditor no escribe


def test_create_grant_and_caf(client):
    cid = _create(client)

    r = client.post(
        f"/admin/customers/{cid}/services",
        json={"service_code": SERVICE_RCV, "apikey": "k"},
        headers=ADMIN,
    )
    assert r.status_code == 200 and r.json()["granted"] is True

    r = client.post(
        f"/admin/customers/{cid}/caf",
        json={"xml_base64": base64.b64encode(fake_caf_xml(33, 1, 5)).decode()},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    assert r.json()["doc_type"] == 33 and r.json()["folio_to"] == 5


def _describe(pfx: bytes, password: str) -> dict:
    """`describe` parsea el .pfx de verdad; aquí los .pfx son de juguete.

    La huella se deriva de los bytes para que dos .pfx distintos no parezcan el
    mismo certificado: es justo lo que comprueba el test de duplicados.
    """
    import hashlib

    return {
        "due_date": dt.date(2030, 1, 1),
        "holder": "TITULAR DE PRUEBA",
        "issuer": "CA DE PRUEBA",
        "thumbprint": hashlib.sha256(pfx).hexdigest(),
    }


def test_upload_certificate(client, monkeypatch):
    cid = _create(client, key="c2")
    monkeypatch.setattr(certificate_service, "describe", _describe)

    r = client.post(
        f"/admin/customers/{cid}/certificate",
        json={"file_base64": base64.b64encode(b"dummy-pfx").decode(), "password": "pw"},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    assert r.json()["rut"] == "76158145-7"


def test_operator_rcv(client, db, monkeypatch):
    customer = make_customer(db)  # crea cliente + certificado (fakeado)
    monkeypatch.setattr(rcv_service, "RCVClient", _FakeRcv)

    r = client.post(
        f"/admin/customers/{customer.id}/rcv",
        json={"period": "202505", "operation": "COMPRA"},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["issuer_rut"] == "76158145-7"
    assert body["count"] == 1


def test_operator_rcv_requires_auth(client, db):
    customer = make_customer(db)
    r = client.post(
        f"/admin/customers/{customer.id}/rcv",
        json={"period": "202505", "operation": "COMPRA"},
    )
    assert r.status_code == 401  # ni Bearer ni X-Admin-Key


def test_auditor_can_read_but_not_write(client, db):
    make_user(db, "aud2@dimabe.cl", "secret", "auditor")
    h = auth_header(client, "aud2@dimabe.cl", "secret")
    assert client.get("/admin/customers", headers=h).status_code == 200  # lee
    r = client.post("/admin/customers", json={"name": "X", "key": "z", "rut": "1-9"}, headers=h)
    assert r.status_code == 403  # no escribe


def test_list_customers_pagination(client):
    for i in range(3):
        _create(client, key=f"pg{i}")
    page1 = client.get("/admin/customers?limit=2&offset=0", headers=ADMIN).json()
    assert len(page1) == 2
    page2 = client.get("/admin/customers?limit=2&offset=2", headers=ADMIN).json()
    assert len(page2) >= 1
    assert {c["id"] for c in page1}.isdisjoint({c["id"] for c in page2})


def test_failed_create_writes_no_audit(client, db):
    """Atomicidad: si el create falla (clave duplicada → 409) no queda auditoría."""
    from app.db.models import AdminAudit

    _create(client, key="atom")
    before = db.query(AdminAudit).count()
    r = client.post(
        "/admin/customers", json={"name": "X", "key": "atom", "rut": "76158145-7"}, headers=ADMIN
    )
    assert r.status_code == 409
    assert db.query(AdminAudit).count() == before  # cambio y auditoría son atómicos


def test_get_customer_and_404(client):
    cid = _create(client)
    assert client.get(f"/admin/customers/{cid}", headers=ADMIN).status_code == 200
    assert client.get("/admin/customers/99999", headers=ADMIN).status_code == 404


def test_duplicate_key_returns_409(client):
    _create(client, key="dup")
    r = client.post(
        "/admin/customers", json={"name": "X", "key": "dup", "rut": "76158145-7"}, headers=ADMIN
    )
    assert r.status_code == 409


def test_grant_rotation_and_revoke(client):
    cid = _create(client, key="gr")
    for _ in range(2):  # habilitar dos veces = rotación, sin 500
        r = client.post(
            f"/admin/customers/{cid}/services",
            json={"service_code": SERVICE_RCV, "apikey": "k"},
            headers=ADMIN,
        )
        assert r.status_code == 200, r.text

    svcs = client.get(f"/admin/customers/{cid}/services", headers=ADMIN).json()
    assert len(svcs) == 1 and svcs[0]["service_code"] == SERVICE_RCV

    assert (
        client.delete(f"/admin/customers/{cid}/services/{SERVICE_RCV}", headers=ADMIN).status_code
        == 200
    )
    assert client.get(f"/admin/customers/{cid}/services", headers=ADMIN).json() == []
    assert (
        client.delete(f"/admin/customers/{cid}/services/{SERVICE_RCV}", headers=ADMIN).status_code
        == 404
    )


def test_grant_unknown_service_returns_400(client):
    cid = _create(client, key="badsvc")
    r = client.post(
        f"/admin/customers/{cid}/services",
        json={"service_code": "no-existe", "apikey": "k"},
        headers=ADMIN,
    )
    assert r.status_code == 400
    assert "service_code desconocido" in r.json()["error"]["message"]


def test_certificate_rut_mismatch_allowed(client, monkeypatch):
    # El certificado del SII se emite a una persona natural: su RUT puede diferir
    # del de la empresa. Por eso un cert con RUT distinto se acepta (no se valida).
    monkeypatch.setattr(certificate_service, "describe", _describe)
    cid = _create(client, key="rutmm", rut="11111111-1")
    r = client.post(
        f"/admin/customers/{cid}/certificate",
        json={"file_base64": base64.b64encode(b"x").decode(), "password": "pw"},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text


def test_caf_bad_base64_returns_400(client):
    cid = _create(client, key="badb64")
    r = client.post(f"/admin/customers/{cid}/caf", json={"xml_base64": "@@@"}, headers=ADMIN)
    assert r.status_code == 400


def test_caf_rut_mismatch_returns_400(client):
    # Cliente con RUT distinto al <RE> del CAF (76158145-7) → rechazo.
    cid = _create(client, key="cafrut", rut="11111111-1")
    r = client.post(
        f"/admin/customers/{cid}/caf",
        json={"xml_base64": base64.b64encode(fake_caf_xml(33, 1, 5)).decode()},
        headers=ADMIN,
    )
    assert r.status_code == 400
    assert "no coincide" in r.json()["error"]["message"]


def test_caf_overlapping_range_returns_400(client):
    cid = _create(client, key="cafovl")
    ok = client.post(
        f"/admin/customers/{cid}/caf",
        json={"xml_base64": base64.b64encode(fake_caf_xml(33, 1, 10)).decode()},
        headers=ADMIN,
    )
    assert ok.status_code == 200
    dup = client.post(
        f"/admin/customers/{cid}/caf",
        json={"xml_base64": base64.b64encode(fake_caf_xml(33, 5, 15)).decode()},
        headers=ADMIN,
    )
    assert dup.status_code == 400
    assert "solapa" in dup.json()["error"]["message"]


def test_create_customer_invalid_rut_returns_422(client):
    r = client.post(
        "/admin/customers",
        json={"name": "X", "key": "badrut", "rut": "12345678-0"},  # DV incorrecto
        headers=ADMIN,
    )
    assert r.status_code == 422


def test_customer_cafs_listing(client):
    cid = _create(client, key="cafs")
    client.post(
        f"/admin/customers/{cid}/caf",
        json={"xml_base64": base64.b64encode(fake_caf_xml(33, 1, 5)).decode()},
        headers=ADMIN,
    )
    cafs = client.get(f"/admin/customers/{cid}/cafs", headers=ADMIN).json()
    assert len(cafs) == 1
    assert cafs[0]["doc_type"] == 33 and cafs[0]["folio_to"] == 5


def test_customer_certificates_listing(client, monkeypatch):
    cid = _create(client, key="certs")
    monkeypatch.setattr(certificate_service, "describe", _describe)
    client.post(
        f"/admin/customers/{cid}/certificate",
        json={"file_base64": base64.b64encode(b"x").decode(), "password": "pw"},
        headers=ADMIN,
    )
    certs = client.get(f"/admin/customers/{cid}/certificates", headers=ADMIN).json()
    assert len(certs) == 1 and certs[0]["expired"] is False


def test_no_deja_cargar_dos_veces_el_mismo_certificado(client, monkeypatch):
    """Cargarlo dos veces deja la ficha con filas indistinguibles.

    Y como se resuelve el más reciente, la copia tapa a la original sin que nada
    lo diga: quien reintenta un envío cree haber cambiado algo y no cambió nada.
    """
    cid = _create(client, key="c-dup")
    monkeypatch.setattr(certificate_service, "describe", _describe)
    cuerpo = {"file_base64": base64.b64encode(b"un-pfx").decode(), "password": "pw"}

    assert (
        client.post(f"/admin/customers/{cid}/certificate", json=cuerpo, headers=ADMIN).status_code
        == 200
    )

    r = client.post(f"/admin/customers/{cid}/certificate", json=cuerpo, headers=ADMIN)
    assert r.status_code == 400
    mensaje = r.json()["error"]["message"]
    assert "ya está cargado" in mensaje
    # Dice cuál, para no tener que adivinar cuál de las filas es.
    assert "id 1" in mensaje

    # Y uno distinto sí entra: el rechazo es por certificado, no por cliente.
    otro = {"file_base64": base64.b64encode(b"otro-pfx").decode(), "password": "pw"}
    assert (
        client.post(f"/admin/customers/{cid}/certificate", json=otro, headers=ADMIN).status_code
        == 200
    )


def test_la_ficha_muestra_el_rut_que_firma(client, monkeypatch):
    """El RUT del certificado es el que necesita «Enviar Doctos» en el SII.

    No es el de la empresa —el certificado se emite a una persona natural— y sin
    verlo no hay forma de saber a qué RUT darle el permiso cuando el envío sale
    rechazado.
    """
    cid = _create(client, key="c-rut")
    monkeypatch.setattr(certificate_service, "describe", _describe)
    client.post(
        f"/admin/customers/{cid}/certificate",
        json={"file_base64": base64.b64encode(b"pfx").decode(), "password": "pw"},
        headers=ADMIN,
    )

    fila = client.get(f"/admin/customers/{cid}/certificates", headers=ADMIN).json()[0]
    assert fila["rut"] == "76158145-7"  # el del stub de conftest
    assert fila["holder"] == "TITULAR DE PRUEBA"
    assert fila["issuer"] == "CA DE PRUEBA"
