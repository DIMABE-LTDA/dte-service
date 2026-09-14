"""Endpoints admin de la clave tributaria del SII (status / store / delete)."""

ADMIN = {"X-Admin-Key": "test-admin-key-0123456789"}


def _create(client):
    r = client.post("/admin/customers", json={"name": "K", "rut": "76158145-7"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_sii_key_lifecycle(client, db):
    cid = _create(client)
    assert (
        client.get(f"/admin/customers/{cid}/sii-key", headers=ADMIN).json()["configured"] is False
    )
    assert (
        client.post(
            f"/admin/customers/{cid}/sii-key", json={"password": "clave-real"}, headers=ADMIN
        ).status_code
        == 200
    )
    assert client.get(f"/admin/customers/{cid}/sii-key", headers=ADMIN).json()["configured"] is True
    assert client.delete(f"/admin/customers/{cid}/sii-key", headers=ADMIN).status_code == 200
    assert (
        client.get(f"/admin/customers/{cid}/sii-key", headers=ADMIN).json()["configured"] is False
    )
