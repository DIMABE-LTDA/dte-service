"""Referencias (<Referencia>): códigos alfanuméricos (HES/801), IndGlobal, RUTOtr.

Valida contra el XSD real del SII (``Validator``) en vez de sólo comparar
strings: así se detecta cualquier error de orden de campos o de tipo que el
SII rechazaría.
"""

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dte_chile.caf import load_caf_bytes
from dte_chile.certificate import Certificate
from dte_chile.document_types import DTEType, ReferenceCode
from dte_chile.envelope import Cover, build_envelope
from dte_chile.envelope import serialize as serialize_envelope
from dte_chile.models import DTE, Issuer, Item, Receiver, Reference
from dte_chile.signer import sign_document
from dte_chile.validation import Validator
from dte_chile.xml_builder import build_document
from lxml import etree

TS = dt.datetime(2026, 8, 28, 10, 0, 0)
ISSUE_DATE = dt.date(2026, 11, 3)
NS = "http://www.sii.cl/SiiDte"


@pytest.fixture(scope="module")
def signing_cert() -> Certificate:
    """Certificado self-signed: sólo para firmar el sobre que se valida."""
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


def _issuer() -> Issuer:
    return Issuer(
        rut="76158145-7",
        business_name="DEMO SPA",
        activity="Venta al por menor de maquinaria",
        economic_activity=471000,
        address="Calle 1",
        commune="Santiago",
        city="Santiago",
    )


def _receiver() -> Receiver:
    return Receiver(
        rut="77073851-2",
        business_name="CLIENTE SPA",
        activity="Compra",
        address="Calle 2",
        commune="Santiago",
    )


def _invoice_with_references(references: list[Reference]) -> DTE:
    return DTE(
        type=DTEType.AFFECTED_INVOICE,
        folio=10,
        issue_date=ISSUE_DATE,
        issuer=_issuer(),
        receiver=_receiver(),
        items=[Item("ITEM 1", quantity=1, unit_price=1000)],
        references=references,
    )


def _signed_envelope_xml(dte: DTE, cert, real_caf) -> bytes:
    signed = sign_document(build_document(dte, real_caf(int(dte.type)), TS), cert)
    cover = Cover(
        issuer_rut="76158145-7",
        sender_rut="76158145-7",
        resolution_date=dt.date(2026, 1, 1),
        subtotals=[(int(dte.type), 1)],
    )
    return serialize_envelope(build_envelope([signed], cover, cert, TS))


def test_hes_and_801_references_validate_against_the_sii_xsd(signing_cert, real_caf):
    """Odoo manda HES (hoja de entrada de servicios) y 801 (OC) en la misma factura."""
    dte = _invoice_with_references(
        [
            Reference(doc_type="HES", folio="FOLIO-HES-1", date=ISSUE_DATE, reason="HES asociada"),
            Reference(doc_type=801, folio="12345", date=ISSUE_DATE, reason="OC 12345"),
        ]
    )
    xml = _signed_envelope_xml(dte, signing_cert, real_caf)
    Validator("schemas").validate(xml)  # no lanza

    root = etree.fromstring(xml)
    types = root.findall(f".//{{{NS}}}Referencia/{{{NS}}}TpoDocRef")
    assert [t.text for t in types] == ["HES", "801"]


def test_ind_global_and_rut_otr_respect_the_xsd_order(signing_cert, real_caf):
    """IndGlobal y RUTOtr van entre TpoDocRef/FolioRef y FolioRef/FchRef, en ese orden."""
    dte = _invoice_with_references(
        [
            Reference(
                doc_type=52,
                folio="0",
                is_global=True,
                other_rut="76086428-5",
                date=ISSUE_DATE,
                code=ReferenceCode.CANCEL_DOCUMENT,
                reason="ANULA GUIAS",
            )
        ]
    )
    xml = _signed_envelope_xml(dte, signing_cert, real_caf)
    Validator("schemas").validate(xml)  # no lanza: respeta el orden del XSD

    root = etree.fromstring(xml)
    ref = root.find(f".//{{{NS}}}Referencia")
    tags = [child.tag.removeprefix(f"{{{NS}}}") for child in ref]
    assert tags == [
        "NroLinRef",
        "TpoDocRef",
        "IndGlobal",
        "FolioRef",
        "RUTOtr",
        "FchRef",
        "CodRef",
        "RazonRef",
    ]
    assert ref.find(f"{{{NS}}}IndGlobal").text == "1"
    assert ref.find(f"{{{NS}}}FolioRef").text == "0"
    assert ref.find(f"{{{NS}}}RUTOtr").text == "76086428-5"
