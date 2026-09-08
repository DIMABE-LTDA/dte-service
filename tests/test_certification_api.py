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


def _set(body, code):
    """El set con ese número de atención. El expediente devuelve SIEMPRE los diez
    del catálogo, incluidos los que aún no se dieron de alta."""
    return next(s for s in body["sets"] if s["code"] == code)


# --- lectura ---------------------------------------------------------------


def test_expediente_vacio_muestra_los_sets_que_faltan(client, db):
    """Un expediente que sólo mostrara lo enviado escondería justo lo que hay
    que hacer."""
    c = make_customer(db)
    body = client.get(_base(c.id), headers=_op(client, db)).json()
    assert body["unassigned"] == []
    assert len(body["sets"]) == 10
    assert {s["state"] for s in body["sets"]} == {"sin_dar_de_alta"}
    assert body["progress"] == {
        "sets_total": 10,
        "sets_declared": 0,
        "sets_accepted": 0,
        "sets_pending": 10,
    }
    assert [p["key"] for p in body["steps"]] == [
        "sets",
        "boletas",
        "simulacion",
        "intercambio",
        "impresion",
        "cumplimiento",
    ]


def test_un_envio_sin_set_sale_como_sin_clasificar(client, db):
    """Se guardan aunque no se sepa a qué set pertenecen; hay que poder verlos."""
    c = make_customer(db)
    _con_envio(db, c)
    body = client.get(_base(c.id), headers=_op(client, db)).json()
    assert {s["state"] for s in body["sets"]} == {"sin_dar_de_alta"}
    assert len(body["unassigned"]) == 1
    assert body["unassigned"][0]["track_id"] == "0257259806"


def test_las_cinco_etapas_con_su_semaforo(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038170")
    cert_set = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")

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

    cert_set = _set(client.get(_base(c.id), headers=h).json(), "5038170")
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

    cert_set = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038171")
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
    etapas = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")["stages"]
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
    assert _set(body, "5038170")["kind"] == "basico"


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
    sid = _set(client.get(_base(c.id), headers=h).json(), "5038171")["id"]

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


# --- el trámite completo --------------------------------------------------


def test_dar_de_alta_los_sets_que_asigno_el_sii(client, db):
    """Se copia una vez el número de atención de cada set y el expediente ya
    sabe qué falta."""
    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"{_base(c.id)}/setup",
        json={"codes": {"basico": "5038170", "guias": "5038173", "libro_ventas": ""}},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert _set(body, "5038170")["kind"] == "basico"
    assert _set(body, "5038173")["kind"] == "guias"
    # El que se dejó vacío sigue visible como pendiente, no desaparece.
    libro = next(s for s in body["sets"] if s["kind"] == "libro_ventas")
    assert libro["state"] == "sin_dar_de_alta"
    assert body["progress"]["sets_total"] == 10


def test_los_pasos_del_tramite_se_marcan_a_mano(client, db):
    """Ocurren en el sitio del SII o por correo: el servicio no puede saberlo."""
    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"{_base(c.id)}/steps/impresion",
        json={"done_at": "2026-09-10", "note": "Enviado a sii_dte_impresos@sii.cl"},
        headers=h,
    )
    assert r.status_code == 200
    paso = next(p for p in r.json()["steps"] if p["key"] == "impresion")
    assert paso["state"] == "ok"
    assert paso["done_at"] == "2026-09-10"


def test_el_paso_de_los_sets_no_se_puede_marcar_a_mano(client, db):
    """Lo deduce el sistema. Marcarlo haría mentir al semáforo, que es lo único
    que un semáforo no puede permitirse."""
    c = make_customer(db)
    r = client.post(
        f"{_base(c.id)}/steps/sets", json={"done_at": "2026-09-10"}, headers=_op(client, db)
    )
    assert r.status_code == 400
    assert "no marcable" in r.json()["detail"]


def test_el_avance_del_paso_1_sale_de_los_sets(client, db):
    c = make_customer(db)
    h = _op(client, db)
    # El flujo real: primero se dan de alta los sets con su número de atención,
    # y después los envíos caen en el suyo.
    client.post(f"{_base(c.id)}/setup", json={"codes": {"basico": "5038170"}}, headers=h)
    _con_envio(db, c, code="5038170", estado="EPR")
    sid = _set(client.get(_base(c.id), headers=h).json(), "5038170")["id"]
    client.post(f"{_base(c.id)}/sets/{sid}/declare", json={"declared_at": "2026-09-02"}, headers=h)

    body = client.get(_base(c.id), headers=h).json()
    assert body["progress"]["sets_declared"] == 1
    paso = next(p for p in body["steps"] if p["key"] == "sets")
    assert paso["state"] == "atencion"  # uno declarado, faltan nueve
    assert "1 de 10 declarados" in paso["detail"]


# --- catálogo de causas ----------------------------------------------------


def test_un_rechazo_trae_que_revisar(client, db):
    """Diez envíos se perdieron persiguiendo una firma que estaba bien. Eso no
    puede vivir sólo en un documento que hay que acordarse de leer."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="RFR")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")[
        "submissions"
    ][0]

    assert envio["cause"]["label"] == "Rechazado por error en firma"
    assert "casi nunca es la firma" in envio["cause"]["usually"].lower()
    assert any("Enviar Doctos" in paso for paso in envio["cause"]["check"])
    assert envio["cause"]["ok"] is False


def test_un_aceptado_tambien_avisa_de_lo_que_falta_mirar(client, db):
    """EPR es 'sobre procesado', no 'todo bien': puede traer reparos dentro."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="EPR")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")[
        "submissions"
    ][0]

    assert envio["cause"]["ok"] is True
    assert "reparos" in envio["cause"]["meaning"]


def test_un_codigo_desconocido_no_inventa_guia(client, db):
    """Preferimos no decir nada a decir algo que no sabemos."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="XXX")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")[
        "submissions"
    ][0]
    assert envio["cause"] is None


def test_el_libro_descuadrado_apunta_a_los_campos_cruzados(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038171", estado="LRH")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038171")[
        "submissions"
    ][0]
    assert any("TotOpIVARec" in paso for paso in envio["cause"]["check"])
