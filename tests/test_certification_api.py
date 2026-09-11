"""Expediente de certificación por API: etapas, semáforo y aislamiento."""

import base64
import datetime as dt

import pytest

from app.db.models import CertificationSet, CertificationSubmission, SiiEnvironment
from app.services import (
    book_service,
    certificate_service,
    certification_preview,
    certification_service,
)
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
        lambda x: (
            b'<LibroCompraVenta xmlns="http://www.sii.cl/SiiDte"><EnvioLibro>'
            b"<Detalle><TpoDoc>33</TpoDoc><NroDoc>19</NroDoc></Detalle></EnvioLibro></LibroCompraVenta>"
        ),
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
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")["submissions"][
        0
    ]

    assert envio["cause"]["label"] == "Rechazado por error en firma"
    assert "casi nunca es la firma" in envio["cause"]["usually"].lower()
    assert any("Enviar Doctos" in paso for paso in envio["cause"]["check"])
    assert envio["cause"]["ok"] is False


def test_un_aceptado_tambien_avisa_de_lo_que_falta_mirar(client, db):
    """EPR es 'sobre procesado', no 'todo bien': puede traer reparos dentro."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="EPR")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")["submissions"][
        0
    ]

    assert envio["cause"]["ok"] is True
    assert "reparos" in envio["cause"]["meaning"]


def test_un_codigo_desconocido_no_inventa_guia(client, db):
    """Preferimos no decir nada a decir algo que no sabemos."""
    c = make_customer(db)
    _con_envio(db, c, code="5038170", estado="XXX")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038170")["submissions"][
        0
    ]
    assert envio["cause"] is None


def test_el_libro_descuadrado_apunta_a_los_campos_cruzados(client, db):
    c = make_customer(db)
    _con_envio(db, c, code="5038171", estado="LRH")
    envio = _set(client.get(_base(c.id), headers=_op(client, db)).json(), "5038171")["submissions"][
        0
    ]
    assert any("TotOpIVARec" in paso for paso in envio["cause"]["check"])


# --- emisión guiada --------------------------------------------------------


_DEFINICION = {
    "endpoint": "books",
    "payload": {
        "period": "2026-05",
        "operation_type": "COMPRA",
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


def _con_set(client, db, h, code="5038172", kind="libro_compras"):
    # Libro de compras y no de ventas: sus líneas son dato del caso —las entrega
    # el SII en el set— y se guardan tal cual. Las del de ventas las genera el
    # sistema con los documentos aceptados, y no sirven para probar el flujo
    # guardar → emitir → enviar con un cuerpo conocido.
    client.post(f"{_base(_cid(db))}/setup", json={"codes": {kind: code}}, headers=h)
    return db.query(CertificationSet).filter_by(code=code).one()


def _cid(db):
    from app.db.models import Customer

    return db.query(Customer).order_by(Customer.id).first().id


def test_guardar_y_leer_la_definicion_de_un_set(client, db):
    """Los datos del caso se guardan literales: lo que se revisa es lo que se envía."""
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
    client.post(f"{_base(a.id)}/setup", json={"codes": {"libro_compras": "5038172"}}, headers=h)
    client.post(f"{_base(b.id)}/setup", json={"codes": {"libro_compras": "6000002"}}, headers=h)
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
        e["key"]: e for e in _set(client.get(_base(c.id), headers=h).json(), "5038172")["stages"]
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


# --- ver qué se emite ------------------------------------------------------


def test_la_vista_previa_se_entiende_sin_abrir_el_json(client, db):
    """Emitir consume folios: revisar tiene que ser posible sin leer XML."""
    c = make_customer(db)
    h = _op(client, db)
    client.post(f"{_base(c.id)}/setup", json={"codes": {"basico": "5038170"}}, headers=h)
    cs = db.query(CertificationSet).filter_by(code="5038170").one()
    client.put(
        f"{_base(c.id)}/sets/{cs.id}/definition",
        json={
            "endpoint": "issue-batch",
            "payload": {
                "documents": [
                    {
                        "type": 33,
                        "issue_date": "2026-09-02",
                        "issuer": {"rut": "76158145-7"},
                        "receiver": {
                            "rut": "60803000-K",
                            "business_name": "SERVICIO DE IMPUESTOS INTERNOS",
                        },
                        "items": [
                            {"name": "Cajon AFECTO", "quantity": 161, "unit_price": 3071},
                            {
                                "name": "Servicio EXENTO",
                                "quantity": 1,
                                "unit_price": 1000,
                                "exempt": True,
                            },
                        ],
                        "references": [{"batch_index": 1, "code": 2, "reason": "CORRIGE GIRO"}],
                    }
                ]
            },
        },
        headers=h,
    )

    v = client.get(f"{_base(c.id)}/sets/{cs.id}/preview", headers=h).json()
    assert v["summary"] == "1 documento(s) en un solo sobre"
    doc = v["documents"][0]
    assert doc["doc_label"] == "Factura electrónica"
    assert doc["receiver"] == "SERVICIO DE IMPUESTOS INTERNOS"
    # 161 × 3071 = 494.431, y la línea exenta va aparte.
    assert doc["lines_affect"] == 494431
    assert doc["lines_exempt"] == 1000
    # La primera referencia asocia el documento a su caso del set, como pide el
    # instructivo del SII; las del propio documento van desde la segunda.
    assert doc["references"][0] == f"Caso del set: CASO {cs.code}-1"
    assert "Corrige texto n.º 1 de este mismo envío" in doc["references"][1]
    # Y se dice que esa suma NO es el total del documento.
    assert "no" in v["note"].lower() or "total del documento" in v["note"]


def test_el_descuento_de_linea_se_refleja_en_el_monto(client, db):
    from app.services import certification_preview

    doc = certification_preview.definition(
        "issue-batch",
        {
            "documents": [
                {
                    "type": 33,
                    "items": [
                        {"name": "X", "quantity": 100, "unit_price": 1000, "discount_pct": 20}
                    ],
                }
            ]
        },
    )["documents"][0]
    assert doc["items"][0]["amount"] == 80000


def test_la_vista_previa_de_un_libro_muestra_sus_lineas(client, db):
    from app.services import certification_preview

    v = certification_preview.definition(
        "books",
        {
            "period": "2026-05",
            "book_type": "ESPECIAL",
            "notification_folio": 5038171,
            "lines": [
                {
                    "doc_type": 33,
                    "folio": 19,
                    "rut": "77073851-2",
                    "business_name": "CLIENTE",
                    "net_amount": 1000,
                    "vat_amount": 190,
                    "total_amount": 1190,
                }
            ],
        },
    )
    assert v["kind"] == "libro"
    assert "2026-05" in v["summary"]
    assert "ESPECIAL" in v["detail"]
    assert v["documents"][0]["total"] == 1190


def test_el_contenido_del_sobre_sale_del_xml_firmado(client, db):
    """Ahí sí están el folio y los totales reales: es lo que se va a enviar."""
    c = make_customer(db)
    h = _op(client, db)
    certification_service.certification_set_var.set("5038170")
    certification_service.capture(
        c,
        b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE><DTE><Documento>'
        b"<Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>19</Folio></IdDoc>"
        b"<Receptor><RUTRecep>60803000-K</RUTRecep>"
        b"<RznSocRecep>SERVICIO DE IMPUESTOS INTERNOS</RznSocRecep></Receptor>"
        b"<Totales><MntNeto>494431</MntNeto><IVA>93942</IVA>"
        b"<MntTotal>588373</MntTotal></Totales></Encabezado>"
        b"</Documento></DTE></SetDTE></EnvioDTE>",
        "0257259806",
    )
    certification_service.certification_set_var.set(None)
    sid = db.query(CertificationSubmission).one().id

    v = client.get(f"{_base(c.id)}/submissions/{sid}/contents", headers=h).json()
    doc = v["documents"][0]
    assert doc["folio"] == "19"
    assert doc["doc_label"] == "Factura electrónica"
    assert doc["receiver"] == "SERVICIO DE IMPUESTOS INTERNOS"
    assert doc["total"] == "588373"


def test_importar_carga_los_sets_con_su_definicion(client, db):
    c = make_customer(db)
    h = _op(client, db)
    r = client.post(
        f"{_base(c.id)}/import",
        json={
            "sets": {
                "basico": {
                    "code": "5038170",
                    "endpoint": "issue-batch",
                    "payload": {"documents": [{"type": 33, "items": []}]},
                }
            }
        },
        headers=h,
    )
    assert r.status_code == 200
    assert _set(r.json(), "5038170")["kind"] == "basico"
    cs = db.query(CertificationSet).filter_by(code="5038170").one()
    assert client.get(f"{_base(c.id)}/sets/{cs.id}/definition", headers=h).status_code == 200


def test_la_previa_nombra_los_documentos_externos_de_exportacion():
    """Una factura de exportación referencia el DUS y el documento de transporte.

    Esas referencias no apuntan al propio sobre —no tienen ``batch_index`` ni
    código de corrección—, así que si sólo se sabe leer el código de corrección
    la fila sale vacía y el operador no ve a qué apunta el documento que va a
    emitir. Es justo lo que el SII rechaza si va mal.
    """
    previa = certification_preview.definition(
        "issue-export-batch",
        {
            "documents": [
                {
                    "type": 110,
                    "receiver": {"business_name": "IMPORTADORA EXTRANJERA SA"},
                    "items": [{"name": "MADERA", "quantity": 2, "unit_price": 100}],
                    "references": [
                        {"doc_type": 807, "folio": "1", "date": "2026-09-01", "reason": "DUS"},
                        {"doc_type": 809, "folio": "7", "date": "2026-09-02", "reason": "AWB"},
                    ],
                },
                {
                    "type": 112,
                    "receiver": {"business_name": "IMPORTADORA EXTRANJERA SA"},
                    "items": [],
                    "references": [{"batch_index": 1, "code": 3, "reason": "DEVOLUCION"}],
                },
            ]
        },
    )
    externas = previa["documents"][0]["references"]
    assert externas == [
        "DUS n.º 1 del 2026-09-01 — DUS",
        "Carta de porte aéreo (AWB) n.º 7 del 2026-09-02 — AWB",
    ]
    # La referencia interna se sigue diciendo por posición: el documento al que
    # apunta todavía no tiene folio.
    assert previa["documents"][1]["references"] == [
        "Corrige montos n.º 1 de este mismo envío — DEVOLUCION"
    ]


def test_el_indice_lista_solo_los_de_certificacion(client, db):
    """La portada del módulo no debe ofrecer expedientes que no existen.

    Un cliente de producción no tiene set de pruebas que seguir, y el propio
    expediente responde 400 si se le pide. Listarlo aquí sería un enlace roto.
    """
    make_customer(db, key="acme-cert")
    produccion = make_customer(db, rut="77262159-0", key="acme-prod")
    produccion.environment = SiiEnvironment.PRODUCTION
    db.commit()

    r = client.get("/admin/certification", headers=_op(client, db))
    assert r.status_code == 200
    filas = r.json()
    assert [f["key"] for f in filas] == ["acme-cert"]
    # El denominador sale del catálogo: diez sets, se hayan dado de alta o no.
    assert filas[0]["progress"]["sets_total"] == 10
    assert filas[0]["last_activity"] is None


def test_la_previa_entiende_la_liquidacion_factura():
    """La liquidación factura no tiene la forma de los demás documentos.

    Sus montos van en ``lines``, nunca lleva ``type`` y las comisiones cuelgan
    aparte. Leída con el molde genérico, la previa mostraba cuatro documentos
    «—» con todo en cero: eso invita a emitir un set que no se ha revisado.
    """
    previa = certification_preview.definition(
        "issue-settlement-batch",
        {
            "documents": [
                {
                    "receiver": {"business_name": "MANDANTE EJEMPLO LIMITADA", "rut": "17099910-K"},
                    "lines": [
                        {"name": "NETO FACTURAS", "amount": 180269, "quantity": 4},
                        {"name": "EXENTO FACTURAS", "amount": 50865, "quantity": 3, "exempt": True},
                    ],
                    "commissions": [{"description": "NETO COMISION FIJA", "net_amount": 866}],
                }
            ]
        },
    )
    doc = previa["documents"][0]
    assert doc["doc_label"] == "Liquidación factura"
    assert doc["receiver"] == "MANDANTE EJEMPLO LIMITADA"
    assert doc["lines_affect"] == 180269
    assert doc["lines_exempt"] == 50865
    # Las comisiones tienen que verse: sin ellas la línea del libro no cierra.
    assert doc["global_discounts"] == ["Comisión: NETO COMISION FIJA 866"]


def test_el_sobre_sin_enviar_no_se_llama_none(client, db, fake_book_engine):
    """Dos sobres emitidos y sin enviar no pueden compartir nombre de archivo.

    El nombre salía del TrackID, que en un sobre emitido y sin enviar todavía no
    existe: se descargaban los dos como «EnvioDTE_None.xml» y el segundo pisaba
    al primero en la carpeta de descargas.
    """
    customer = make_customer(db, key="sobre-cert")
    _con_envio(db, customer, code="5038170")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.track_id = None
    db.commit()

    r = client.get(
        f"/admin/customers/{customer.id}/certification/submissions/{envio.id}/envelope",
        headers=_op(client, db),
    )
    assert r.status_code == 200
    assert r.json()["filename"] == f"EnvioDTE_sin-enviar-{envio.id}.xml"


def test_el_fallo_de_autenticacion_dice_que_revisar(client, db, monkeypatch):
    """«estado=10» es exacto y no dice nada accionable.

    La causa nunca está en la semilla sino en quién la firma: un certificado no
    acreditado, o un RUT sin «Enviar Doctos» en ESE ambiente. Es el error que
    costó diez envíos rechazados antes de dar con el permiso, así que la guía
    viaja con la respuesta en vez de vivir en la cabeza de quien ya lo sufrió.
    """
    from dte_chile.errors import SiiAuthError

    customer = make_customer(db, key="auth-cert")
    _con_envio(db, customer, code="5038170")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.track_id = None
    db.commit()

    def revienta(*a, **k):
        raise SiiAuthError("El SII rechazó la semilla firmada (estado=10).")

    monkeypatch.setattr(certification_service, "send_draft", revienta)

    r = client.post(
        f"/admin/customers/{customer.id}/certification/submissions/{envio.id}/send",
        headers=_op(client, db),
    )
    assert r.status_code == 502
    cuerpo = r.json()["error"]
    assert "estado=10" in cuerpo["message"]
    guia = " ".join(cuerpo["details"])
    assert "acreditada" in guia
    assert "Enviar Doctos" in guia
    # El permiso es por ambiente: omitirlo es justo lo que hizo perder el tiempo.
    assert "SEPARADOS" in guia


def test_no_envia_un_sobre_firmado_con_otro_certificado(client, db, monkeypatch):
    """Cambiar el certificado no rehace la firma del sobre ya emitido.

    Enviarlo así gasta un TrackID y vuelve como RFR «error en firma» —cierto en
    ese caso, y de los rechazos más caros de diagnosticar porque su causa
    habitual es otra: un permiso que falta.
    """
    from app.db.models import CustomerCertificate

    customer = make_customer(db, key="firma-cert")
    _con_envio(db, customer, code="5038170")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.track_id = None
    envio.signed_thumbprint = "huella-del-certificado-viejo"
    db.commit()
    # El cliente tiene ahora otro certificado vigente.
    db.query(CustomerCertificate).filter_by(customer_id=customer.id).update(
        {"thumbprint": "huella-nueva"}
    )
    db.commit()

    url = f"/admin/customers/{customer.id}/certification/submissions/{envio.id}/send"
    r = client.post(url, headers=_op(client, db))
    assert r.status_code == 409
    assert "ya no es el vigente" in r.json()["detail"]
    assert "Vuelve a emitir" in r.json()["detail"]


def test_un_sobre_de_antes_no_se_bloquea(client, db, monkeypatch):
    """Sin huella guardada no se afirma nada.

    Los sobres anteriores a que se guardara no dicen con qué se firmaron;
    bloquearlos por si acaso impediría un reenvío legítimo.
    """
    customer = make_customer(db, key="firma-vieja")
    _con_envio(db, customer, code="5038170")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.track_id = None
    envio.signed_thumbprint = None
    db.commit()

    llamado = {}

    def _envia(*a, **k):
        llamado["si"] = True
        return envio

    monkeypatch.setattr(certification_service, "send_draft", _envia)
    url = f"/admin/customers/{customer.id}/certification/submissions/{envio.id}/send"
    assert client.post(url, headers=_op(client, db)).status_code == 200
    assert llamado == {"si": True}


def test_un_sobre_procesado_con_todo_rechazado_no_se_pinta_verde(client, db):
    """`EPR` dice que el sobre se pudo leer, no que sus documentos valgan.

    Es el caso real de esta certificación: siete sets con EPR y sus 28
    documentos rechazados dentro. El expediente los mostró como aceptados
    durante una semana, y con ellos en verde no había nada que mirar.
    """
    customer = make_customer(db, key="epr-cert")
    _con_envio(db, customer, code="5038170", estado="EPR")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.sii_stats = [
        {"doc_type": 33, "informed": 4, "accepted": 0, "rejected": 4, "flagged": 0},
        {"doc_type": 61, "informed": 3, "accepted": 0, "rejected": 3, "flagged": 0},
    ]
    db.commit()

    dossier = client.get(
        f"/admin/customers/{customer.id}/certification", headers=_op(client, db)
    ).json()
    cert_set = next(s for s in dossier["sets"] if s["code"] == "5038170")

    etapa = next(e for e in cert_set["stages"] if e["key"] == "estado")
    assert etapa["state"] == "error"
    assert "ninguno de los 7" in etapa["detail"]
    assert cert_set["state"] == "rechazado"

    # Y la guía deja de decir que está todo bien.
    assert cert_set["submissions"][0]["cause"]["ok"] is False
    # El desglose viaja al portal, que es lo que permite ver qué tipo falló.
    assert [s["doc_type"] for s in cert_set["submissions"][0]["stats"]] == [33, 61]


def test_un_libro_aceptado_sigue_en_verde(client, db):
    """El LOK de un libro sí es el veredicto entero: no trae desglose."""
    customer = make_customer(db, key="lok-cert")
    _con_envio(db, customer, code="5038171", estado="LOK")

    dossier = client.get(
        f"/admin/customers/{customer.id}/certification", headers=_op(client, db)
    ).json()
    cert_set = next(s for s in dossier["sets"] if s["code"] == "5038171")
    etapa = next(e for e in cert_set["stages"] if e["key"] == "estado")
    assert etapa["state"] == "ok"
    assert cert_set["submissions"][0]["cause"]["ok"] is True


def test_aceptados_parciales_avisan_sin_gritar(client, db):
    """Con algunos aceptados y otros no, el set no está listo ni perdido."""
    customer = make_customer(db, key="mixto-cert")
    _con_envio(db, customer, code="5038170", estado="EPR")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.sii_stats = [{"doc_type": 33, "informed": 4, "accepted": 3, "rejected": 1, "flagged": 0}]
    db.commit()

    dossier = client.get(
        f"/admin/customers/{customer.id}/certification", headers=_op(client, db)
    ).json()
    cert_set = next(s for s in dossier["sets"] if s["code"] == "5038170")
    etapa = next(e for e in cert_set["stages"] if e["key"] == "estado")
    assert etapa["state"] == "atencion"
    assert "3 de 4 aceptados" in etapa["detail"]


def test_consultar_un_sobre_sin_enviar_dice_que_no_hay_trackid(client, db):
    """Un borrador no tiene TrackID: preguntarle al SII no tiene sentido."""
    customer = make_customer(db, key="sin-track")
    _con_envio(db, customer, code="5038170")
    envio = db.query(CertificationSubmission).filter_by(customer_id=customer.id).one()
    envio.track_id = None
    db.commit()

    r = client.post(
        f"/admin/customers/{customer.id}/certification/submissions/{envio.id}/refresh",
        headers=_op(client, db),
    )
    assert r.status_code == 409
    assert "no tiene TrackID" in r.json()["detail"]
