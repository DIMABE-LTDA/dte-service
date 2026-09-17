"""Verificación de configuración: que atrape lo que costó una semana descubrir.

Cada test reproduce un fallo real de la primera certificación que pasó por la
plataforma. Todos se descubrieron a mano, leyendo XML dentro de un contenedor;
con otro contribuyente no va a haber nadie haciendo eso.
"""

import base64
import datetime as dt
import os
from types import SimpleNamespace

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


def _caf_real(doc_type, rut=RUT, desde=1, hasta=50, fa=None, clave_publica=None):
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
    # Vigente: los CAF de documentos con crédito fiscal vencen a los seis meses.
    fa = fa or dt.date.today().isoformat()
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


def test_avisa_un_caf_vencido_o_de_otro_ambiente(db):
    """El SII rechaza en la recepción los folios de un CAF vencido (Res. 58/2017)."""
    customer = make_customer(db)
    vencido = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    _cargar_caf(db, customer, _caf_real(33, fa=vencido), 33)
    c = _check(certification_checks.run(db, customer), "caf_33")
    assert c["state"] == "error"
    assert "venció" in c["detail"]

    customer.environment = SiiEnvironment.PRODUCTION
    db.commit()
    _cargar_caf(db, customer, _caf_real(61), 61)
    c = _check(certification_checks.run(db, customer), "caf_61")
    assert "es de certificación" in c["detail"]


# --------------------------------------------------------------------------- #
#  Definiciones
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
#  Lo que el sistema necesita para completar cada set
# --------------------------------------------------------------------------- #


def test_sin_datos_del_emisor_no_se_puede_emitir(db):
    """El emisor ya no sale de la definición: si la ficha no lo tiene, falta."""
    customer = make_customer(db)
    c = _check(certification_checks.run(db, customer), "emisor")
    assert c["state"] == "error"
    assert "razón social" in c["detail"]


def test_con_los_datos_del_emisor_completos_pasa(db):
    from app.services import customer_service

    customer = make_customer(db)
    customer_service.set_issuer(
        customer,
        {
            "legal_name": "EMPRESA SPA",
            "activity": "GIRO",
            "economic_activity": 439000,
            "address": "CALLE 1",
            "commune": "RANCAGUA",
        },
    )
    db.commit()
    c = _check(certification_checks.run(db, customer), "emisor")
    assert c["state"] == "ok"
    assert "EMPRESA SPA" in c["detail"]


def test_la_resolucion_por_defecto_se_senala(db):
    """«Indique el número y fecha que está publicado en los datos de su empresa
    en el ambiente de certificación»: la del sistema no es la de nadie."""
    customer = make_customer(db)
    c = _check(certification_checks.run(db, customer), "resolucion")
    assert c["state"] == "atencion"
    assert "valor por defecto" in c["detail"]

    customer.resolution_date = dt.date(2026, 8, 26)
    customer.resolution_number = 0
    db.commit()
    c = _check(certification_checks.run(db, customer), "resolucion")
    assert c["state"] == "ok"
    assert "Maullín" in c["detail"]


def test_sin_receptores_de_prueba_se_avisa(db):
    """«Utilice RUT distintos para las distintas facturas.»"""
    customer = make_customer(db)
    sii = {"rut": "60803000-K", "business_name": "SII"}
    _definir(
        db,
        customer,
        "basico",
        "5038170",
        {"documents": [{"type": 33, "receiver": sii} for _ in range(4)]},
    )
    c = _check(certification_checks.run(db, customer), "receptores")
    assert c["state"] == "atencion"
    assert "Hacen falta 4" in c["detail"]

    customer.cert_receivers = [{"rut": "76086428-5"}, {"rut": "96790240-3"}]
    db.commit()
    c = _check(certification_checks.run(db, customer), "receptores")
    assert "2 de 4" in c["detail"]


def test_el_libro_de_ventas_espera_documentos_aceptados(db):
    customer = make_customer(db)
    _definir(db, customer, "libro_ventas", "5038171", {}, endpoint="books")
    c = _check(certification_checks.run(db, customer), "def_libro_ventas")
    assert c["state"] == "atencion"
    assert "documentos aceptados" in c["detail"]


def _con_certificado_real(db, customer, monkeypatch):
    """Como `_con_certificado`, pero el doble sí puede firmar.

    El otro entrega sólo metadatos: sirve para los chequeos que miran emisor y
    vigencia, no para los que construyen y firman un documento de verdad.
    """
    from dte_chile.certificate import Certificate

    fila = db.query(CustomerCertificate).filter_by(customer_id=customer.id).one()
    fila.rut, fila.holder = "12291733-9", "ARTURO LENIN MUNOZ VERGARA"
    fila.issuer = "E-CERTCHILE CA FES 02"
    fila.thumbprint = "huella-actual"
    db.commit()

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PRUEBA 12291733-9")])
    emisor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "E-CERTCHILE CA FES 02")])
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
    resuelto = Certificate(
        private_key_pem=clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        rut="12291733-9",
    )
    monkeypatch.setattr(certificate_service, "resolve_certificate", lambda db, c: resuelto)


def test_la_prueba_de_firma_valida_como_el_sii_y_no_bloquea(db, monkeypatch):
    """Firmas correctas tienen que dar «ok», no bloquear la emisión.

    Este chequeo llevaba su propia copia de la verificación, y comprobaba TODAS
    las firmas contra el sobre completo. Con eso daba por buenas las que el SII
    rechaza —la de un <DTE> se valida con ese <DTE> aislado— y, al corregir el
    motor, pasó a dar por malas las correctas y bloqueó la emisión. Ahora
    delega en el motor, que sabe qué contexto corresponde a cada firma.
    """
    customer = make_customer(db)
    _con_certificado_real(db, customer, monkeypatch)
    _cargar_caf(db, customer, _caf_real(33), 33)

    c = _check(certification_checks.run(db, customer), "firma")
    assert c["state"] == "ok", c["detail"]
    assert "2 de 2" in c["detail"]  # el <Documento> y el <SetDTE>


# --------------------------------------------------------------------------- #
#  Contenido que el SII rechaza en la revisión del set (16-09-2026)
# --------------------------------------------------------------------------- #
def _claves(db, customer):
    return {
        c["key"]
        for g in certification_checks.run(db, customer)["groups"]
        for c in g["checks"]
        if c["key"].startswith("contenido_")
    }


def test_un_anticipo_de_liquidacion_debe_ir_con_tipo_99(db):
    """«Los Valores de la Linea 1 del Detalle No Cuadran» en el set 5038178."""
    customer = make_customer(db)
    linea = {"name": "NETO ANTICIPO FACTURACION", "amount": 550000, "quantity": 78}
    _definir(
        db,
        customer,
        "liquidacion",
        "5038178",
        {"documents": [{"lines": [{**linea, "liquidated_type": "33"}]}]},
        endpoint="issue-settlement-batch",
    )
    assert "contenido_liquidacion_anticipo" in _claves(db, customer)

    d = db.query(CertificationDefinition).one()
    d.payload = {"documents": [{"lines": [{**linea, "liquidated_type": "99"}]}]}
    db.commit()
    assert "contenido_liquidacion_anticipo" not in _claves(db, customer)


def test_una_linea_de_exportacion_necesita_cantidad_y_precio(db):
    """El set 5038177 traía «VALOR LINEA 14» y se guardó como monto suelto."""
    customer = make_customer(db)
    _definir(
        db,
        customer,
        "exportacion_2",
        "5038177",
        {"documents": [{"items": [{"name": "ASESORIAS", "amount": "14", "surcharge_pct": "10"}]}]},
        endpoint="issue-export-batch",
    )
    assert "contenido_exportacion_2_cantidad" in _claves(db, customer)

    d = db.query(CertificationDefinition).one()
    d.payload = {
        "documents": [
            {
                "items": [
                    {
                        "name": "ASESORIAS",
                        "quantity": "1",
                        "unit_price": "14",
                        "surcharge_pct": "10",
                    }
                ]
            }
        ]
    }
    db.commit()
    assert "contenido_exportacion_2_cantidad" not in _claves(db, customer)


def test_una_factura_de_hoteleria_necesita_el_pasaporte(db):
    """«El Documento Debe Tener 2 Linea(s) de Referencia» en el caso 5038177-3."""
    customer = make_customer(db)
    doc = {
        "service_indicator": 4,
        "items": [{"name": "ALOJAMIENTO HABITACIONES", "quantity": "1", "unit_price": "42"}],
    }
    _definir(
        db,
        customer,
        "exportacion_2",
        "5038177",
        {"documents": [doc]},
        endpoint="issue-export-batch",
    )
    assert "contenido_exportacion_2_pasaporte" in _claves(db, customer)

    d = db.query(CertificationDefinition).one()
    d.payload = {"documents": [{**doc, "references": [{"doc_type": 813, "folio": "C01X00T47"}]}]}
    db.commit()
    assert "contenido_exportacion_2_pasaporte" not in _claves(db, customer)


def test_las_definiciones_del_repo_pasan_las_reglas_de_contenido(db):
    """El archivo del repo es lo que se carga para un contribuyente nuevo: no puede
    traer de vuelta ningún error que el SII ya rechazó."""
    import json
    import pathlib

    ruta = pathlib.Path(__file__).parents[1] / "docs/certificacion/definiciones-77262159-0.json"
    sets = json.loads(ruta.read_text(encoding="utf-8"))
    definiciones = {
        kind: SimpleNamespace(payload=s["payload"])
        for kind, s in sets.items()
        if kind in ("liquidacion", "exportacion_1", "exportacion_2")
    }
    problemas = certification_checks._contenido(definiciones)
    assert problemas == [], [p["detail"] for p in problemas]


def test_la_referencia_de_una_boleta_va_en_codref(db):
    """El set de boletas: «<CodRef> SET · <RazonRef> CASO-1»."""
    customer = make_customer(db)
    receipt = {"type": 39, "items": [{"name": "Arroz", "quantity": 5, "unit_price": 700}]}
    s = CertificationSet(customer_id=customer.id, code="", kind="boletas")
    db.add(s)
    db.flush()
    definicion = CertificationDefinition(
        set_id=s.id,
        endpoint="boletas",
        payload={
            "receipts": [{**receipt, "references": [{"doc_type": "SET", "reason": "CASO-1"}]}]
        },
    )
    db.add(definicion)
    db.commit()
    assert "contenido_boletas_referencia" in _claves(db, customer)

    definicion.payload = {
        "receipts": [{**receipt, "references": [{"code": "SET", "reason": "CASO-1"}]}]
    }
    db.commit()
    assert "contenido_boletas_referencia" not in _claves(db, customer)
