"""Captura del expediente de certificación.

Lo que se fija aquí es que no se vuelva a perder lo que se perdió: el TrackID,
el sobre enviado y qué venía dentro.
"""

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
