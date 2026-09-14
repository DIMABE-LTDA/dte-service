"""Lo que completa el sistema en cada set: nada que dependa del cliente o del día
viene escrito en la definición.

Cada regla sale del «Instructivo para la construcción de documentos con los
datos del set de pruebas» del SII, y cada test cubre algo que la primera
certificación traía escrito a mano: el emisor en cada documento, la fecha fija,
ninguna referencia al caso, y un libro de ventas con folios de otra ronda.
"""

import datetime as dt

from app.core import crypto
from app.db.models import CertificationSet, CertificationSubmission
from app.services import certification_fill, customer_service
from tests.conftest import auth_header, make_customer, make_user

HOY = dt.date(2026, 9, 10)


def _perfil(customer, **cambios):
    datos = {
        "legal_name": "CONSTRUCTORA DIMABE SPA",
        "activity": "OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION",
        "economic_activity": 439000,
        "address": "MONS. FDO. DE BARRIONUEVO #1540",
        "commune": "RANCAGUA",
        "city": "RANCAGUA",
        "branch_code": 4229993,
        **cambios,
    }
    customer_service.set_issuer(customer, datos)


def _set(db, customer, kind, code):
    s = CertificationSet(customer_id=customer.id, code=code, kind=kind)
    db.add(s)
    db.commit()
    return s


_DEFINICION = {
    "documents": [
        {
            "type": 33,
            "issue_date": "2026-09-01",
            "issuer": {"rut": "99999999-9", "business_name": "OTRO CONTRIBUYENTE"},
            "receiver": {"rut": "60803000-K", "business_name": "SII"},
            "items": [{"name": "Pañuelo AFECTO", "quantity": 1, "unit_price": 100}],
        },
        {
            "type": 61,
            "issue_date": "2026-09-01",
            "issuer": {"rut": "99999999-9"},
            "receiver": {"rut": "60803000-K", "business_name": "SII"},
            "items": [{"name": "Pañuelo AFECTO", "quantity": 1, "unit_price": 100}],
            "references": [
                {"doc_type": "SET", "folio": "1", "reason": "CASO VIEJO-2"},
                {"batch_index": 1, "code": 1, "reason": "ANULA FACTURA"},
            ],
        },
        {
            "type": 110,
            "issue_date": "2026-09-01",
            "receiver": {"rut": "55555555-5", "business_name": "IMPORTADORA"},
            "items": [{"name": "MADERA", "quantity": 1, "unit_price": 10}],
            "references": [{"doc_type": 807, "folio": "1", "date": "2026-09-01", "reason": "DUS"}],
        },
    ]
}


# --------------------------------------------------------------------------- #
#  Guardar: sólo los datos del caso
# --------------------------------------------------------------------------- #


def test_la_definicion_guarda_solo_los_datos_del_caso():
    guardada = certification_fill.strip("issue-batch", "basico", _DEFINICION)
    docs = guardada["documents"]

    assert all("issuer" not in d and "issue_date" not in d for d in docs)
    # La referencia al caso la pone el sistema con el número de atención.
    assert all(str(r.get("doc_type")) != "SET" for d in docs for r in d.get("references", []))
    # La referencia interna se conserva, y la externa pierde su fecha fija.
    assert docs[1]["references"] == [{"batch_index": 1, "code": 1, "reason": "ANULA FACTURA"}]
    assert "date" not in docs[2]["references"][0]
    # Lo que SÍ es del caso queda intacto, acentos incluidos.
    assert docs[0]["items"][0]["name"] == "Pañuelo AFECTO"
    # Y no se toca el original.
    assert _DEFINICION["documents"][0]["issuer"]["rut"] == "99999999-9"


def test_el_libro_guarda_sin_periodo_ni_folio_ni_lineas_generadas():
    libro = {"period": "2026-09", "notification_folio": 5038171, "lines": [{"folio": 18}]}
    ventas = certification_fill.strip("books", "libro_ventas", libro)
    assert ventas == {}
    # El de compras es dato del caso: sus líneas las entrega el SII.
    compras = certification_fill.strip("books", "libro_compras", libro)
    assert compras == {"lines": [{"folio": 18}]}


# --------------------------------------------------------------------------- #
#  Emitir: lo que pone el sistema
# --------------------------------------------------------------------------- #


def test_el_sistema_pone_emisor_fecha_y_caso(db):
    customer = make_customer(db)
    _perfil(customer)
    s = _set(db, customer, "basico", "5038170")

    cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", _DEFINICION, hoy=HOY)
    docs = cuerpo["documents"]

    # El emisor es el cliente, venga lo que venga en la definición.
    assert {d["issuer"]["rut"] for d in docs} == {customer.rut}
    assert docs[0]["issuer"]["business_name"] == "CONSTRUCTORA DIMABE SPA"
    assert docs[0]["issuer"]["branch_code"] == 4229993
    # «Agregue fecha del día».
    assert {d["issue_date"] for d in docs} == {"2026-09-10"}
    # Primera referencia de cada documento: el caso, con el número de atención.
    assert [d["references"][0]["reason"] for d in docs] == [
        "CASO 5038170-1",
        "CASO 5038170-2",
        "CASO 5038170-3",
    ]
    assert all(d["references"][0]["doc_type"] == "SET" for d in docs)
    # Las demás, desde la segunda: la nota sigue apuntando a su factura.
    assert docs[1]["references"][1]["batch_index"] == 1
    # La externa toma la fecha de emisión.
    assert docs[2]["references"][1]["date"] == "2026-09-10"
    # Sin receptores de prueba configurados, se avisa en vez de callar.
    assert any("sin receptores de prueba" in n for n in notas)


def test_un_caso_con_otra_numeracion_se_respeta(db):
    customer = make_customer(db)
    _perfil(customer)
    s = _set(db, customer, "basico", "5038170")
    definicion = {"documents": [{**_DEFINICION["documents"][0], "case": "5038170-7"}]}

    cuerpo, _ = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)
    assert cuerpo["documents"][0]["references"][0]["reason"] == "CASO 5038170-7"


def test_sin_datos_del_emisor_se_dice_que_faltan(db):
    customer = make_customer(db)
    s = _set(db, customer, "basico", "5038170")

    _cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", _DEFINICION, hoy=HOY)
    faltan = next(n for n in notas if "faltan datos del emisor" in n)
    assert "razón social" in faltan and "giro" in faltan


def test_emitir_sin_datos_del_emisor_se_detiene_con_un_mensaje_claro(client, db):
    """Mejor que un 500 del esquema: esto lo arregla una persona en la ficha."""
    from app.db.models import CertificationDefinition

    customer = make_customer(db)
    s = _set(db, customer, "basico", "5038170")
    db.add(CertificationDefinition(set_id=s.id, endpoint="issue-batch", payload=_DEFINICION))
    db.commit()
    make_user(db, "op@dimabe.cl", "secret", "operator")
    h = auth_header(client, "op@dimabe.cl", "secret")

    r = client.post(f"/admin/customers/{customer.id}/certification/sets/{s.id}/emit", headers=h)
    assert r.status_code == 409
    assert "faltan datos del emisor" in r.json()["detail"]
    assert "razón social" in r.json()["detail"]


# --------------------------------------------------------------------------- #
#  Libros: con los documentos aceptados
# --------------------------------------------------------------------------- #


def _sobre(*docs: str) -> bytes:
    return (
        '<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE>'
        + "".join(f"<DTE><Documento>{d}</Documento></DTE>" for d in docs)
        + "</SetDTE></EnvioDTE>"
    ).encode("iso-8859-1")


_FACTURA = (
    "<Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>23</Folio><FchEmis>2026-09-10</FchEmis>"
    "</IdDoc><Receptor><RUTRecep>76086428-5</RUTRecep><RznSocRecep>CLIENTE UNO</RznSocRecep>"
    "</Receptor><Totales><MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA>"
    "<MntTotal>1190</MntTotal></Totales></Encabezado>"
)
_NOTA = (
    "<Encabezado><IdDoc><TipoDTE>61</TipoDTE><Folio>32</Folio><FchEmis>2026-09-10</FchEmis>"
    "</IdDoc><Receptor><RUTRecep>76086428-5</RUTRecep><RznSocRecep>CLIENTE UNO</RznSocRecep>"
    "</Receptor><Totales><MntNeto>1000</MntNeto><IVA>190</IVA><MntTotal>1190</MntTotal>"
    "</Totales></Encabezado>"
    "<Referencia><NroLinRef>1</NroLinRef><TpoDocRef>SET</TpoDocRef><FolioRef>0</FolioRef>"
    "<RazonRef>CASO 5038170-2</RazonRef></Referencia>"
    "<Referencia><NroLinRef>2</NroLinRef><TpoDocRef>33</TpoDocRef><FolioRef>23</FolioRef>"
    "<CodRef>1</CodRef></Referencia>"
)


def _envio(db, customer, cert_set, estado, stats=None, xml=None):
    db.add(
        CertificationSubmission(
            set_id=cert_set.id,
            customer_id=customer.id,
            track_id="1",
            sent_at=dt.datetime(2026, 9, 10),
            envelope_kind="EnvioDTE",
            envelope_encrypted=crypto.encrypt(xml or _sobre(_FACTURA, _NOTA)),
            sii_state=estado,
            sii_stats=stats,
        )
    )
    db.commit()


def test_el_libro_de_ventas_se_arma_con_los_documentos_aceptados(db):
    customer = make_customer(db)
    basico = _set(db, customer, "basico", "5038170")
    libro = _set(db, customer, "libro_ventas", "5038171")
    _envio(db, customer, basico, "EPR", [{"doc_type": 33, "informed": 1, "accepted": 1}])

    cuerpo, _ = certification_fill.fill(db, customer, libro, "books", {"lines": [{"folio": 18}]})

    assert [(line["doc_type"], line["folio"]) for line in cuerpo["lines"]] == [(33, 23), (61, 32)]
    nota = cuerpo["lines"][1]
    # La nota declara el documento que modifica, no la referencia al caso.
    assert (nota["ref_doc_type"], nota["ref_folio"]) == (33, 23)
    assert cuerpo["lines"][0]["total_amount"] == 1190
    # Período el de los documentos, folio de notificación el del set.
    assert cuerpo["period"] == "2026-09"
    assert cuerpo["notification_folio"] == 5038171


def test_un_envio_rechazado_no_entra_al_libro(db):
    """Declarar documentos rechazados es declarar folios que el SII no tiene."""
    customer = make_customer(db)
    basico = _set(db, customer, "basico", "5038170")
    libro = _set(db, customer, "libro_ventas", "5038171")
    _envio(db, customer, basico, "EPR", [{"doc_type": 33, "informed": 2, "rejected": 2}])

    cuerpo, notas = certification_fill.fill(db, customer, libro, "books", {})
    assert cuerpo["lines"] == []
    assert "basico (5038170)" in notas[0]


def test_las_comisiones_de_la_liquidacion_van_al_libro(db):
    customer = make_customer(db)
    liq = _set(db, customer, "liquidacion", "5038178")
    libro = _set(db, customer, "libro_ventas", "5038171")
    liquidacion = (
        "<Encabezado><IdDoc><TipoDTE>43</TipoDTE><Folio>17</Folio><FchEmis>2026-09-10</FchEmis>"
        "</IdDoc><Receptor><RUTRecep>17099910-K</RUTRecep><RznSocRecep>MANDANTE</RznSocRecep>"
        "</Receptor><Totales><MntNeto>149226</MntNeto><MntExe>37094</MntExe><IVA>28353</IVA>"
        "<Comisiones><ValComNeto>3145</ValComNeto><ValComExe>0</ValComExe>"
        "<ValComIVA>598</ValComIVA></Comisiones><MntTotal>210930</MntTotal></Totales>"
        "</Encabezado>"
    )
    xml = (
        '<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE><DTE><Liquidacion>'
        + liquidacion
        + "</Liquidacion></DTE></SetDTE></EnvioDTE>"
    ).encode()
    _envio(db, customer, liq, "EPR", [{"doc_type": 43, "informed": 1, "accepted": 1}], xml)

    cuerpo, _ = certification_fill.fill(db, customer, libro, "books", {})
    linea = cuerpo["lines"][0]
    # Los mismos valores que el libro que el SII aceptó para este caso.
    assert (linea["commission_net"], linea["commission_vat"]) == (3145, 598)
    assert linea["total_amount"] == 210930


def test_el_libro_de_guias_se_arma_con_las_guias_aceptadas(db):
    customer = make_customer(db)
    guias = _set(db, customer, "guias", "5038173")
    libro = _set(db, customer, "libro_guias", "5038174")
    guia = (
        "<Encabezado><IdDoc><TipoDTE>52</TipoDTE><Folio>10</Folio><FchEmis>2026-09-10</FchEmis>"
        "<IndTraslado>5</IndTraslado></IdDoc><Receptor><RUTRecep>76158145-7</RUTRecep>"
        "<RznSocRecep>PROPIA</RznSocRecep></Receptor><Totales><MntTotal>0</MntTotal></Totales>"
        "</Encabezado>"
    )
    _envio(
        db, customer, guias, "EPR", [{"doc_type": 52, "informed": 1, "accepted": 1}], _sobre(guia)
    )

    cuerpo, _ = certification_fill.fill(db, customer, libro, "books/guides", {})
    assert cuerpo["lines"] == [
        {
            "folio": 10,
            "date": "2026-09-10",
            "receiver_rut": "76158145-7",
            "receiver_name": "PROPIA",
            "net_amount": 0,
            "vat_amount": 0,
            "total_amount": 0,
            "transfer_type": 5,
        }
    ]


# --------------------------------------------------------------------------- #
#  Perfil del emisor: portal y ERP
# --------------------------------------------------------------------------- #


def test_el_perfil_se_configura_desde_el_portal(client, db):
    customer = make_customer(db)
    make_user(db, "sa@dimabe.cl", "secret", "superadmin")
    h = auth_header(client, "sa@dimabe.cl", "secret")

    r = client.patch(
        f"/admin/customers/{customer.id}",
        json={
            "issuer": {
                "legal_name": "EMPRESA SPA",
                "activity": "GIRO",
                "economic_activity": 1,
                "address": "CALLE 1",
                "commune": "SANTIAGO",
            }
        },
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["issuer"]["legal_name"] == "EMPRESA SPA"
    assert r.json()["issuer_missing"] == []


def test_un_perfil_incompleto_dice_que_falta(client, db):
    customer = make_customer(db)
    make_user(db, "sa@dimabe.cl", "secret", "superadmin")
    h = auth_header(client, "sa@dimabe.cl", "secret")
    r = client.get(f"/admin/customers/{customer.id}", headers=h)
    assert set(r.json()["issuer_missing"]) == {
        "razón social",
        "giro",
        "código ACTECO",
        "dirección",
        "comuna",
    }


# --------------------------------------------------------------------------- #
#  Receptores
# --------------------------------------------------------------------------- #

_RECEPTORES = [
    {
        "rut": "76086428-5",
        "business_name": "CLIENTE UNO",
        "activity": "COMERCIO",
        "address": "CALLE 1",
        "commune": "RANCAGUA",
    },
    {
        "rut": "96790240-3",
        "business_name": "CLIENTE DOS",
        "activity": "COMERCIO",
        "address": "CALLE 2",
        "commune": "RANCAGUA",
    },
]
_SII = {
    "rut": "60803000-K",
    "business_name": "SII",
    "activity": "G",
    "address": "T",
    "commune": "S",
}


def _item():
    return [{"name": "X", "quantity": 1, "unit_price": 10}]


def _con_receptores(db, customer, lista=None):
    _perfil(customer)
    customer.cert_receivers = _RECEPTORES if lista is None else lista
    db.commit()


def test_cada_factura_va_a_un_cliente_distinto_y_la_nota_hereda_el_suyo(db):
    """«Utilice RUT distintos para las distintas facturas.»"""
    customer = make_customer(db)
    _con_receptores(db, customer)
    s = _set(db, customer, "basico", "5038170")
    definicion = {
        "documents": [
            {"type": 33, "receiver": _SII, "items": _item()},
            {"type": 33, "receiver": _SII, "items": _item()},
            {
                "type": 61,
                "receiver": _SII,
                "items": _item(),
                "references": [{"batch_index": 2, "code": 1, "reason": "ANULA"}],
            },
            {
                "type": 56,
                "receiver": _SII,
                "items": _item(),
                "references": [{"batch_index": 3, "code": 1, "reason": "ANULA NC"}],
            },
        ]
    }

    cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)
    ruts = [d["receiver"]["rut"] for d in cuerpo["documents"]]
    assert ruts[:2] == ["76086428-5", "96790240-3"]
    # La NC anula la segunda factura y la ND anula la NC: siguen la cadena.
    assert ruts[2:] == ["96790240-3", "96790240-3"]
    assert notas == []


def test_la_guia_de_traslado_interno_va_al_propio_emisor(db):
    """La definición traía el RUT de otro contribuyente escrito a mano."""
    customer = make_customer(db)
    _con_receptores(db, customer)
    s = _set(db, customer, "guias", "5038173")
    otro = {
        "rut": "77262159-0",
        "business_name": "OTRO",
        "activity": "A",
        "address": "B",
        "commune": "C",
    }
    definicion = {
        "documents": [
            {"type": 52, "transfer_type": 5, "receiver": otro, "items": _item()},
            {"type": 52, "transfer_type": 1, "receiver": _SII, "items": _item()},
        ]
    }
    cuerpo, _ = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)
    interna, venta = cuerpo["documents"]
    assert interna["receiver"]["rut"] == customer.rut
    assert interna["receiver"]["business_name"] == "CONSTRUCTORA DIMABE SPA"
    assert venta["receiver"]["rut"] == "76086428-5"


def test_los_receptores_del_caso_no_se_tocan(db):
    """Importador, mandante y vendedor vienen del set: no son clientes del emisor."""
    customer = make_customer(db)
    _con_receptores(db, customer)
    s = _set(db, customer, "factura_compra", "5038180")
    vendedor = {
        "rut": "17099910-K",
        "business_name": "VENDEDOR",
        "activity": "A",
        "address": "B",
        "commune": "C",
    }
    definicion = {
        "documents": [
            {"type": 46, "receiver": vendedor, "items": _item()},
            {
                "type": 61,
                "receiver": vendedor,
                "items": _item(),
                "references": [{"batch_index": 1, "code": 3, "reason": "DEVOLUCION"}],
            },
        ]
    }
    cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)
    assert {d["receiver"]["rut"] for d in cuerpo["documents"]} == {"17099910-K"}
    assert notas == []


def test_con_menos_receptores_que_facturas_se_avisa(db):
    customer = make_customer(db)
    _con_receptores(db, customer, _RECEPTORES[:1])
    s = _set(db, customer, "basico", "5038170")
    # Tres documentos distintos: `[{...}] * 3` sería el MISMO dict tres veces.
    definicion = {"documents": [{"type": 33, "receiver": _SII, "items": _item()} for _ in range(3)]}
    _, notas = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)
    assert "3 documentos y 1 receptor(es)" in notas[0]
