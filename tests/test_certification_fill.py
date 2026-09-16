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


def test_avisa_de_un_bulto_sin_contenedor_ni_sello(db):
    """El reparo (HED-2-804) del set de exportación (2), folio 8.

    El XSD da `IdContainer` y `Sello` por opcionales y el SII los exige igual en
    cuanto el documento informa bultos. Descubrirlo costó un sobre entero —tres
    folios— porque el reparo sólo aparece DESPUÉS de enviar; el aviso va en la
    previa, que es donde se decide gastarlos.
    """
    customer = make_customer(db)
    _perfil(customer)
    s = _set(db, customer, "exportacion_2", "5038177")
    definicion = {
        "documents": [
            {
                "type": 110,
                "items": [{"name": "CAJAS", "quantity": "239", "unit_price": "114"}],
                "customs": {"packages": [{"kind_code": 75, "quantity": 24, "marks": "SIN MARCAS"}]},
            }
        ]
    }

    _, notas = certification_fill.fill(db, customer, s, "issue-export-batch", definicion, hoy=HOY)
    aviso = next(n for n in notas if "bultos" in n)
    assert "Id. Container" in aviso and "Sello" in aviso
    assert "HED-2-804" in aviso


def test_un_bulto_con_contenedor_y_sello_no_avisa(db):
    customer = make_customer(db)
    _perfil(customer)
    s = _set(db, customer, "exportacion_2", "5038177")
    definicion = {
        "documents": [
            {
                "type": 110,
                "items": [{"name": "CAJAS", "quantity": "239", "unit_price": "114"}],
                "customs": {
                    "packages": [
                        {
                            "kind_code": 75,
                            "quantity": 24,
                            "marks": "SIN MARCAS",
                            "container_id": "TCLU1234567",
                            "seal": "SL-4471209",
                        }
                    ]
                },
            }
        ]
    }

    _, notas = certification_fill.fill(db, customer, s, "issue-export-batch", definicion, hoy=HOY)
    assert not [n for n in notas if "bultos" in n]


def test_el_set_de_boletas_entra_al_expediente_sin_numero_de_atencion(db):
    """El SII no numera el set de boletas: no está en «Declarar avance».

    El import descartaba en silencio los sets sin código y la verificación
    pedía «copia su número de atención» de un número que no existe, con
    resultado de ERROR — lo que bloqueaba la emisión de los otros diez sets,
    que no tienen nada que ver.
    """
    from app.services import certification_service

    customer = make_customer(db)
    s = certification_service.find_or_create_set_by_kind(db, customer.id, "boletas")
    assert s.kind == "boletas"
    assert s.code == ""
    # Idempotente: cargar dos veces no duplica el set.
    assert certification_service.find_or_create_set_by_kind(db, customer.id, "boletas").id == s.id


def test_el_set_de_boletas_recibe_emisor_y_fecha_como_los_demas(db):
    """Las boletas van en `receipts`, no en `documents`.

    Sin tratar esa forma aparte, el emisor y la fecha no se rellenaban y la
    definición tenía que traerlos escritos — justo lo que el resto del
    expediente evita, porque un emisor copiado a mano contradice la ficha.
    """
    customer = make_customer(db)
    _perfil(customer)
    s = _set(db, customer, "boletas", "")
    definicion = {
        "receipts": [
            {
                "type": 39,
                "issuer": {"rut": "99999999-9"},  # de otro contribuyente
                "issue_date": "2026-09-01",  # de otro día
                "items": [{"name": "Arroz", "quantity": 5, "unit_price": 700, "unit": "Kg"}],
                "references": [{"doc_type": "SET", "folio": "1", "reason": "CASO-5"}],
            }
        ]
    }

    guardada = certification_fill.strip("boletas", "boletas", definicion)
    assert "issuer" not in guardada["receipts"][0]
    assert "issue_date" not in guardada["receipts"][0]
    # La referencia al caso SÍ se conserva: en boletas el SII numera los casos
    # por su cuenta (CASO-1..5), no por número de atención, así que es dato.
    assert guardada["receipts"][0]["references"][0]["reason"] == "CASO-5"

    cuerpo, notas = certification_fill.fill(db, customer, s, "boletas", definicion, hoy=HOY)
    boleta = cuerpo["receipts"][0]
    assert boleta["issuer"]["rut"] == customer.rut
    assert boleta["issue_date"] == "2026-09-10"
    # El plazo de 24 horas es lo que hace caro equivocarse, y no se ve en
    # ninguna otra parte de la pantalla.
    assert any("24 horas" in n for n in notas)


_EXPORT_SIN_PAGO = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>'
    b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><SetDTE ID="S">'
    b"<DTE><Exportaciones><Encabezado>"
    b"<IdDoc><TipoDTE>110</TipoDTE><Folio>10</Folio><FchEmis>2026-09-14</FchEmis>"
    b"<FmaPagExp>21</FmaPagExp></IdDoc>"
    b"<Receptor><RUTRecep>55555555-5</RUTRecep><RznSocRecep>IMPORTADORA</RznSocRecep></Receptor>"
    b"<Totales><TpoMoneda>LIBRA EST</TpoMoneda><MntExe>154344</MntExe>"
    b"<MntTotal>154344</MntTotal></Totales>"
    # Con S/PAGO el SII EXIGE los montos en otra moneda en cero (HED-1-803).
    b"<OtraMoneda><TpoMoneda>PESO CL</TpoMoneda><TpoCambio>2</TpoCambio>"
    b"<MntExeOtrMnda>0</MntExeOtrMnda><MntTotOtrMnda>0</MntTotOtrMnda></OtraMoneda>"
    b"</Encabezado></Exportaciones></DTE></SetDTE></EnvioDTE>"
)


def test_una_exportacion_sin_pago_no_entra_al_libro_en_cero():
    """El reparo del SII: «Falta [MntTotal MntPeriodo] T:[110]-F:[10]».

    Con forma de pago S/PAGO el Servicio EXIGE que los montos en otra moneda
    vayan en cero —regla HED-1-803, ya corregida en el motor—, así que ese cero
    no dice que el documento no valga nada: dice que ahí no se informa.
    Copiarlo al Libro de Ventas dejaba la línea entera en cero, y el libro
    declaraba un documento sin monto.
    """
    linea = certification_fill.lines_from_envelope(_EXPORT_SIN_PAGO, "libro_ventas")[0]

    assert linea["doc_type"] == 110
    assert linea["folio"] == 10
    # El libro va en pesos: se convierte con el tipo de cambio del documento.
    assert linea["exempt_amount"] == 308688
    assert linea["total_amount"] == 308688


def test_una_exportacion_pagada_usa_los_pesos_que_declara():
    """El caso normal no cambia: si OtraMoneda trae montos, mandan ellos."""
    xml = _EXPORT_SIN_PAGO.replace(
        b"<MntExeOtrMnda>0</MntExeOtrMnda><MntTotOtrMnda>0</MntTotOtrMnda>",
        b"<MntExeOtrMnda>14490</MntExeOtrMnda><MntTotOtrMnda>14490</MntTotOtrMnda>",
    )
    linea = certification_fill.lines_from_envelope(xml, "libro_ventas")[0]
    # 14490 y no 308688: el documento ya dijo cuánto vale en pesos.
    assert linea["total_amount"] == 14490


_GUIAS = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>'
    b'<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><SetDTE ID="S">'
    + b"".join(
        b"<DTE><Documento><Encabezado>"
        b"<IdDoc><TipoDTE>52</TipoDTE><Folio>" + str(f).encode() + b"</Folio>"
        b"<FchEmis>2026-09-14</FchEmis><IndTraslado>1</IndTraslado></IdDoc>"
        b"<Receptor><RUTRecep>76008900-1</RUTRecep><RznSocRecep>CLIENTE</RznSocRecep></Receptor>"
        b"<Totales><MntNeto>" + str(n).encode() + b"</MntNeto><IVA>" + str(i).encode() + b"</IVA>"
        b"<MntTotal>" + str(n + i).encode() + b"</MntTotal></Totales>"
        b"</Encabezado></Documento></DTE>"
        for f, n, i in ((1, 0, 0), (2, 2586065, 491352), (3, 1930610, 366816))
    )
    + b"</SetDTE></EnvioDTE>"
)


def test_el_libro_de_guias_marca_la_facturada_y_la_anulada():
    """Lo exige el instructivo del set 5038174, literal:

    «EL CASO 2 CORRESPONDE A UNA GUIA QUE SE FACTURO EN EL PERIODO» y «EL CASO 3
    CORRESPONDE A UNA GUIA ANULADA».

    No sale del sobre de guías —las tres se emitieron iguales y el SII las
    aceptó—, sino del enunciado del caso. Sin esto el libro las declaraba como
    tres guías de venta normales, con el monto de la anulada sumando al total
    del período. No se vio antes porque el SII rechazaba este libro por cascada
    («No Tiene un SET GUia de Despacho Aprobado») y nunca llegaba a mirar su
    contenido.
    """
    lineas = certification_fill.lines_from_envelope(_GUIAS, "libro_guias")

    assert [x["folio"] for x in lineas] == [1, 2, 3]
    # La primera no lleva nada: es el traslado interno, sin venta.
    assert "voided" not in lineas[0] and "modified_amount" not in lineas[0]
    # La segunda, facturada en el período: su monto pasa a «modificado».
    assert lineas[1]["modified_amount"] == 3077417
    assert "voided" not in lineas[1]
    # La tercera, anulada después de enviarla: no aporta monto al período.
    assert lineas[2]["voided"] == 2
    assert lineas[2]["total_amount"] == 0
    assert lineas[2]["net_amount"] == 0 and lineas[2]["vat_amount"] == 0


def test_el_libro_de_ventas_no_se_ve_afectado_por_los_casos_de_guias():
    """El mapa de casos es del libro de GUÍAS: no puede tocar el de ventas."""
    lineas = certification_fill.lines_from_envelope(_GUIAS, "libro_ventas")
    assert all("voided" not in x and "modified_amount" not in x for x in lineas)


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


def test_el_giro_del_emisor_se_recorta_para_el_receptor_de_la_guia_interna(db):
    """GiroRecep admite 40 caracteres y GiroEmis 80.

    En la guía de traslado interno el emisor va también de receptor, así que un
    giro válido en su propio campo no cabe en el del receptor y el documento se
    caía antes de emitir. El operador no puede arreglarlo: ese giro no lo
    escribió para este documento, lo derivó el sistema. Se recorta y se avisa.
    """
    customer = make_customer(db)
    _con_receptores(db, customer)
    customer.issuer_activity = "OTRAS ACTIVIDADES ESPECIALIZADAS DE CONSTRUCCION"  # 48
    db.commit()
    s = _set(db, customer, "guias", "5038173")
    definicion = {"documents": [{"type": 52, "transfer_type": 5, "items": _item()}]}

    cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)

    receptor = cuerpo["documents"][0]["receiver"]
    assert len(receptor["activity"]) == 40
    assert receptor["activity"] == "OTRAS ACTIVIDADES ESPECIALIZADAS DE CONS"
    # El emisor conserva el suyo entero: ahí sí cabe.
    assert cuerpo["documents"][0]["issuer"]["activity"] == customer.issuer_activity
    # Y el recorte no es silencioso.
    assert any("se recortó a 40" in n for n in notas), notas


def test_un_giro_corto_no_genera_aviso(db):
    customer = make_customer(db)
    _con_receptores(db, customer)
    customer.issuer_activity = "CONSTRUCCION"
    db.commit()
    s = _set(db, customer, "guias", "5038173")
    definicion = {"documents": [{"type": 52, "transfer_type": 5, "items": _item()}]}

    cuerpo, notas = certification_fill.fill(db, customer, s, "issue-batch", definicion, hoy=HOY)

    assert cuerpo["documents"][0]["receiver"]["activity"] == "CONSTRUCCION"
    assert not any("recort" in n for n in notas), notas
