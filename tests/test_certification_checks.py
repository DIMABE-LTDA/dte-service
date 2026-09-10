"""Verificación de configuración: que atrape lo que costó una semana descubrir.

Cada test reproduce un fallo real de la primera certificación que pasó por la
plataforma. Todos se descubrieron a mano, leyendo XML dentro de un contenedor;
con otro contribuyente no va a haber nadie haciendo eso.
"""

import base64
import datetime as dt
import os
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core import crypto
from app.db.models import (
    Caf,
    CertificationDefinition,
    CertificationSet,
    CertificationSubmission,
    CustomerCertificate,
    FolioPointer,
    SiiEnvironment,
)
from app.services import certificate_service, certification_checks
from tests.conftest import auth_header, fake_caf_xml, make_customer, make_user

RUT = "76158145-7"  # el de make_customer


def _caf_real(doc_type, rut=RUT, desde=1, hasta=50, fa="2026-01-01", clave_publica=None):
    """Un CAF con par de claves coherente y una FRMA del largo de una del SII.

    `clave_publica` permite poner en RSAPK la de OTRA clave, para el caso de un
    archivo cuyo par no cuadra.
    """
    privada = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    publica = (clave_publica or privada.public_key()).public_numbers()
    m = base64.b64encode(publica.n.to_bytes((publica.n.bit_length() + 7) // 8, "big")).decode()
    e = base64.b64encode(publica.e.to_bytes(3, "big")).decode()
    pem = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    frma = base64.b64encode(os.urandom(64)).decode()
    return (
        f'<AUTORIZACION><CAF version="1.0"><DA><RE>{rut}</RE><RS>DEMO</RS>'
        f"<TD>{doc_type}</TD><RNG><D>{desde}</D><H>{hasta}</H></RNG><FA>{fa}</FA>"
        f"<RSAPK><M>{m}</M><E>{e}</E></RSAPK><IDK>100</IDK></DA>"
        f'<FRMA algoritmo="SHA1withRSA">{frma}</FRMA></CAF><RSASK>{pem}</RSASK>'
        f"</AUTORIZACION>"
    ).encode()


def _cargar_caf(db, customer, xml, doc_type, desde=1, hasta=50):
    """Directo a la tabla: `add_caf` rechazaría algunos de estos a propósito."""
    db.add(
        Caf(
            customer_id=customer.id,
            doc_type=doc_type,
            folio_from=desde,
            folio_to=hasta,
            xml_encrypted=crypto.encrypt(xml),
        )
    )
    if db.get(FolioPointer, (customer.id, doc_type)) is None:
        db.add(FolioPointer(customer_id=customer.id, doc_type=doc_type, last_folio=0))
    db.commit()


def _definir(db, customer, kind, code, payload, endpoint="issue-batch"):
    s = CertificationSet(customer_id=customer.id, code=code, kind=kind)
    db.add(s)
    db.flush()
    db.add(CertificationDefinition(set_id=s.id, endpoint=endpoint, payload=payload))
    db.commit()
    return s


def _check(resultado, key):
    for g in resultado["groups"]:
        for c in g["checks"]:
            if c["key"] == key:
                return c
    raise AssertionError(f"no hay comprobación {key}")


def _cert_x509(autofirmado: bool) -> bytes:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRUEBA 12291733-9")])
    emisor = (
        sujeto
        if autofirmado
        else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "E-CERTCHILE CA FES 02")])
    )
    ahora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(emisor)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora)
        .not_valid_after(ahora + dt.timedelta(days=900))
        .sign(clave, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _con_certificado(db, customer, monkeypatch, autofirmado):
    fila = db.query(CustomerCertificate).filter_by(customer_id=customer.id).one()
    fila.rut, fila.holder = "12291733-9", "ARTURO LENIN MUNOZ VERGARA"
    fila.issuer = "PRUEBA" if autofirmado else "E-CERTCHILE CA FES 02"
    fila.thumbprint = "huella-actual"
    db.commit()
    pem = _cert_x509(autofirmado)
    monkeypatch.setattr(
        certificate_service,
        "resolve_certificate",
        lambda db, c: SimpleNamespace(cert_pem=pem, rut="12291733-9"),
    )


# --------------------------------------------------------------------------- #
#  Certificado
# --------------------------------------------------------------------------- #


def test_detecta_un_certificado_autofirmado(db, monkeypatch):
    """Firma bien y no autentica: volvía «rechazó la semilla firmada (estado=10)»."""
    customer = make_customer(db)
    _con_certificado(db, customer, monkeypatch, autofirmado=True)

    c = _check(certification_checks.run(db, customer), "certificado")
    assert c["state"] == "error"
    assert "AUTOFIRMADO" in c["detail"]
    assert "entidad acreditada" in c["fix"]


def test_un_certificado_acreditado_pasa(db, monkeypatch):
    customer = make_customer(db)
    _con_certificado(db, customer, monkeypatch, autofirmado=False)

    c = _check(certification_checks.run(db, customer), "certificado")
    assert c["state"] == "ok"
    assert "12291733-9" in c["detail"]


# --------------------------------------------------------------------------- #
#  CAF
# --------------------------------------------------------------------------- #


def test_detecta_un_caf_que_no_es_del_sii(db):
    """Un CAF sin la firma del Servicio: los de prueba traían una FRMA de un byte."""
    customer = make_customer(db)
    _cargar_caf(db, customer, fake_caf_xml(33, 1, 50), 33)

    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "error"
    assert "no trae la firma del SII" in c["detail"]


def test_detecta_un_caf_cuyo_par_de_claves_no_cuadra(db):
    customer = make_customer(db)
    otra = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
    _cargar_caf(db, customer, _caf_real(33, clave_publica=otra), 33)

    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "error"
    assert "clave privada no corresponde" in c["detail"]


def test_detecta_un_caf_de_otro_rut(db):
    """`add_caf` ya lo rechaza al subir; esto cubre lo cargado antes de esa regla."""
    customer = make_customer(db)
    _cargar_caf(db, customer, _caf_real(33, rut="77262159-0"), 33)

    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "error"
    assert "otro RUT" in c["detail"]


def test_avisa_cuando_los_folios_no_alcanzan(db):
    """Con el puntero fuera del rango, el CAF real 1-50 no servía: el siguiente
    folio era el 205."""
    customer = make_customer(db)
    _cargar_caf(db, customer, _caf_real(33, desde=1, hasta=5), 33, desde=1, hasta=5)
    doc = {"type": 33, "issuer": {"rut": RUT}, "issue_date": "2026-02-01"}
    _definir(db, customer, "basico", "5038170", {"documents": [doc] * 4})

    db.get(FolioPointer, (customer.id, 33)).last_folio = 3
    db.commit()
    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "error"
    assert "quedan 2" in c["detail"]

    db.get(FolioPointer, (customer.id, 33)).last_folio = 0
    db.commit()
    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "atencion"  # 5 alcanzan para un intento de 4, no para dos


# --------------------------------------------------------------------------- #
#  Definiciones
# --------------------------------------------------------------------------- #


def test_detecta_una_definicion_clonada_con_el_emisor_de_otro(db):
    """Clonar de un cliente ya certificado trae su emisor: emitiría como él."""
    customer = make_customer(db)
    doc = {"type": 33, "issuer": {"rut": "77262159-0"}, "issue_date": "2026-02-01"}
    _definir(db, customer, "basico", "5038170", {"documents": [doc]})

    c = _check(certification_checks.run(db, customer), "def_basico")
    assert c["state"] == "error"
    assert "emite como 77262159-0" in c["detail"]


def test_detecta_una_fecha_anterior_al_caf(db):
    """El SII rechaza un documento fechado antes de la autorización de su CAF."""
    customer = make_customer(db)
    _cargar_caf(db, customer, _caf_real(33, fa="2026-03-01"), 33)
    doc = {"type": 33, "issuer": {"rut": RUT}, "issue_date": "2026-02-01"}
    _definir(db, customer, "basico", "5038170", {"documents": [doc]})

    c = _check(certification_checks.run(db, customer), "def_basico")
    assert c["state"] == "error"
    assert "anterior al CAF" in c["detail"]


def test_un_set_sin_definir_se_dice_y_no_se_esconde(db):
    customer = make_customer(db)
    c = _check(certification_checks.run(db, customer), "def_liquidacion")
    assert c["state"] == "error"


# --------------------------------------------------------------------------- #
#  Permiso de envío
# --------------------------------------------------------------------------- #


def _envio(db, customer, estado, huella):
    db.add(
        CertificationSubmission(
            customer_id=customer.id,
            track_id="1",
            sent_at=dt.datetime(2026, 9, 2),
            envelope_kind="EnvioDTE",
            envelope_encrypted=b"",
            sii_state=estado,
            signed_thumbprint=huella,
        )
    )
    db.commit()


def test_un_rfr_con_el_certificado_actual_apunta_al_permiso(db, monkeypatch):
    customer = make_customer(db)
    _con_certificado(db, customer, monkeypatch, autofirmado=False)
    _envio(db, customer, "RFR", "huella-actual")

    c = _check(certification_checks.run(db, customer), "permiso")
    assert c["state"] == "error"
    assert "Enviar Doctos" in c["detail"]


def test_un_rfr_con_otro_certificado_no_culpa_al_permiso(db, monkeypatch):
    """El primer RFR vino de un certificado de prueba, no de un permiso faltante."""
    customer = make_customer(db)
    _con_certificado(db, customer, monkeypatch, autofirmado=False)
    _envio(db, customer, "RFR", "huella-de-otro-certificado")

    c = _check(certification_checks.run(db, customer), "permiso")
    assert c["state"] == "atencion"


def test_un_envio_procesado_prueba_el_permiso(db, monkeypatch):
    customer = make_customer(db)
    _con_certificado(db, customer, monkeypatch, autofirmado=False)
    _envio(db, customer, "EPR", None)

    c = _check(certification_checks.run(db, customer), "permiso")
    assert c["state"] == "ok"


# --------------------------------------------------------------------------- #
#  Prueba de firma
# --------------------------------------------------------------------------- #


def test_el_timbre_se_firma_y_verifica_con_cada_caf(db):
    """La prueba que habría evitado los 32 rechazos: sin gastar un folio."""
    customer = make_customer(db)
    for tipo in (33, 61):
        _cargar_caf(db, customer, _caf_real(tipo), tipo)

    c = _check(certification_checks.run(db, customer), "timbre")
    assert c["state"] == "ok"
    assert "33" in c["detail"] and "61" in c["detail"]
    # Y no se movió ningún puntero: la prueba no gasta folios.
    assert db.get(FolioPointer, (customer.id, 33)).last_folio == 0


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #


def _op(client, db):
    make_user(db, "op@dimabe.cl", "secret", "operator")
    return auth_header(client, "op@dimabe.cl", "secret")


def test_el_endpoint_devuelve_los_grupos(client, db):
    customer = make_customer(db)
    r = client.get(f"/admin/customers/{customer.id}/certification/checks", headers=_op(client, db))
    assert r.status_code == 200
    cuerpo = r.json()
    assert [g["key"] for g in cuerpo["groups"]] == [
        "emisor",
        "certificado",
        "caf",
        "definiciones",
        "prueba",
    ]
    assert cuerpo["ready"] is False  # sin CAF ni definiciones no hay nada que emitir


def test_en_produccion_no_hay_verificacion_de_certificacion(client, db):
    customer = make_customer(db)
    customer.environment = SiiEnvironment.PRODUCTION
    db.commit()
    r = client.get(f"/admin/customers/{customer.id}/certification/checks", headers=_op(client, db))
    assert r.status_code == 400


@pytest.mark.parametrize("numero", [0, 80])
def test_la_resolucion_en_certificacion_espera_cero(db, numero):
    customer = make_customer(db)
    customer.resolution_number = numero
    db.commit()
    c = _check(certification_checks.run(db, customer), "resolucion")
    assert c["state"] == ("ok" if numero == 0 else "atencion")


def test_habria_detectado_el_timbre_que_causo_los_32_rechazos(db, monkeypatch):
    """El fallo real: el motor incrustaba el CAF con sus saltos de línea y
    firmaba el DD con ellos. La firma validaba —el motor firmaba lo que
    enviaba— y el SII igual lo rechazaba, porque firma el DD plano.

    Se simula ese motor y se exige que la verificación lo marque en rojo antes
    del primer envío.
    """
    import dte_chile.ted as ted

    customer = make_customer(db)
    # Un CAF como los del SII: con un salto de línea entre cada etiqueta.
    plano = _caf_real(33).decode()
    con_saltos = plano.replace("><", ">\n<")
    _cargar_caf(db, customer, con_saltos.encode(), 33)

    monkeypatch.setattr(ted, "_flatten", lambda nodo: nodo)  # el motor de antes
    c = _check(certification_checks.run(db, customer), "timbre")
    assert c["state"] == "error"
    assert "espacios entre etiquetas" in c["detail"]
