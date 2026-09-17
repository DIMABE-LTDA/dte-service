"""Paso 4: las respuestas al set de intercambio que entrega el SII.

Se usa el archivo real que bajó CONSTRUCTORA DIMABE SPA el 17-09-2026: dos
facturas de 88888888-8, y la segunda (folio 52299) dirigida a otro receptor. Lo
que se prueba es lo que el SII revisa: que esa se informe no recibida y se
rechace, que el recibo de mercaderías sea sólo de la otra, y que los tres
archivos validen contra su XSD con sus firmas.
"""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dte_chile import signer
from dte_chile.certificate import Certificate
from lxml import etree

from app.services import certificate_service, customer_service
from tests.conftest import auth_header, make_customer, make_user

SET = Path(__file__).resolve().parent / "fixtures/sii/set_intercambio_77262159-0.xml"
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"

pytestmark = pytest.mark.skipif(
    not (SCHEMAS / "response" / "RespuestaEnvioDTE_v10.xsd").exists(),
    reason="XSD de respuesta no presentes",
)


def _certificado() -> Certificate:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ENSAYO")])
    ahora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=30))
        .sign(clave, hashes.SHA256())
    )
    return Certificate(
        private_key_pem=clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        rut="12291733-9",
    )


@pytest.fixture
def respuesta(client, db, monkeypatch):
    customer = make_customer(db, rut="77262159-0")
    customer_service.set_issuer(
        customer,
        {
            "legal_name": "CONSTRUCTORA DIMABE SPA",
            "activity": "OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
            "economic_activity": 439000,
            "address": "MONS. FDO. DE BARRIONUEVO #1540",
            "commune": "RANCAGUA",
            "city": "RANCAGUA",
        },
    )
    db.commit()
    cert = _certificado()
    monkeypatch.setattr(certificate_service, "resolve_certificate", lambda db, c: cert)
    make_user(db, "op@dimabe.cl", "secret", "operator")
    r = client.post(
        f"/admin/customers/{customer.id}/certification/exchange",
        json={
            "file_name": SET.name,
            "envelope_base64": base64.b64encode(SET.read_bytes()).decode(),
        },
        headers=auth_header(client, "op@dimabe.cl", "secret"),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    body["xml"] = {f["kind"]: base64.b64decode(f["xml_base64"]) for f in body["files"]}
    return body


def test_distingue_la_factura_de_otro_receptor(respuesta):
    assert respuesta["issuer_rut"] == "88888888-8"
    assert [(d["folio"], d["received"]) for d in respuesta["documents"]] == [
        (52298, True),
        (52299, False),
    ]


def test_entrega_las_tres_respuestas_firmadas(respuesta):
    assert set(respuesta["xml"]) == {"acuse_recibo", "recibo_mercaderias", "resultado_comercial"}
    for kind, xml in respuesta["xml"].items():
        firmas = signer.verify_signatures(etree.fromstring(xml))
        assert firmas and all(firmas), kind


def test_acuse_informa_no_recibida_la_factura_ajena(respuesta):
    raiz = etree.fromstring(respuesta["xml"]["acuse_recibo"])
    assert raiz.findtext(".//{*}RutResponde") == "77262159-0"
    assert raiz.findtext(".//{*}EstadoRecepEnv") == "0"
    estados = {
        n.findtext("{*}Folio"): n.findtext("{*}EstadoRecepDTE")
        for n in raiz.iter("{*}RecepcionDTE")
    }
    assert estados == {"52298": "0", "52299": "3"}


def test_recibo_solo_de_la_factura_propia(respuesta):
    raiz = etree.fromstring(respuesta["xml"]["recibo_mercaderias"])
    assert [n.findtext("{*}Folio") for n in raiz.iter("{*}DocumentoRecibo")] == ["52298"]
    assert raiz.findtext(".//{*}Recinto") == "MONS. FDO. DE BARRIONUEVO #1540, RANCAGUA"


def test_resultado_acepta_una_y_rechaza_la_otra(respuesta):
    raiz = etree.fromstring(respuesta["xml"]["resultado_comercial"])
    estados = {
        n.findtext("{*}Folio"): n.findtext("{*}EstadoDTE") for n in raiz.iter("{*}ResultadoDTE")
    }
    assert estados == {"52298": "0", "52299": "2"}


def test_un_archivo_que_no_es_un_envio_se_rechaza_con_422(client, db, monkeypatch):
    customer = make_customer(db, rut="77262159-0")
    monkeypatch.setattr(certificate_service, "resolve_certificate", lambda db, c: _certificado())
    make_user(db, "op@dimabe.cl", "secret", "operator")
    r = client.post(
        f"/admin/customers/{customer.id}/certification/exchange",
        json={"envelope_base64": base64.b64encode(b"<no-es-un-envio/>").decode()},
        headers=auth_header(client, "op@dimabe.cl", "secret"),
    )
    assert r.status_code == 422
