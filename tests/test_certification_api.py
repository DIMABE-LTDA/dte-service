"""Expediente de certificación por API: etapas, semáforo y aislamiento."""

import base64
import datetime as dt

import pytest

from app.db.models import CertificationSet, CertificationSubmission, SiiEnvironment
from app.services import book_service, certificate_service, certification_service
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


@pytest.fixture
def fake_book_engine(monkeypatch):
    """El motor real firma con un certificado de verdad; aquí sólo interesa el
    flujo de emitir/enviar."""
    monkeypatch.setattr(book_service, "build_book", lambda cover, cert, ts: "BOOK")
    monkeypatch.setattr(
        book_service,
        "serialize",
        lambda x: b'<LibroCompraVenta xmlns="http://www.sii.cl/SiiDte"><EnvioLibro>'
        b"<Detalle><TpoDoc>33</TpoDoc><NroDoc>19</NroDoc></Detalle></EnvioLibro></LibroCompraVenta>",
    )


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


# --- emisión guiada --------------------------------------------------------


_DEFINICION = {
    "endpoint": "books",
    "payload": {
        "period": "2026-05",
        "operation_type": "VENTA",
        "validate_xsd": False,
        "lines": [
            {
                "doc_type": 33,
                "folio": 19,
                "date": "2026-05-10",
                "rut": "77073851-2",
                "business_name": "CLIENTE",
                "net_amount": 1000,
                "vat_amount": 190,
                "total_amount": 1190,
            }
        ],
    },
}


def _con_set(client, db, h, code="5038171", kind="libro_ventas"):
    client.post(f"{_base(_cid(db))}/setup", json={"codes": {kind: code}}, headers=h)
    return db.query(CertificationSet).filter_by(code=code).one()


def _cid(db):
    from app.db.models import Customer

    return db.query(Customer).order_by(Customer.id).first().id


def test_guardar_y_leer_la_definicion_de_un_set(client, db):
    """El cuerpo se guarda literal: lo que se revisa es lo que se envía."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)

    r = client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)
    assert r.status_code == 200
    leida = client.get(f"{_base(c.id)}/sets/{cs.id}/definition", headers=h).json()
    assert leida["endpoint"] == "books"
    assert leida["payload"]["lines"][0]["folio"] == 19


def test_emitir_no_envia_y_deja_el_sobre_guardado(client, db, fake_book_engine):
    """Emitir y enviar son dos actos: si el SII rechaza, se reenvía el mismo
    sobre sin gastar folios nuevos."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)

    r = client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)
    assert r.status_code == 200, r.text
    envio = r.json()
    assert envio["track_id"] is None  # emitido, sin enviar
    assert envio["sent_at"] is None
    assert db.query(CertificationSubmission).one().envelope_encrypted


def test_no_se_emite_dos_veces_sin_insistir(client, db, fake_book_engine):
    """Emitir quema folios y no se deshace: el doble clic no puede costar un set."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)

    assert client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h).status_code == 200
    r = client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)
    assert r.status_code == 409
    assert "sin enviar" in r.json()["detail"]
    assert db.query(CertificationSubmission).count() == 1

    # Insistiendo sí, y el aviso dice lo que va a pasar.
    r = client.post(f"{_base(c.id)}/sets/{cs.id}/emit?force=true", headers=h)
    assert r.status_code == 200
    assert db.query(CertificationSubmission).count() == 2


def test_sin_definicion_no_se_emite(client, db):
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    r = client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)
    assert r.status_code == 409
    assert "definido qué emitir" in r.json()["detail"]


def test_clonar_la_definicion_de_otro_cliente(client, db):
    """Lo que hace barato el segundo contribuyente."""
    a = make_customer(db, key="a")
    b = make_customer(db, rut="77073851-2", key="b")
    h = _op(client, db)
    client.post(f"{_base(a.id)}/setup", json={"codes": {"libro_ventas": "5038171"}}, headers=h)
    client.post(f"{_base(b.id)}/setup", json={"codes": {"libro_ventas": "6000001"}}, headers=h)
    set_a = db.query(CertificationSet).filter_by(customer_id=a.id).one()
    set_b = db.query(CertificationSet).filter_by(customer_id=b.id).one()
    client.put(f"{_base(a.id)}/sets/{set_a.id}/definition", json=_DEFINICION, headers=h)

    r = client.post(
        f"{_base(b.id)}/sets/{set_b.id}/definition/clone",
        json={"from_customer_id": a.id},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["payload"]["lines"][0]["folio"] == 19


def test_clonar_de_un_cliente_sin_ese_set_avisa(client, db):
    a = make_customer(db, key="a")
    b = make_customer(db, rut="77073851-2", key="b")
    h = _op(client, db)
    client.post(f"{_base(b.id)}/setup", json={"codes": {"libro_ventas": "6000001"}}, headers=h)
    set_b = db.query(CertificationSet).filter_by(customer_id=b.id).one()
    r = client.post(
        f"{_base(b.id)}/sets/{set_b.id}/definition/clone",
        json={"from_customer_id": a.id},
        headers=h,
    )
    assert r.status_code == 404
    assert "libro_ventas" in r.json()["detail"]


def test_un_certificado_ilegible_avisa_en_vez_de_reventar(client, db, monkeypatch):
    """El operador veía 'HTTP 500' sin saber que el problema era el .pfx que él
    mismo cargó."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)

    def _revienta(*a, **k):
        raise ValueError("Could not deserialize PKCS12 data")

    monkeypatch.setattr(certificate_service, "resolve_certificate", _revienta)
    r = client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)
    assert r.status_code == 409
    assert ".pfx" in r.json()["detail"]


def test_un_sobre_sin_enviar_no_pinta_el_envio_en_verde(client, db, fake_book_engine):
    """Los folios ya se gastaron, pero el SII todavía no lo tiene: decir que el
    set está entregado sería mentir."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)
    client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)

    etapas = {
        e["key"]: e for e in _set(client.get(_base(c.id), headers=h).json(), "5038171")["stages"]
    }
    assert etapas["emision"]["state"] == "ok"
    assert etapas["envio"]["state"] == "atencion"
    assert "sin enviar" in etapas["envio"]["detail"]
    assert etapas["estado"]["state"] == "pendiente"
    # Y nunca "TrackID None".
    assert "None" not in etapas["envio"]["detail"]


def test_enviar_un_borrador_le_pone_el_trackid(client, db, fake_book_engine, monkeypatch):
    """Reenviar el mismo sobre no cuesta folios: es la razón de separar emitir
    de enviar."""
    from dte_chile.sii_client import SubmissionResult

    from app.services import sii_upload

    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)
    sid = client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h).json()["id"]

    class _FakeClient:
        def __init__(self, *a, **k):
            self.session = type("S", (), {"close": lambda self: None})()

        def send_dte(self, xml, issuer, sender):
            return SubmissionResult(track_id="0257299999", status="0", detail="ok")

    monkeypatch.setattr(sii_upload, "SIIClient", _FakeClient)
    r = client.post(f"{_base(c.id)}/submissions/{sid}/send", headers=h)
    assert r.status_code == 200
    assert r.json()["track_id"] == "0257299999"
    assert r.json()["sent_at"] is not None
    # No se duplicó la fila: es el mismo sobre, no uno nuevo.
    assert db.query(CertificationSubmission).count() == 1


# --- muestras de impresión (paso 5) ---------------------------------------


def test_las_muestras_salen_de_los_sobres_guardados(client, db, monkeypatch):
    """Sin los sobres esto no se podía hacer: el servicio no almacena DTE y de la
    tanda aceptada se habían perdido seis."""
    from app.services import dte_service

    c = make_customer(db)
    h = _op(client, db)
    _con_envio(db, c, code="5038170", estado="EPR")
    monkeypatch.setattr(
        dte_service,
        "print_documents",
        lambda customer, req: {"documents": [{"type": 33, "folio": 19, "html": "<html/>"}]},
    )

    r = client.post(f"{_base(c.id)}/print-samples", headers=h)
    assert r.status_code == 200
    assert r.json()["documents"][0]["folio"] == 19
    assert r.json()["documents"][0]["track_id"] == "0257259806"


def test_un_libro_no_se_imprime_pero_se_informa(client, db):
    """Un libro es un registro, no un documento que se entregue a nadie. Que se
    salte tiene que verse, no ocurrir en silencio."""
    c = make_customer(db)
    h = _op(client, db)
    certification_service.certification_set_var.set("5038171")
    certification_service.capture(
        c,
        b'<LibroCompraVenta xmlns="http://www.sii.cl/SiiDte"><EnvioLibro>'
        b"<Detalle><TpoDoc>33</TpoDoc><NroDoc>19</NroDoc></Detalle></EnvioLibro></LibroCompraVenta>",
        "0257260578",
    )
    certification_service.certification_set_var.set(None)

    body = client.post(f"{_base(c.id)}/print-samples", headers=h).json()
    assert body["documents"] == []
    assert body["skipped"][0]["reason"] == "LibroCompraVenta"


def test_un_sobre_sin_enviar_no_entra_en_las_muestras(client, db, fake_book_engine):
    """El SII pide los documentos del set de pruebas: un sobre que no se envió
    no es parte del set."""
    c = make_customer(db)
    h = _op(client, db)
    cs = _con_set(client, db, h)
    client.put(f"{_base(c.id)}/sets/{cs.id}/definition", json=_DEFINICION, headers=h)
    client.post(f"{_base(c.id)}/sets/{cs.id}/emit", headers=h)

    body = client.post(f"{_base(c.id)}/print-samples", headers=h).json()
    assert body["documents"] == []
    assert body["skipped"] == []


def test_de_varios_intentos_se_imprime_el_aceptado(client, db, monkeypatch):
    """Imprimir el rechazado junto al bueno confundiría al revisor del SII."""
    from app.services import dte_service

    c = make_customer(db)
    h = _op(client, db)
    _con_envio(db, c, code="5038170", track="0257260576", estado="RFR")
    _con_envio(db, c, code="5038170", track="0257264862", estado="EPR")
    monkeypatch.setattr(
        dte_service,
        "print_documents",
        lambda customer, req: {"documents": [{"type": 33, "folio": 19}]},
    )

    body = client.post(f"{_base(c.id)}/print-samples", headers=h).json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["track_id"] == "0257264862"
