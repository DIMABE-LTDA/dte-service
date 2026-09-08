import base64

import pytest
from dte_chile.sii_client import SubmissionResult
from pydantic import ValidationError

from app.schemas.book import BookLineIn
from app.security.service_codes import SERVICE_BOOK
from app.services import book_service, sii_upload
from tests.conftest import grant, headers, make_customer

_PAYLOAD = {
    "period": "2026-05",
    "operation_type": "VENTA",
    "lines": [
        {
            "doc_type": 33,
            "folio": 1,
            "date": "2026-05-10",
            "rut": "77073851-2",
            "business_name": "CLIENTE",
            "net_amount": 1000,
            "vat_amount": 190,
            "total_amount": 1190,
        }
    ],
    # Estos tests arman el libro con un motor simulado: ni se envía al SII ni
    # se valida contra el XSD (ambos tienen sus propios tests).
    "send": False,
    "validate_xsd": False,
}


@pytest.fixture
def fake_book_engine(monkeypatch):
    monkeypatch.setattr(book_service, "build_book", lambda cover, cert, ts: "BOOK")
    monkeypatch.setattr(book_service, "serialize", lambda x: b"<LibroCompraVenta/>")


def test_build_book(client, db, fake_book_engine):
    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)

    r = client.post("/books", json=_PAYLOAD, headers=headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["operation_type"] == "VENTA"
    assert base64.b64decode(body["xml_base64"]) == b"<LibroCompraVenta/>"


# --------------------------------------------------------------------------- #
#  Libro de Guías de Despacho (Res. Ex. N°154 / set 5038174)
# --------------------------------------------------------------------------- #
_GUIDES_PAYLOAD = {
    "period": "2026-11",
    "notification_folio": 5038174,
    "lines": [
        {
            "folio": 1,
            "date": "2026-11-03",
            "transfer_type": 5,
            "receiver_rut": "76158145-7",
            "receiver_name": "EMISOR",
        },
        {
            "folio": 2,
            "date": "2026-11-04",
            "transfer_type": 1,
            "receiver_rut": "77073851-2",
            "receiver_name": "CLIENTE",
            "net_amount": 2586065,
            "vat_amount": 491352,
            "total_amount": 3077417,
            "modified_amount": 3077417,
            "ref_doc_type": 33,
            "ref_folio": 120,
            "ref_date": "2026-11-10",
        },
        {"folio": 3, "voided": 2},
    ],
    # Estos tests arman el libro con un motor simulado: ni se envía al SII ni
    # se valida contra el XSD (ambos tienen sus propios tests).
    "send": False,
    "validate_xsd": False,
}


@pytest.fixture
def fake_guide_book_engine(monkeypatch):
    monkeypatch.setattr(book_service, "build_guide_book", lambda cover, cert, ts: cover)
    monkeypatch.setattr(book_service, "serialize_guide_book", lambda x: b"<LibroGuia/>")


def test_build_guide_book(client, db, fake_guide_book_engine):
    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)

    r = client.post("/books/guides", json=_GUIDES_PAYLOAD, headers=headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == "2026-11"
    assert base64.b64decode(body["xml_base64"]) == b"<LibroGuia/>"


def test_guide_book_maps_transfer_and_void_codes(client, db, monkeypatch):
    """Los códigos del request llegan al motor como sus enums, no como int."""
    from dte_chile.document_types import TransferType
    from dte_chile.guide_book import VoidStatus

    captured = {}

    def _capture(cover, cert, ts):
        captured["cover"] = cover
        return cover

    monkeypatch.setattr(book_service, "build_guide_book", _capture)
    monkeypatch.setattr(book_service, "serialize_guide_book", lambda x: b"<LibroGuia/>")

    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)
    r = client.post("/books/guides", json=_GUIDES_PAYLOAD, headers=headers())
    assert r.status_code == 200, r.text

    cover = captured["cover"]
    assert cover.notification_folio == 5038174
    assert cover.submission_type == "TOTAL"
    assert cover.lines[0].transfer_type is TransferType.INTERNAL
    assert cover.lines[1].ref_folio == 120
    assert cover.lines[2].voided is VoidStatus.AFTER_SENDING


def test_guide_book_requires_book_service(client, db):
    """Sin el servicio BOOK habilitado el cliente no resuelve: 401, como el resto."""
    make_customer(db)  # sin grant
    r = client.post("/books/guides", json=_GUIDES_PAYLOAD, headers=headers())
    assert r.status_code == 401, r.text


# --------------------------------------------------------------------------- #
#  Libro de Compras (set 5038172)
# --------------------------------------------------------------------------- #
_PURCHASE_PAYLOAD = {
    "period": "2026-08",
    "operation_type": "COMPRA",
    "proportionality_factor": 0.60,
    "lines": [
        {
            "doc_type": 30,
            "folio": 781,
            "date": "2026-08-10",
            "rut": "77073851-2",
            "business_name": "PROVEEDOR C",
            "net_amount": 30019,
            "common_use_vat": 5704,
            "total_amount": 35723,
        },
        {
            "doc_type": 33,
            "folio": 67,
            "date": "2026-08-10",
            "rut": "77073851-2",
            "business_name": "PROVEEDOR D",
            "net_amount": 11305,
            "non_recoverable_vat": [{"code": 4, "amount": 2148}],
            "total_amount": 13453,
        },
        {
            "doc_type": 46,
            "folio": 9,
            "date": "2026-08-10",
            "rut": "77073851-2",
            "business_name": "PROVEEDOR E",
            "net_amount": 10215,
            "retained_total_vat": 1941,
            "total_amount": 10215,
        },
    ],
    # Estos tests arman el libro con un motor simulado: ni se envía al SII ni
    # se valida contra el XSD (ambos tienen sus propios tests).
    "send": False,
    "validate_xsd": False,
}


def test_purchase_book_maps_the_new_fields(client, db, monkeypatch):
    """Los campos de compras deben llegar al motor como sus dataclasses."""
    from dte_chile.book import NonRecoverableVat

    captured = {}
    monkeypatch.setattr(
        book_service, "build_book", lambda cover, cert, ts: captured.setdefault("cover", cover)
    )
    monkeypatch.setattr(book_service, "serialize", lambda x: b"<LibroCompraVenta/>")

    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)
    r = client.post("/books", json=_PURCHASE_PAYLOAD, headers=headers())
    assert r.status_code == 200, r.text

    cover = captured["cover"]
    assert cover.operation_type == "COMPRA"
    assert cover.proportionality_factor == 0.60
    assert cover.lines[0].common_use_vat == 5704
    assert cover.lines[1].non_recoverable_vat == [NonRecoverableVat(code=4, amount=2148)]
    assert cover.lines[2].retained_total_vat == 1941


def test_proportionality_factor_must_be_a_fraction(client, db):
    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)
    payload = dict(_PURCHASE_PAYLOAD, proportionality_factor=1.5)
    r = client.post("/books", json=payload, headers=headers())
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
#  Envío al SII
# --------------------------------------------------------------------------- #
class _FakeSession:
    def close(self):
        pass


@pytest.fixture
def fake_sii(monkeypatch):
    """Captura lo que se sube, sin tocar la red."""
    seen: dict = {"uploads": []}

    class _FakeSII:
        def __init__(self, cert, environment, timeout=30):
            self.session = _FakeSession()

        def send_dte(self, xml, issuer_rut, sender_rut):
            seen["uploads"].append({"xml": xml, "issuer_rut": issuer_rut})
            return SubmissionResult(track_id="T555", status="0", detail="recibido")

    monkeypatch.setattr(sii_upload, "SIIClient", _FakeSII)
    return seen


def test_sales_book_is_uploaded_to_the_sii(client, db, fake_book_engine, fake_sii):
    """El set de certificación exige enviar el libro, no sólo construirlo."""
    grant(db, make_customer(db), SERVICE_BOOK)
    payload = {**_PAYLOAD, "send": True}

    r = client.post("/books", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["track_id"] == "T555"
    assert len(fake_sii["uploads"]) == 1
    assert b"LibroCompraVenta" in fake_sii["uploads"][0]["xml"]


def test_guide_book_is_uploaded_to_the_sii(client, db, fake_guide_book_engine, fake_sii):
    grant(db, make_customer(db), SERVICE_BOOK)
    payload = {**_GUIDES_PAYLOAD, "send": True}

    r = client.post("/books/guides", json=payload, headers=headers())
    assert r.status_code == 200, r.text
    assert r.json()["submission"]["track_id"] == "T555"
    assert b"LibroGuia" in fake_sii["uploads"][0]["xml"]


def test_the_book_is_uploaded_under_the_customers_rut(client, db, fake_book_engine, fake_sii):
    customer = make_customer(db)
    grant(db, customer, SERVICE_BOOK)
    client.post("/books", json={**_PAYLOAD, "send": True}, headers=headers())
    assert fake_sii["uploads"][0]["issuer_rut"] == customer.rut


def test_not_sending_leaves_no_submission(client, db, fake_book_engine, fake_sii):
    grant(db, make_customer(db), SERVICE_BOOK)
    r = client.post("/books", json={**_PAYLOAD, "send": False}, headers=headers())
    assert r.json()["submission"] is None
    assert fake_sii["uploads"] == []


def test_the_book_can_be_sent_as_a_certification_special(client, db, monkeypatch, fake_sii):
    """El set de certificación pide el libro como ESPECIAL con su número de atención.

    Un libro MENSUAL declara TODO el período y el SII lo contrasta contra los
    DTE que tiene registrados, así que un libro con sólo los documentos del set
    sale descuadrado (el Servicio responde LRH).
    """
    grant(db, make_customer(db), SERVICE_BOOK)
    visto = {}
    monkeypatch.setattr(
        book_service, "build_book", lambda cover, cert, ts: visto.setdefault("cover", cover)
    )
    monkeypatch.setattr(book_service, "serialize", lambda x: b"<LibroCompraVenta/>")

    payload = {**_PAYLOAD, "book_type": "ESPECIAL", "notification_folio": 5038171}
    r = client.post("/books", json=payload, headers=headers())

    assert r.status_code == 200, r.text
    assert visto["cover"].book_type == "ESPECIAL"
    assert visto["cover"].notification_folio == 5038171


def test_the_book_is_monthly_by_default(client, db, monkeypatch, fake_sii):
    grant(db, make_customer(db), SERVICE_BOOK)
    visto = {}
    monkeypatch.setattr(
        book_service, "build_book", lambda cover, cert, ts: visto.setdefault("cover", cover)
    )
    monkeypatch.setattr(book_service, "serialize", lambda x: b"<LibroCompraVenta/>")

    assert client.post("/books", json=_PAYLOAD, headers=headers()).status_code == 200
    assert visto["cover"].book_type == "MENSUAL"


def test_a_malformed_book_is_caught_before_reaching_the_sii(client, db, monkeypatch, fake_sii):
    """Los libros se validan contra el XSD como los documentos.

    Sin esto, un libro mal formado se subía igual y el SII lo devolvía con
    `LRS` (rechazado por schema), sin decir qué elemento estaba mal.
    """
    grant(db, make_customer(db), SERVICE_BOOK)
    monkeypatch.setattr(book_service, "build_book", lambda cover, cert, ts: "BOOK")
    # Raíz correcta pero sin carátula ni resumen: es lo que el XSD rechaza.
    monkeypatch.setattr(book_service, "serialize", lambda x: b"<LibroCompraVenta/>")

    r = client.post("/books", json={**_PAYLOAD, "validate_xsd": True}, headers=headers())

    assert r.status_code == 422, r.text
    assert fake_sii["uploads"] == []  # no llegó a subirse


# --------------------------------------------------------------------------- #
#  Moneda extranjera: el IECV va en pesos
# --------------------------------------------------------------------------- #
def _linea(**extra):
    base = {
        "doc_type": 110,
        "folio": 7,
        "date": "2026-05-10",
        "rut": "55555555-5",
        "business_name": "COMPRADOR EXTRANJERO",
    }
    return {**base, **extra}


def test_exportacion_se_convierte_a_pesos():
    """Una factura de USD 15,40 entraba al libro como 15 pesos: el monto del
    documento leído como si fuera nacional."""
    linea = book_service._book_line(
        BookLineIn(
            **_linea(
                exempt_amount="15.40",
                total_amount="15.40",
                currency="DOLAR USA",
                exchange_rate="950.25",
            )
        )
    )
    # 15,40 × 950,25 = 14.633,85 → 14.634
    assert linea.exempt_amount == 14634
    assert linea.total_amount == 14634
    assert isinstance(linea.exempt_amount, int)


def test_el_redondeo_es_medio_hacia_arriba():
    """round() redondea al par: 0,5 caería unas veces arriba y otras abajo, y no
    es lo que hace el SII ni quien cuadra el libro a mano."""
    linea = book_service._book_line(
        BookLineIn(**_linea(exempt_amount="1.5", total_amount="1.5", currency="X", exchange_rate=1))
    )
    assert linea.exempt_amount == 2
    linea = book_service._book_line(
        BookLineIn(**_linea(exempt_amount="2.5", total_amount="2.5", currency="X", exchange_rate=1))
    )
    assert linea.exempt_amount == 3


def test_la_linea_sigue_cerrando_despues_de_redondear():
    """El SII cuadra el libro sumando: si el total queda a un peso de la suma de
    sus partes, el libro sale descuadrado."""
    linea = book_service._book_line(
        BookLineIn(
            **_linea(
                doc_type=33,
                net_amount="10.005",
                vat_amount="1.901",
                total_amount="11.906",
                currency="DOLAR USA",
                exchange_rate=1,
            )
        )
    )
    assert linea.net_amount + linea.vat_amount == linea.total_amount


def test_sin_moneda_los_montos_siguen_siendo_pesos_enteros():
    linea = book_service._book_line(
        BookLineIn(**_linea(doc_type=33, net_amount=1000, vat_amount=190, total_amount=1190))
    )
    assert (linea.net_amount, linea.vat_amount, linea.total_amount) == (1000, 190, 1190)


def test_decimales_sin_moneda_se_rechazan():
    """Es justo el error que se busca evitar: emitir '15.40' en un campo que el
    XSD quiere entero."""
    with pytest.raises(ValidationError, match="decimales"):
        BookLineIn(**_linea(exempt_amount="15.40", total_amount="15.40"))


def test_moneda_sin_tipo_de_cambio_se_rechaza():
    with pytest.raises(ValidationError, match="tipo de cambio"):
        BookLineIn(**_linea(exempt_amount=15, total_amount=15, currency="DOLAR USA"))


def test_tipo_de_cambio_sin_moneda_se_rechaza():
    with pytest.raises(ValidationError, match="de qué moneda"):
        BookLineIn(**_linea(exempt_amount=15, total_amount=15, exchange_rate="950.25"))


def test_el_tipo_de_cambio_debe_ser_positivo():
    with pytest.raises(ValidationError):
        BookLineIn(**_linea(currency="DOLAR USA", exchange_rate=0))
