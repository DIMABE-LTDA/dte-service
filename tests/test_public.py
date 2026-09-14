"""Consulta pública de boletas.

Es la única superficie sin autenticación del servicio, así que lo que más se
prueba acá es lo que NO debe poder hacerse: enumerar folios, adivinar montos, o
llegar a datos de otro emisor.
"""

import base64
import datetime as dt

import pytest

from app.core import crypto
from app.db.models import IssuedReceipt
from app.routers import public
from tests.conftest import make_customer

# Un <DTE> mínimo pero real: el endpoint lo reparsea para imprimirlo.
_DTE_XML = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><Documento ID="F7T39">
<Encabezado><IdDoc><TipoDTE>39</TipoDTE><Folio>7</Folio>
<FchEmis>2026-09-01</FchEmis><IndServicio>3</IndServicio></IdDoc>
<Emisor><RUTEmisor>76158145-7</RUTEmisor><RznSocEmisor>DEMO SPA</RznSocEmisor>
<GiroEmisor>Venta</GiroEmisor><DirOrigen>Calle 1</DirOrigen>
<CmnaOrigen>Santiago</CmnaOrigen></Emisor>
<Receptor><RUTRecep>66666666-6</RUTRecep></Receptor>
<Totales><MntNeto>1714</MntNeto><IVA>326</IVA><MntTotal>2040</MntTotal></Totales>
</Encabezado>
<Detalle><NroLinDet>1</NroLinDet><NmbItem>Papel de regalo</NmbItem>
<QtyItem>17</QtyItem><PrcItem>120</PrcItem><MontoItem>2040</MontoItem></Detalle>
<TED version="1.0"><DD><RE>76158145-7</RE><F>7</F></DD>
<FRMT algoritmo="SHA1withRSA">ZmFrZQ==</FRMT></TED>
<TmstFirma>2026-09-01T12:00:00</TmstFirma>
</Documento></DTE>"""


@pytest.fixture(autouse=True)
def _reset_limiter():
    public._lookup_limiter.reset()
    yield
    public._lookup_limiter.reset()


def _store(db, customer, folio=7, date="2026-09-01", total=2040):
    db.add(
        IssuedReceipt(
            customer_id=customer.id,
            doc_type=39,
            folio=folio,
            issue_date=dt.date.fromisoformat(date),
            total_amount=total,
            xml_encrypted=crypto.encrypt(_DTE_XML),
        )
    )
    db.commit()


def _lookup(client, **over):
    body = {
        "rut": "76158145-7",
        "folio": 7,
        "issue_date": "2026-09-01",
        "total_amount": 2040,
    }
    body.update(over)
    return client.post("/public/boletas/lookup", json=body)


# --------------------------------------------------------------------------- #
#  Consulta correcta
# --------------------------------------------------------------------------- #
def test_finds_the_receipt_with_the_four_data(client, db):
    customer = make_customer(db)
    _store(db, customer)

    r = _lookup(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["folio"] == 7
    assert body["total_amount"] == 2040
    assert body["issuer_name"] == customer.name

    html = base64.b64decode(body["html_base64"]).decode("utf-8")
    assert "BOLETA ELECTRÓNICA" in html
    assert "Papel de regalo" in html
    assert "$2.040" in html


def test_no_authentication_needed(client, db):
    """El comprador no tiene credenciales: sólo el papel."""
    customer = make_customer(db)
    _store(db, customer)
    r = client.post(
        "/public/boletas/lookup",
        json={
            "rut": "76158145-7",
            "folio": 7,
            "issue_date": "2026-09-01",
            "total_amount": 2040,
        },
    )
    assert r.status_code == 200


def test_issuer_name_is_public(client, db):
    customer = make_customer(db)
    r = client.get("/public/issuer/76158145-7")
    assert r.status_code == 200
    assert r.json() == {"rut": customer.rut, "name": customer.name}


# --------------------------------------------------------------------------- #
#  Sitio multiempresa
# --------------------------------------------------------------------------- #
def test_lists_issuers_so_the_buyer_can_choose(client, db):
    uno = make_customer(db, rut="76158145-7", key="uno")
    dos = make_customer(db, rut="77262159-0", key="dos")
    _store(db, uno)
    _store(db, dos)

    r = client.get("/public/issuers")
    assert r.status_code == 200
    assert {i["rut"] for i in r.json()} == {uno.rut, dos.rut}


def test_issuers_without_receipts_are_not_listed(client, db):
    """Elegirlos no llevaría a nada, y la lista no debe ser la cartera de clientes."""
    con_boletas = make_customer(db, rut="76158145-7", key="uno")
    make_customer(db, rut="77262159-0", key="dos")
    _store(db, con_boletas)

    assert [i["rut"] for i in client.get("/public/issuers").json()] == [con_boletas.rut]


def test_deleted_issuers_are_not_listed(client, db):
    from app.services import customer_service

    customer = make_customer(db)
    _store(db, customer)
    customer_service.soft_delete_customer(db, customer)

    assert client.get("/public/issuers").json() == []


def test_an_issuer_is_listed_once_no_matter_how_many_receipts(client, db):
    customer = make_customer(db)
    _store(db, customer, folio=7)
    _store(db, customer, folio=8)

    assert len(client.get("/public/issuers").json()) == 1


# --------------------------------------------------------------------------- #
#  Lo que NO debe poder hacerse
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("campo", "valor"),
    [("folio", 8), ("issue_date", "2026-09-02"), ("total_amount", 2041)],
)
def test_any_wrong_field_finds_nothing(client, db, campo, valor):
    """Los tres datos deben calzar: con folio solo se enumerarían las ventas."""
    customer = make_customer(db)
    _store(db, customer)
    assert _lookup(client, **{campo: valor}).status_code == 404


def test_same_message_whether_it_exists_or_not(client, db):
    """Distinguir los casos permitiría confirmar qué folios existen."""
    customer = make_customer(db)
    _store(db, customer)

    inexistente = _lookup(client, folio=999)
    monto_errado = _lookup(client, total_amount=1)
    assert inexistente.status_code == monto_errado.status_code == 404
    assert inexistente.json()["detail"] == monto_errado.json()["detail"]


def test_cannot_reach_another_issuers_receipts(client, db):
    customer = make_customer(db, rut="76158145-7", key="uno")
    _store(db, customer)
    make_customer(db, rut="77262159-0", key="dos")

    assert _lookup(client, rut="77262159-0").status_code == 404


def test_unknown_issuer_is_404(client, db):
    assert _lookup(client, rut="11111111-1").status_code == 404
    assert client.get("/public/issuer/11111111-1").status_code == 404


def test_deleted_issuer_is_not_served(client, db):
    from app.services import customer_service

    customer = make_customer(db)
    _store(db, customer)
    customer_service.soft_delete_customer(db, customer)

    assert _lookup(client).status_code == 404


def test_rate_limit_stops_brute_forcing_the_amount(client, db, monkeypatch):
    """Sin límite, el monto se adivinaría probando: es sólo un número."""
    from app.security.ratelimit import SlidingWindowLimiter

    monkeypatch.setattr(public, "_lookup_limiter", SlidingWindowLimiter(3, 60.0))
    customer = make_customer(db)
    _store(db, customer)

    for _ in range(3):
        _lookup(client, total_amount=1)
    r = _lookup(client)  # datos correctos, pero ya se pasó del límite
    assert r.status_code == 429
    assert "Espera un minuto" in r.json()["detail"]


def test_lookup_rejects_nonsense_input(client, db):
    make_customer(db)
    assert _lookup(client, folio=0).status_code == 422
    assert _lookup(client, total_amount=-1).status_code == 422
    assert _lookup(client, issue_date="no-es-fecha").status_code == 422
