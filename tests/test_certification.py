"""Captura del expediente de certificación.

Lo que se fija aquí es que no se vuelva a perder lo que se perdió: el TrackID,
el sobre enviado y qué venía dentro.
"""

from types import SimpleNamespace

import pytest
from dte_chile.sii_client import SubmissionResult

from app.core import crypto
from app.db.models import (
    CertificationDocument,
    CertificationSet,
    CertificationSubmission,
    SiiEnvironment,
)
from app.services import certification_service, sii_upload
from tests.conftest import make_customer

_SOBRE = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>'
    b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><SetDTE ID="S">'
    b"<DTE><Documento><Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>19</Folio>"
    b"</IdDoc></Encabezado></Documento></DTE>"
    b"<DTE><Documento><Encabezado><IdDoc><TipoDTE>61</TipoDTE><Folio>25</Folio>"
    b"</IdDoc></Encabezado></Documento></DTE>"
    b"</SetDTE></EnvioDTE>"
)

_LIBRO = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>'
    b'<LibroCompraVenta xmlns="http://www.sii.cl/SiiDte" version="1.0"><EnvioLibro ID="L">'
    b"<Detalle><TpoDoc>33</TpoDoc><NroDoc>19</NroDoc></Detalle>"
    b"</EnvioLibro></LibroCompraVenta>"
)


@pytest.fixture(autouse=True)
def _sin_set():
    """El contextvar es de proceso: aislarlo entre tests."""
    token = certification_service.certification_set_var.set(None)
    yield
    certification_service.certification_set_var.reset(token)


def _enviar(customer, xml=_SOBRE, track="0257259806"):
    certification_service.capture(customer, xml, track)


def test_guarda_el_trackid_el_sobre_y_los_documentos(db):
    """Los tres datos que se perdían."""
    c = make_customer(db)
    _enviar(c)

    envio = db.query(CertificationSubmission).one()
    assert envio.track_id == "0257259806"
    assert envio.customer_id == c.id
    assert envio.envelope_kind == "EnvioDTE"
    # El sobre se guarda entero y cifrado: es lo que alimenta las muestras de
    # impresión y lo que permite reenviar sin volver a quemar folios.
    assert crypto.decrypt(envio.envelope_encrypted) == _SOBRE
    assert _SOBRE not in envio.envelope_encrypted.encode()

    docs = {(d.doc_type, d.folio) for d in db.query(CertificationDocument).all()}
    assert docs == {(33, 19), (61, 25)}


def test_en_produccion_no_captura_nada(db):
    """El servicio no guarda DTE. La excepción es sólo para certificación."""
    c = make_customer(db, key="prod")
    c.environment = SiiEnvironment.PRODUCTION
    db.commit()
    _enviar(c)
    assert db.query(CertificationSubmission).count() == 0


def test_sin_trackid_no_captura(db):
    """Sin TrackID no hubo envío que registrar."""
    _enviar(make_customer(db), track=None)
    assert db.query(CertificationSubmission).count() == 0


def test_registra_las_lineas_de_un_libro(db):
    c = make_customer(db)
    _enviar(c, xml=_LIBRO, track="0257260578")
    envio = db.query(CertificationSubmission).one()
    assert envio.envelope_kind == "LibroCompraVenta"
    assert [(d.doc_type, d.folio) for d in envio.documents] == [(33, 19)]


def test_la_cabecera_asocia_el_envio_a_su_set(db):
    c = make_customer(db)
    certification_service.certification_set_var.set("5038170")
    _enviar(c)

    cert_set = db.query(CertificationSet).one()
    assert cert_set.code == "5038170"
    assert cert_set.customer_id == c.id
    assert db.query(CertificationSubmission).one().set_id == cert_set.id


def test_varios_envios_del_mismo_set_no_se_pisan(db):
    """El Libro de Ventas llevó trece intentos: reintentar no borra el anterior."""
    c = make_customer(db)
    certification_service.certification_set_var.set("5038171")
    for track in ("0257260576", "0257260754", "0257264862"):
        _enviar(c, track=track)

    assert db.query(CertificationSet).count() == 1
    tracks = [s.track_id for s in db.query(CertificationSubmission).all()]
    assert tracks == ["0257260576", "0257260754", "0257264862"]


def test_sin_cabecera_el_envio_se_guarda_igual(db):
    """La captura no puede depender de que alguien recuerde declarar el set."""
    _enviar(make_customer(db))
    envio = db.query(CertificationSubmission).one()
    assert envio.set_id is None
    assert db.query(CertificationSet).count() == 0


def test_un_fallo_al_registrar_no_rompe_la_emision(db, monkeypatch, caplog):
    """Cuando se llama, el folio ya se gastó y el documento ya está en el SII."""
    c = make_customer(db)
    monkeypatch.setattr(
        certification_service.crypto,
        "encrypt",
        lambda *_: 1 / 0,  # noqa: ARG005
    )
    _enviar(c)  # no debe levantar
    assert db.query(CertificationSubmission).count() == 0
    assert "no se pudo registrar" in caplog.text


def test_el_enganche_esta_en_el_unico_punto_de_envio(db, monkeypatch):
    """Todos los envíos —documentos, boletas y libros— pasan por sii_upload."""
    c = make_customer(db)

    class _FakeClient:
        def __init__(self, *a, **k):
            self.session = type("S", (), {"close": lambda self: None})()

        def send_dte(self, xml, issuer, sender):
            return SubmissionResult(track_id="0257259806", status="0", detail="ok")

    monkeypatch.setattr(sii_upload, "SIIClient", _FakeClient)
    cert = type("C", (), {"rut": "12291733-9"})()
    sii_upload.upload(c, cert, _SOBRE, c.rut, 30)

    assert db.query(CertificationSubmission).one().track_id == "0257259806"


def test_un_documento_aceptado_con_reparo_cuenta_como_entregado():
    """El SII lo aceptó y lo registró; el reparo es una observación.

    Contarlo como no entregado dejaba fuera del Libro de Ventas documentos que
    el Servicio sí tiene, que es justo el descuadre que el libro viene a evitar.
    El set de exportación volvió con 1 aceptado y 2 con reparo, ninguno
    rechazado.
    """
    envio = SimpleNamespace(
        sii_state="EPR",
        sii_stats=[
            {"doc_type": 110, "informed": 1, "accepted": 0, "rejected": 0, "flagged": 1},
            {"doc_type": 111, "informed": 1, "accepted": 0, "rejected": 0, "flagged": 1},
            {"doc_type": 112, "informed": 1, "accepted": 1, "rejected": 0, "flagged": 0},
        ],
    )
    assert certification_service.entregado(envio) is True


def test_un_sobre_con_todo_rechazado_no_esta_entregado():
    """EPR describe al sobre, no a su contenido: ésta es la distinción que
    hizo que nadie mirara durante una semana."""
    envio = SimpleNamespace(
        sii_state="EPR",
        sii_stats=[{"doc_type": 33, "informed": 4, "accepted": 0, "rejected": 4, "flagged": 0}],
    )
    assert certification_service.entregado(envio) is False


def test_la_consulta_por_documento_usa_el_monto_del_documento_no_el_de_pesos():
    """El SII compara la tupla contra lo que registró: lo que venía dentro.

    Preguntar por una factura de exportación con su equivalente en pesos
    devuelve DNK —«Datos NO Coinciden»— aunque el documento esté aceptado. Y
    despista, porque DNK parece un reparo y es la consulta mal armada. Pasó con
    el set de exportación: sólo coincidió el documento cuyo total era 0, donde
    da igual la moneda.
    """
    sobre = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><SetDTE ID="SetDoc">
<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><Exportaciones ID="F6T110">
<Encabezado>
  <IdDoc><TipoDTE>110</TipoDTE><Folio>6</Folio><FchEmis>2026-09-14</FchEmis></IdDoc>
  <Receptor><RUTRecep>55555555-5</RUTRecep></Receptor>
  <Totales><TpoMoneda>LIBRA EST</TpoMoneda><MntTotal>160677.62</MntTotal></Totales>
  <OtraMoneda><TpoMoneda>PESO CL</TpoMoneda><TpoCambio>1265</TpoCambio>
    <MntTotOtrMnda>203257189</MntTotOtrMnda></OtraMoneda>
</Encabezado></Exportaciones></DTE>
</SetDTE></EnvioDTE>"""

    filas = certification_service._documentos_para_consultar(sobre)

    assert len(filas) == 1
    fila = filas[0]
    assert fila["doc_type"] == 110
    assert fila["folio"] == 6
    assert fila["rut"] == "55555555-5"
    assert fila["date"] == "2026-09-14"
    # El del documento, truncado: NO los 203.257.189 de OtraMoneda.
    assert fila["total_amount"] == 160677


def test_un_set_con_reparos_no_se_muestra_como_sin_respuesta():
    """Caía en «enviado», que es el estado de un sobre que el SII no contestó.

    Un set con reparos SÍ tiene respuesta: el Servicio lo procesó y anotó
    observaciones. Mostrarlo como sin respuesta hacía pensar que faltaba
    consultar, cuando lo que falta es leer los reparos.
    """
    etapas = [
        {"key": "envio", "state": "ok"},
        {"key": "estado", "state": "atencion"},
        {"key": "declaracion", "state": "atencion"},
    ]
    assert certification_service.set_state(etapas) == "con_reparos"


def test_un_set_con_reparos_cuenta_como_entregado_en_el_avance():
    sets = [{"kind": "basico", "state": "con_reparos"}, {"kind": "exenta", "state": "aceptado"}]
    avance = certification_service.progress(sets)
    assert avance["sets_accepted"] == 2
    assert avance["sets_pending"] == 0
