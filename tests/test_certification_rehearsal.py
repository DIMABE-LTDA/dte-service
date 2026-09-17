"""De los archivos del SII a los sobres: tiene que salir lo que el SII aprobó.

Recorre el camino completo de un contribuyente nuevo —leer la hoja del set,
cargar los sets, emitir los once— y compara el contenido de cada sobre con el
del envío que el SII aprobó a CONSTRUCTORA DIMABE SPA el 16-09-2026.

Protege todo a la vez: el lector de la hoja, lo que completa el sistema al
emitir y el motor. Cualquier cambio que altere un campo que el SII revisa hace
fallar este test, salvo las diferencias conocidas de ``certification_compare``,
que tienen cada una su regla.

Los CAF son generados aquí: los reales traen la clave privada del contribuyente
y no van al repo. El timbre no es contenido que se compare, así que no cambia
nada.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from dte_chile.certificate import Certificate

from app.core import crypto
from app.db.models import Caf, FolioPointer
from app.services import (
    certification_compare,
    certification_rehearsal,
    certification_service,
    certification_sheet,
    customer_service,
)
from tests.conftest import make_customer

RAIZ = Path(__file__).resolve().parent / "fixtures"
CLIENTE = json.loads((RAIZ / "certificacion/cliente-77262159-0.json").read_text(encoding="utf-8"))
APROBADOS = RAIZ / "certificacion/aprobados"
TIPOS = (33, 34, 39, 43, 46, 52, 56, 61, 110, 111, 112)

pytestmark = pytest.mark.skipif(
    not (certification_rehearsal.SCHEMAS / "dte" / "DTE_v10.xsd").exists(),
    reason="XSD del SII no presentes en schemas/",
)


def _caf(doc_type: int, rut: str) -> bytes:
    privada = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    publica = privada.public_key().public_numbers()
    m = base64.b64encode(publica.n.to_bytes((publica.n.bit_length() + 7) // 8, "big")).decode()
    e = base64.b64encode(publica.e.to_bytes(3, "big")).decode()
    pem = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return (
        f'<AUTORIZACION><CAF version="1.0"><DA><RE>{rut}</RE><RS>ENSAYO</RS>'
        f"<TD>{doc_type}</TD><RNG><D>1</D><H>100</H></RNG><FA>2026-08-26</FA>"
        f"<RSAPK><M>{m}</M><E>{e}</E></RSAPK><IDK>100</IDK></DA>"
        f'<FRMA algoritmo="SHA1withRSA">{base64.b64encode(os.urandom(64)).decode()}</FRMA>'
        f"</CAF><RSASK>{pem}</RSASK></AUTORIZACION>"
    ).encode()


def _certificado(rut: str) -> Certificate:
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"ENSAYO {rut}")])
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
        rut=rut,
    )


def _cliente(db):
    cliente = make_customer(db, rut=CLIENTE["rut"])
    cliente.resolution_number = CLIENTE["resolution_number"]
    cliente.resolution_date = dt.date.fromisoformat(CLIENTE["resolution_date"])
    customer_service.set_issuer(cliente, CLIENTE["issuer"])
    cliente.cert_receivers = CLIENTE["receivers"]
    for tipo in TIPOS:
        db.add(
            Caf(
                customer_id=cliente.id,
                doc_type=tipo,
                folio_from=1,
                folio_to=100,
                xml_encrypted=crypto.encrypt(_caf(tipo, CLIENTE["rut"])),
            )
        )
        db.add(FolioPointer(customer_id=cliente.id, doc_type=tipo, last_folio=0))
    db.commit()
    return cliente


@pytest.fixture
def emitidos(db):
    cliente = _cliente(db)
    hoja = certification_sheet.parse((RAIZ / "sii/set_pruebas_77262159-0.txt").read_bytes())
    hoja.sets.update(
        certification_sheet.parse((RAIZ / "sii/set_boletas_77262159-0.txt").read_bytes()).sets
    )
    certification_rehearsal.cargar(db, cliente, hoja)
    return certification_rehearsal.emitir_todo(db, cliente, _certificado(CLIENTE["signer_rut"]))


def test_se_emiten_los_once_sets_validos(emitidos):
    assert sorted(emitidos) == sorted([*certification_rehearsal.ORDEN, "rcof"])
    problemas = {k: r.error or r.forma for k, r in emitidos.items() if r.error or r.forma}
    assert problemas == {}


def test_el_contenido_es_el_que_aprobo_el_sii(emitidos):
    inesperadas = {}
    for archivo in sorted(APROBADOS.glob("*.json")):
        kind = archivo.stem
        aprobado = [tuple(x) for x in json.loads(archivo.read_text(encoding="utf-8"))["contenido"]]
        difs = certification_compare.comparar(aprobado, emitidos[kind].xml)
        _aceptadas, malas = certification_compare.clasificar(kind, difs)
        if malas:
            inesperadas[kind] = [str(d) for d in malas]
    assert inesperadas == {}


def test_las_diferencias_conocidas_no_esconden_otras():
    """Una regla de diferencia conocida no puede aceptar un cambio de verdad."""
    Dif = certification_compare.Diferencia
    ruta = "/SetDTE[1]/DTE[1]/Liquidacion[1]/Detalle[1]/NmbItem[1]"
    _, malas = certification_compare.clasificar(
        "liquidacion",
        [
            Dif(ruta, "NETO FACTURAS", "NETO FACTURAS ELECTRÓNICAS"),  # otra palabra
            Dif(ruta, "NETO NOTA DE CREDITO 328", "NETO NOTA DE CRÉDITO 329"),  # otra cifra
        ],
    )
    assert len(malas) == 2
    _, malas = certification_compare.clasificar(
        "guias",
        [Dif("/SetDTE[1]/DTE[1]/Documento[1]/Encabezado[1]/Transporte[1]/DirDest[1]", "X", None)],
    )
    assert len(malas) == 1  # un dato inventado sigue siendo obligatorio
    _, malas = certification_compare.clasificar(
        "basico",
        [
            Dif(
                "/SetDTE[1]/DTE[1]/Documento[1]/Detalle[1]/NmbItem[1]",
                "Cajón AFECTO",
                "Cajon AFECTO",
            )
        ],
    )
    assert len(malas) == 1  # las tildes sólo se aceptan en la liquidación


def test_el_rcof_reporta_los_folios_y_montos_del_sobre_de_boletas(emitidos):
    """El correo del SII pide «el Reporte de Consumo de Folios (RCOF) asociado»."""
    from lxml import etree

    ns = {"s": "http://www.sii.cl/SiiDte"}
    boletas = etree.fromstring(emitidos["boletas"].xml)
    rcof = etree.fromstring(emitidos["rcof"].xml)
    assert rcof.tag == "{http://www.sii.cl/SiiDte}ConsumoFolios"

    folios = sorted(
        int(f) for f in boletas.xpath("//s:Documento//s:IdDoc/s:Folio/text()", namespaces=ns)
    )
    total = sum(
        int(t) for t in boletas.xpath("//s:Documento//s:Totales/s:MntTotal/text()", namespaces=ns)
    )
    resumen = rcof.find(".//s:Resumen", ns)

    assert resumen.findtext("s:TipoDocumento", namespaces=ns) == "39"
    assert int(resumen.findtext("s:FoliosEmitidos", namespaces=ns)) == len(folios) == 5
    assert int(resumen.findtext("s:MntTotal", namespaces=ns)) == total
    rango = resumen.find("s:RangoUtilizados", ns)
    assert (
        int(rango.findtext("s:Inicial", namespaces=ns)),
        int(rango.findtext("s:Final", namespaces=ns)),
    ) == (
        folios[0],
        folios[-1],
    )


# --------------------------------------------------------------------------- #
#  Paso 3: simulación
# --------------------------------------------------------------------------- #
def test_la_simulacion_sale_valida_y_sin_referencias_a_casos(db):
    """La definición de la simulación de este contribuyente, emitida de verdad.

    El manual del SII pide «un envío, recibido en el SII sin rechazos ni
    reparos». Aquí se comprueba lo que se puede comprobar sin el SII: esquema,
    firmas y timbres tal como viajan, que ningún documento referencie un caso
    del set y que cada nota apunte a una factura del mismo envío.
    """
    from lxml import etree

    from app.db.models import CertificationSet

    cliente = _cliente(db)
    definicion = json.loads(
        (RAIZ / "certificacion/simulacion-77262159-0.json").read_text(encoding="utf-8")
    )
    certification_rehearsal.cargar(
        db, cliente, certification_sheet.Sheet(sets=definicion["sets"], notas=[])
    )
    cert_set = db.query(CertificationSet).filter_by(customer_id=cliente.id, kind="simulacion").one()

    envio = certification_service.emit(db, cliente, _certificado(CLIENTE["signer_rut"]), cert_set)
    xml = certification_service.envelope(envio)

    assert certification_rehearsal._forma(xml) == []

    ns = {"s": "http://www.sii.cl/SiiDte"}
    raiz = etree.fromstring(xml)
    tipos = raiz.xpath("//s:Documento//s:IdDoc/s:TipoDTE/text()", namespaces=ns)
    assert 10 <= len(tipos) <= 100
    assert set(tipos) == {"33", "52", "56", "61"}
    assert raiz.xpath("//s:Referencia[s:TpoDocRef='SET']", namespaces=ns) == []

    facturas = set(
        raiz.xpath("//s:Documento[.//s:TipoDTE='33']//s:IdDoc/s:Folio/text()", namespaces=ns)
    )
    for nota in raiz.xpath("//s:Documento[.//s:TipoDTE='56' or .//s:TipoDTE='61']", namespaces=ns):
        ref = nota.find(".//s:Referencia", ns)
        assert ref.findtext("s:TpoDocRef", namespaces=ns) == "33"
        assert ref.findtext("s:FolioRef", namespaces=ns) in facturas
