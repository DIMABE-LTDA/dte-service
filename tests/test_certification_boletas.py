"""El set de boletas sale por sus canales: boletas por la API REST, RCOF por Maullín.

El correo del SII pide enviar «el Set de Boletas generado y el Reporte de
Consumo de Folios (RCOF) asociado» en 24 horas. Equivocarse de canal se
descubre con el reloj corriendo, así que se prueba aquí.
"""

from __future__ import annotations

import datetime as dt

import pytest
from dte_chile.sii_client import SubmissionResult

from app.core import crypto
from app.db.models import CertificationSet, CertificationSubmission
from app.services import certification_service, receipt_service
from tests.conftest import make_customer

_BOLETAS = (
    b'<EnvioBOLETA xmlns="http://www.sii.cl/SiiDte" version="1.0"><SetDTE>'
    + b"".join(
        (
            f"<DTE><Documento><Encabezado><IdDoc><TipoDTE>39</TipoDTE><Folio>{f}</Folio>"
            f"<FchEmis>2026-09-17</FchEmis></IdDoc><Totales><MntNeto>{n}</MntNeto>"
            f"<IVA>{i}</IVA><MntTotal>{n + i}</MntTotal></Totales>"
            "</Encabezado></Documento></DTE>"
        ).encode()
        for f, n, i in ((101, 1000, 190), (102, 2000, 380))
    )
    + b"</SetDTE></EnvioBOLETA>"
)


#: El control de firmas tal cual, antes de que el fixture `canales` lo desactive.
_CONTROL_REAL = certification_service._firmas_como_se_transmiten


class _Cliente:
    """Registra por qué canal se llamó."""

    llamadas: list[str] = []

    def __init__(self, *a, **k):
        self.session = type("S", (), {"close": lambda self: None})()

    def send_receipts(self, xml, issuer, sender):
        _Cliente.llamadas.append("api-boleta:envio")
        return SubmissionResult(track_id="123456789012345", status="REC", detail="")

    def submission_status(self, track_id, rut):
        _Cliente.llamadas.append("api-boleta:estado")
        from dte_chile.receipt_client import ReceiptSubmissionStatus

        return ReceiptSubmissionStatus(
            track_id=track_id,
            state="EPR",
            stats=[{"tipo": 39, "informados": 2, "aceptados": 1, "rechazados": 1, "reparos": 0}],
            details=[
                {
                    "tipo": 39,
                    "folio": 102,
                    "estado": "RCH",
                    "error": [{"descripcion": "Valor Detalle Distinto a Precio * Cantidad"}],
                }
            ],
        )


@pytest.fixture
def canales(monkeypatch):
    _Cliente.llamadas = []
    monkeypatch.setattr("dte_chile.receipt_client.ReceiptClient", _Cliente)

    def _maullin(customer, cert, xml, rut, timeout, capture=True):
        _Cliente.llamadas.append("maullin:upload")
        return SubmissionResult(track_id="0259999999", status="0", detail="")

    def _maullin_estado(customer, cert, track_id, timeout):
        _Cliente.llamadas.append("maullin:estado")
        return {"state": "EPR", "detail": "Envio Procesado", "stats": []}

    monkeypatch.setattr("app.services.sii_upload.upload", _maullin)
    monkeypatch.setattr(certification_service, "query_status", _maullin_estado)
    # Los sobres de estas pruebas no van firmados: lo que se prueba es el canal.
    # El control de firmas tiene su propia prueba más abajo.
    monkeypatch.setattr(certification_service, "_firmas_como_se_transmiten", lambda xml: None)
    return _Cliente.llamadas


def _sobre(db, customer, kind, xml, track=None):
    s = db.query(CertificationSet).filter_by(customer_id=customer.id, kind="boletas").one_or_none()
    if s is None:
        s = CertificationSet(customer_id=customer.id, code="", kind="boletas")
        db.add(s)
        db.flush()
    envio = CertificationSubmission(
        set_id=s.id,
        customer_id=customer.id,
        track_id=track,
        sent_at=dt.datetime(2026, 9, 17) if track else None,
        envelope_kind=kind,
        envelope_encrypted=crypto.encrypt(xml),
    )
    db.add(envio)
    db.commit()
    return envio


def _cert():
    from types import SimpleNamespace

    return SimpleNamespace(rut="12291733-9")


def test_las_boletas_se_envian_por_la_api_rest(db, canales):
    customer = make_customer(db, rut="77262159-0")
    envio = _sobre(db, customer, "EnvioBOLETA", _BOLETAS)

    certification_service.send_draft(db, customer, _cert(), envio, 30)

    assert canales == ["api-boleta:envio"]
    assert envio.track_id == "123456789012345"


def test_un_sobre_cuyas_firmas_no_verifican_no_se_envia(db, canales, monkeypatch):
    """El primer set de boletas salió con los <DTE> sin su xmlns.

    Sus firmas verificaban sobre el árbol, pero el SII corta cada <DTE> del
    texto y rechazó las cinco con «Firma DTE Incorrecta». Lo que no verifica
    tal como se transmite no debe salir: el reloj del CAF sigue corriendo.
    """
    monkeypatch.setattr("dte_chile.signer.verify_transmitted", lambda xml: [False, False, True])
    monkeypatch.setattr(
        certification_service,
        "_firmas_como_se_transmiten",
        _CONTROL_REAL,
    )
    customer = make_customer(db, rut="77262159-0")
    envio = _sobre(db, customer, "EnvioBOLETA", _BOLETAS)

    with pytest.raises(certification_service.EmissionError, match="Firma DTE Incorrecta"):
        certification_service.send_draft(db, customer, _cert(), envio, 30)

    assert canales == []
    assert envio.track_id is None


def test_un_sobre_cuyos_timbres_no_verifican_no_se_envia(db, canales, monkeypatch):
    """El segundo set de boletas: firmas bien, timbre firmado con otro <RSR>."""
    monkeypatch.setattr("dte_chile.signer.verify_transmitted", lambda xml: [True])
    monkeypatch.setattr("dte_chile.ted.verify_stamps", lambda xml: [False, True])
    monkeypatch.setattr(certification_service, "_firmas_como_se_transmiten", _CONTROL_REAL)
    customer = make_customer(db, rut="77262159-0")
    envio = _sobre(db, customer, "EnvioBOLETA", _BOLETAS)

    with pytest.raises(certification_service.EmissionError, match="Firma Timbre Electrónico"):
        certification_service.send_draft(db, customer, _cert(), envio, 30)

    assert canales == []
    assert envio.track_id is None


def test_el_rcof_y_los_demas_sobres_se_envian_por_maullin(db, canales):
    customer = make_customer(db, rut="77262159-0")
    rcof = _sobre(db, customer, "ConsumoFolios", b"<ConsumoFolios/>")

    certification_service.send_draft(db, customer, _cert(), rcof, 30)

    assert canales == ["maullin:upload"]


def test_el_estado_de_las_boletas_se_consulta_en_la_api_rest_con_sus_errores(db, canales):
    customer = make_customer(db, rut="77262159-0")
    envio = _sobre(db, customer, "EnvioBOLETA", _BOLETAS, track="123456789012345")

    certification_service.refresh(db, customer, _cert(), envio, 30)

    assert canales == ["api-boleta:estado"]
    assert envio.sii_state == "EPR"
    assert envio.sii_stats == [
        {"doc_type": 39, "informed": 2, "accepted": 1, "rejected": 1, "flagged": 0}
    ]
    assert "39-102 RCH: Valor Detalle Distinto" in envio.sii_detail


def test_el_estado_del_rcof_se_consulta_en_maullin(db, canales):
    customer = make_customer(db, rut="77262159-0")
    rcof = _sobre(db, customer, "ConsumoFolios", b"<ConsumoFolios/>", track="0259999999")

    certification_service.refresh(db, customer, _cert(), rcof, 30)

    assert canales == ["maullin:estado"]


def test_el_rcof_exige_un_envio_de_boletas_ya_enviado(db):
    customer = make_customer(db, rut="77262159-0")
    sin_enviar = _sobre(db, customer, "EnvioBOLETA", _BOLETAS)
    with pytest.raises(certification_service.EmissionError, match="primero envía"):
        certification_service.folio_report(db, customer, _cert(), sin_enviar)

    otro = _sobre(db, customer, "EnvioDTE", b"<EnvioDTE/>", track="1")
    with pytest.raises(certification_service.EmissionError, match="desde un envío de boletas"):
        certification_service.folio_report(db, customer, _cert(), otro)


def test_el_rcof_suelto_tambien_va_por_maullin(client, db, monkeypatch):
    """Fuera del expediente, `/boletas/folio-report` tampoco usa la API de boleta."""
    llamadas = []

    def _maullin(customer, cert, xml, rut, timeout, capture=True):
        llamadas.append("maullin")
        return SubmissionResult(track_id="0259999999", status="0", detail="")

    def _api(*a, **k):
        raise AssertionError("el RCOF no va por la API de boleta")

    monkeypatch.setattr("app.services.sii_upload.upload", _maullin)
    monkeypatch.setattr(receipt_service, "_client", _api)
    monkeypatch.setattr(receipt_service, "build_folio_report", lambda cover, cert, ts: None)
    monkeypatch.setattr(receipt_service, "serialize_report", lambda x: b"<ConsumoFolios/>")

    from app.schemas.receipt import FolioReportRequest

    req = FolioReportRequest(
        start_date="2026-09-17",
        end_date="2026-09-17",
        lines=[{"doc_type": 39, "folio": 101, "total_amount": 1190}],
        validate_xsd=False,
    )
    customer = make_customer(db, rut="77262159-0")
    receipt_service.send_folio_report(customer, _cert(), req)
    assert llamadas == ["maullin"]
