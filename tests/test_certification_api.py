"""Expediente de certificación por API: etapas, semáforo y aislamiento."""

import base64
import datetime as dt

from app.db.models import CertificationSet, CertificationSubmission, SiiEnvironment
from app.services import certification_service
from tests.conftest import auth_header, make_customer, make_user

_SOBRE = (
    b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
    b"<DTE><Documento><Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>19</Folio>"
    b"</IdDoc></Encabezado></Documento></DTE></SetDTE></EnvioDTE>"
)


def _op(client, db):
    make_user(db, "op@dimabe.cl", "secret", "operator")
    return auth_header(client, "op@dimabe.cl", "secret")


def _con_envio(db, customer, code=None, track="0257259806", estado=None):
    certification_service.certification_set_var.set(code)
    certification_service.capture(customer, _SOBRE, track)
    certification_service.certification_set_var.set(None)
    if estado:
        envio = db.query(CertificationSubmission).filter_by(track_id=track).one()
        envio.sii_state = estado
        db.commit()


def _base(cid):
    return f"/admin/customers/{cid}/certification"


# --- lectura ---------------------------------------------------------------


def test_expediente_vacio(client, db):
    c = make_customer(db)
    r = client.get(_base(c.id), headers=_op(client, db))
    assert r.status_code == 200
    assert r.json() == {"customer_id": c.id, "sets": [], "unassigned": []}


def test_un_envio_sin_set_sale_como_sin_clasificar(client, db):
    """Se guardan aunque no se sepa a qué set pertenecen; hay que poder verlos."""
    c = make_customer(db)
    _con_envio(db, c)
    body = client.get(_base(c.id), headers=_op(client, db)).json()
    assert body["sets"] == []
    assert len(body["unassigned"]) == 1
    assert body["unassigned"][0]["track_id"] == "0257259806"


def test_las_cinco_etapas_con_su_semaforo(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038170")
    cert_set = client.get(_base(c.id), headers=_op(client, db)).json()["sets"][0]

    etapas = {e["key"]: e["state"] for e in cert_set["stages"]}
    assert list(etapas) == ["requisitos", "emision", "envio", "estado", "declaracion"]
    # Enviado pero sin consultar al SII: no puede estar verde.
    assert etapas["emision"] == "ok"
    assert etapas["envio"] == "ok"
    assert etapas["estado"] == "pendiente"
    assert etapas["declaracion"] == "pendiente"
    assert cert_set["state"] == "enviado"


def test_enviado_no_es_aceptado_y_aceptado_no_es_declarado(client, db):
    """El verde de la última etapa significa 'no queda nada que hacer'."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="EPR")
    h = _op(client, db)

    cert_set = client.get(_base(c.id), headers=h).json()["sets"][0]
    etapas = {e["key"]: e["state"] for e in cert_set["stages"]}
    assert etapas["estado"] == "ok"
    # Aceptado por el SII, pero falta el trámite manual: ámbar, no verde.
    assert etapas["declaracion"] == "atencion"
    assert cert_set["state"] == "aceptado"

    r = client.post(
        f"{_base(c.id)}/sets/{cert_set['id']}/declare",
        json={"declared_at": "2026-09-02"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["state"] == "declarado"
    assert {e["key"]: e["state"] for e in r.json()["stages"]}["declaracion"] == "ok"


def test_un_rechazo_pinta_rojo_y_no_borra_el_intento(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038171", track="0257260576", estado="LRH")
    _con_envio(db, c, code="5038171", track="0257264862", estado="LOK")

    cert_set = client.get(_base(c.id), headers=_op(client, db)).json()["sets"][0]
    # Los dos intentos siguen ahí: la bitácora es el activo.
    assert [s["track_id"] for s in cert_set["submissions"]] == ["0257260576", "0257264862"]
    # El semáforo mira el último.
    assert {e["key"]: e["state"] for e in cert_set["stages"]}["estado"] == "ok"


def test_sin_certificado_los_requisitos_estan_en_rojo(client, db):
    from app.db.models import CustomerCertificate

    c = make_customer(db)
    db.query(CustomerCertificate).filter_by(customer_id=c.id).delete()
    db.commit()
    _con_envio(db, c, code="5038170")
    etapas = client.get(_base(c.id), headers=_op(client, db)).json()["sets"][0]["stages"]
    requisitos = next(e for e in etapas if e["key"] == "requisitos")
    assert requisitos["state"] == "error"
    assert "certificado" in requisitos["detail"]


# --- acciones --------------------------------------------------------------


def test_asignar_un_envio_suelto_a_su_set(client, db):
    c = make_customer(db)
    _con_envio(db, c)
    h = _op(client, db)
    sid = client.get(_base(c.id), headers=h).json()["unassigned"][0]["id"]

    r = client.post(
        f"{_base(c.id)}/submissions/{sid}/assign",
        json={"code": "5038170", "kind": "basico"},
        headers=h,
    )
    assert r.status_code == 200
    body = client.get(_base(c.id), headers=h).json()
    assert body["unassigned"] == []
    assert body["sets"][0]["code"] == "5038170"
    assert body["sets"][0]["kind"] == "basico"


def test_descargar_el_sobre_devuelve_el_xml_original(client, db):
    """Es lo que alimenta las muestras de impresión del paso 5."""
    c = make_customer(db)
    _con_envio(db, c)
    h = _op(client, db)
    sid = client.get(_base(c.id), headers=h).json()["unassigned"][0]["id"]

    r = client.get(f"{_base(c.id)}/submissions/{sid}/envelope", headers=h)
    assert r.status_code == 200
    assert base64.b64decode(r.json()["xml_base64"]) == _SOBRE
    assert r.json()["filename"] == "EnvioDTE_0257259806.xml"


def test_la_bitacora_guarda_lo_que_se_descarto(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038171")
    h = _op(client, db)
    sid = client.get(_base(c.id), headers=h).json()["sets"][0]["id"]

    r = client.post(
        f"{_base(c.id)}/notes",
        json={"set_id": sid, "text": "No es la firma: xmlsec valida los sobres enviados."},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["author"] == "op@dimabe.cl"
    assert client.get(f"{_base(c.id)}/notes", headers=h).json()[0]["text"].startswith("No es")


# --- límites ---------------------------------------------------------------


def test_un_cliente_de_produccion_no_tiene_expediente(client, db):
    """El módulo existe sólo para certificación; en producción no hay nada que ver."""
    c = make_customer(db, key="prod")
    c.environment = SiiEnvironment.PRODUCTION
    db.commit()
    r = client.get(_base(c.id), headers=_op(client, db))
    assert r.status_code == 400
    assert "CERTIFICATION" in r.json()["detail"]


def test_no_se_puede_leer_el_sobre_de_otro_cliente(client, db):
    """El id de un envío ajeno no debe bastar para leer su sobre."""
    a = make_customer(db, key="a")
    b = make_customer(db, rut="77073851-2", key="b")
    _con_envio(db, a)
    h = _op(client, db)
    ajeno = db.query(CertificationSubmission).one().id

    r = client.get(f"{_base(b.id)}/submissions/{ajeno}/envelope", headers=h)
    assert r.status_code == 404


def test_un_auditor_mira_pero_no_declara(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038170")
    make_user(db, "audit@dimabe.cl", "secret", "auditor")
    h = auth_header(client, "audit@dimabe.cl", "secret")

    assert client.get(_base(c.id), headers=h).status_code == 200
    sid = db.query(CertificationSet).one().id
    r = client.post(
        f"{_base(c.id)}/sets/{sid}/declare", json={"declared_at": "2026-09-02"}, headers=h
    )
    assert r.status_code == 403


def test_declarar_queda_en_la_auditoria(client, db):
    from app.db.models import AdminAudit

    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="EPR")
    h = _op(client, db)
    sid = db.query(CertificationSet).one().id
    client.post(f"{_base(c.id)}/sets/{sid}/declare", json={"declared_at": "2026-09-02"}, headers=h)
    acciones = [a.action for a in db.query(AdminAudit).all()]
    assert "certification.declare" in acciones


def test_consultar_el_estado_guarda_la_respuesta_del_sii(client, db, monkeypatch):
    """El estado lo dice el Servicio; se guarda crudo, no se deduce."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170")
    h = _op(client, db)
    sid = db.query(CertificationSubmission).one().id

    monkeypatch.setattr(
        certification_service,
        "query_status",
        lambda *a, **k: {"state": "EPR", "detail": "Envio Procesado"},
    )
    r = client.post(f"{_base(c.id)}/submissions/{sid}/refresh", headers=h)
    assert r.status_code == 200
    assert r.json()["sii_state"] == "EPR"
    assert r.json()["sii_detail"] == "Envio Procesado"
    assert dt.datetime.fromisoformat(r.json()["checked_at"])
