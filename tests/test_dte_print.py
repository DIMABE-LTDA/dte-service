"""Representación impresa: `POST /dte/print` sobre un EnvioDTE ya emitido.

El servicio no guarda los DTE, así que el emisor devuelve el sobre que le
entregó la emisión y recibe el impreso (ejemplar tributario y cedible).
"""

import base64
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dte_chile.caf import load_caf_bytes
from dte_chile.certificate import Certificate
from dte_chile.document_types import DispatchType, DTEType, ReferenceCode, TransferType
from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.models import (
    DTE,
    Driver,
    GlobalDiscount,
    Issuer,
    Item,
    Receiver,
    Reference,
    Transport,
)
from dte_chile.signer import sign_document
from dte_chile.xml_builder import build_document
from lxml import etree

from app.security.service_codes import SERVICE_DTE
from tests.conftest import grant, headers, make_customer

TS = dt.datetime(2026, 8, 28, 10, 0, 0)
ISSUE_DATE = dt.date(2026, 11, 3)


@pytest.fixture(scope="module")
def signing_cert() -> Certificate:
    """Certificado self-signed: sólo para firmar el sobre que se va a imprimir."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Cert")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(dt.datetime(2020, 1, 1))
        .not_valid_after(dt.datetime(2035, 1, 1))
        .sign(key, hashes.SHA256())
    )
    return Certificate(
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        cert_pem=certificate.public_bytes(serialization.Encoding.PEM),
        rut="76158145-7",
    )


@pytest.fixture(scope="module")
def real_caf():
    """CAF con llave RSA real: el TED debe firmarse de verdad para timbrar."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()

    def _make(doc_type: int):
        root = etree.Element("AUTORIZACION")
        caf = etree.SubElement(root, "CAF", version="1.0")
        da = etree.SubElement(caf, "DA")
        for tag, value in (("RE", "76158145-7"), ("RS", "DEMO"), ("TD", str(doc_type))):
            etree.SubElement(da, tag).text = value
        rng = etree.SubElement(da, "RNG")
        etree.SubElement(rng, "D").text = "1"
        etree.SubElement(rng, "H").text = "50"
        etree.SubElement(da, "FA").text = "2026-01-01"
        pk = etree.SubElement(da, "RSAPK")
        etree.SubElement(pk, "M").text = "eA=="
        etree.SubElement(pk, "E").text = "Aw=="
        etree.SubElement(da, "IDK").text = "100"
        etree.SubElement(caf, "FRMA", algoritmo="SHA1withRSA").text = "eA=="
        etree.SubElement(root, "RSASK").text = pem
        return load_caf_bytes(etree.tostring(root))

    return _make


def _issuer():
    return Issuer(
        rut="76158145-7",
        business_name="DEMO SPA",
        activity="Venta al por menor de maquinaria",
        economic_activity=471000,
        address="Calle 1",
        commune="Santiago",
        city="Santiago",
    )


def _receiver(rut="77073851-2"):
    return Receiver(
        rut=rut,
        business_name="CLIENTE SPA",
        activity="Compra",
        address="Calle 2",
        commune="Santiago",
    )


def _invoice():
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=4,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[
            Item("ITEM 1 AFECTO", quantity=360, unit_price=5226, discount_pct=9),
            Item("ITEM 3 SERVICIO EXENTO", quantity=2, unit_price=6822, exempt=True),
        ],
        global_discounts=[GlobalDiscount(value=20, reason="DESCUENTO GLOBAL ITEMES AFECTOS")],
    )


def _credit_note():
    return DTE(
        type=DTEType.CREDIT_NOTE,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("ANULA", quantity=1, unit_price=0)],
        references=[
            Reference(
                doc_type=33,
                folio="4",
                date=ISSUE_DATE,
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA FACTURA",
            )
        ],
    )


def _internal_guide():
    return DTE(
        type=DTEType.DISPATCH_NOTE,
        folio=1,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(rut="76158145-7"),
        items=[Item("ITEM 1", quantity=74, unit_price=0)],
        dispatch_type=DispatchType.ISSUER_TO_OTHER,
        transfer_type=TransferType.INTERNAL,
        transport=Transport(
            plate="ABCD12",
            carrier_rut="76158145-7",
            driver=Driver(rut="77073851-2", name="Juan Perez Soto"),
            dest_address="Bodega 2",
            dest_commune="Santiago",
            departure_date=ISSUE_DATE,
            departure_time=dt.time(8, 30, 0),
            arrival_date=ISSUE_DATE,
        ),
    )


def _envelope_b64(documents, cert, real_caf):
    signed = [sign_document(build_document(d, real_caf(int(d.type)), TS), cert) for d in documents]
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="76158145-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(int(d.type), 1) for d in documents],
    )
    xml = serialize_envelope(build_envelope(signed, cover, cert, TS))
    return base64.b64encode(xml).decode("ascii")


def _setup(db):
    customer = make_customer(db)
    grant(db, customer, SERVICE_DTE)
    return customer


def _html(body, index=0):
    return base64.b64decode(body["documents"][index]["html_base64"]).decode("utf-8")


def test_print_returns_both_copies(client, db, signing_cert, real_caf):
    _setup(db)
    payload = {"xml_base64": _envelope_b64([_invoice()], signing_cert, real_caf)}
    r = client.post("/dte/print", json=payload, headers=headers())
    assert r.status_code == 200, r.text

    body = r.json()
    assert len(body["documents"]) == 1
    assert body["documents"][0] == {
        "index": 1,
        "type": 33,
        "folio": 4,
        "cedible": True,
        "html_base64": body["documents"][0]["html_base64"],
    }

    html = _html(body)
    assert "TRIBUTARIO" in html
    assert "CEDIBLE" in html
    assert "data:image/png;base64," in html  # timbre PDF417 embebido


def test_printed_invoice_shows_discounts_and_totals(client, db, signing_cert, real_caf):
    _setup(db)
    payload = {"xml_base64": _envelope_b64([_invoice()], signing_cert, real_caf)}
    html = _html(client.post("/dte/print", json=payload, headers=headers()).json())

    assert "9% ($169.322)" in html  # descuento de línea
    assert "Descuento global 20% — DESCUENTO GLOBAL ITEMES AFECTOS" in html
    assert "EXENTO" in html


def test_print_every_document_of_a_batch_envelope(client, db, signing_cert, real_caf):
    _setup(db)
    payload = {"xml_base64": _envelope_b64([_invoice(), _credit_note()], signing_cert, real_caf)}
    body = client.post("/dte/print", json=payload, headers=headers()).json()

    assert [(d["type"], d["folio"]) for d in body["documents"]] == [(33, 4), (61, 1)]
    # La nota de crédito no es cedible; la factura sí.
    assert [d["cedible"] for d in body["documents"]] == [True, False]
    assert "Factura Electrónica N° 4" in _html(body, 1)  # la referencia de la nota


def test_internal_transfer_guide_has_no_transferable_copy(client, db, signing_cert, real_caf):
    _setup(db)
    payload = {"xml_base64": _envelope_b64([_internal_guide()], signing_cert, real_caf)}
    body = client.post("/dte/print", json=payload, headers=headers()).json()

    assert body["documents"][0]["cedible"] is False
    html = _html(body)
    assert "CEDIBLE" not in html
    assert "Traslados internos" in html
    assert "ABCD12" in html  # datos de transporte de la Res. 154
    assert "Juan Perez Soto" in html


def test_can_ask_for_a_single_copy(client, db, signing_cert, real_caf):
    _setup(db)
    envelope = _envelope_b64([_invoice()], signing_cert, real_caf)

    tax = _html(
        client.post(
            "/dte/print", json={"xml_base64": envelope, "copies": "tax"}, headers=headers()
        ).json()
    )
    assert "TRIBUTARIO" in tax
    assert "CEDIBLE" not in tax

    transferable = _html(
        client.post(
            "/dte/print",
            json={"xml_base64": envelope, "copies": "transferable"},
            headers=headers(),
        ).json()
    )
    assert "CEDIBLE" in transferable
    assert "Ley 19.983" in transferable


def test_sii_office_is_configurable(client, db, signing_cert, real_caf):
    _setup(db)
    payload = {
        "xml_base64": _envelope_b64([_invoice()], signing_cert, real_caf),
        "sii_office": "MAIPU",
    }
    html = _html(client.post("/dte/print", json=payload, headers=headers()).json())
    assert "S.I.I. — MAIPU" in html


def test_invalid_base64_is_a_400(client, db):
    _setup(db)
    r = client.post("/dte/print", json={"xml_base64": "no-es-base64!!"}, headers=headers())
    assert r.status_code == 400, r.text
    assert "base64" in r.json()["error"]["message"]


def test_xml_without_documents_is_a_400(client, db):
    _setup(db)
    payload = {"xml_base64": base64.b64encode(b"<EnvioDTE/>").decode()}
    r = client.post("/dte/print", json=payload, headers=headers())
    assert r.status_code == 400, r.text
    assert "no se pudo leer el sobre" in r.json()["error"]["message"].lower()


def test_malformed_xml_is_a_400(client, db):
    _setup(db)
    payload = {"xml_base64": base64.b64encode(b"<EnvioDTE").decode()}
    r = client.post("/dte/print", json=payload, headers=headers())
    assert r.status_code == 400, r.text
