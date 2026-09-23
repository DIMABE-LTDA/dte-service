"""Las tablas de Aduana que el facturador publica para el ERP."""

from app.security.service_codes import SERVICE_DTE
from tests.conftest import grant, headers, make_customer


def _setup(db):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    return customer


def test_las_tablas_de_aduana_salen_del_facturador(client, db):
    """El ERP no lleva su propia copia: una tabla vieja son documentos rechazados."""
    _setup(db)
    r = client.get("/dte/customs", headers=headers())
    assert r.status_code == 200, r.text
    tablas = r.json()

    assert {"code": 5, "name": "FOB"} in tablas["sale_clauses"]
    assert {"code": 1, "name": "A FIRME"} in tablas["sale_modes"]
    assert any(p["name"] == "AEREO" for p in tablas["transport_routes"])
    assert len(tablas["countries"]) > 100
    assert len(tablas["ports"]) > 100


def test_las_monedas_son_los_nombres_que_exige_el_xsd(client, db):
    """<TpoMoneda> no lleva código sino el nombre literal del Servicio."""
    _setup(db)
    monedas = client.get("/dte/customs", headers=headers()).json()["currencies"]

    assert "DOLAR USA" in monedas
    assert "LIBRA EST" in monedas  # la que aceptó el SII en la certificación
    assert "PESO CL" in monedas
    assert "USD" not in monedas  # el código ISO no vale en el XML


def test_las_tablas_piden_credencial(client, db):
    make_customer(db)  # sin grant de DTE
    assert client.get("/dte/customs", headers=headers()).status_code == 401
