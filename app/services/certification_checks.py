"""Verificación de configuración antes de emitir un set de certificación.

Existe porque la primera certificación que pasó por aquí perdió una semana en
cosas que la plataforma podía haber comprobado sola: un certificado autofirmado
que no autentica ante el SII, CAF sin la firma del Servicio, punteros de folio
fuera del rango del CAF, y un timbre que se firmaba con los saltos de línea del
CAF dentro —32 documentos rechazados de una vez—. Cada una de esas se descubrió
a mano, leyendo XML dentro de un contenedor. Con otro contribuyente no va a
haber nadie haciendo eso.

Todo lo que corre aquí es local: no gasta folios ni habla con el SII. La prueba
de autenticación contra Maullín va aparte (`sii_auth`) porque sí sale a la red
y conviene que la dispare una persona.

Cada comprobación dice qué pasa y **qué hacer**, no sólo si está bien o mal.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from collections import Counter
from types import SimpleNamespace

from lxml import etree

from app.core import crypto
from app.db.models import (
    Caf,
    CertificationDefinition,
    CertificationSet,
    CertificationSubmission,
    Customer,
    CustomerCertificate,
    FolioPointer,
    SiiEnvironment,
)
from app.services.certification_catalog import SET_TYPES

#: Tipos que el SII no timbra con CAF propio en estos sets: los libros.
_LIBROS = {"libro_guias", "libro_compras", "libro_ventas"}

#: Un CAF del SII firma su DA con RSA de 512 bits: 64 bytes de firma. Uno más
#: corto no es un CAF del Servicio — los de prueba que se cargaron aquí traían
#: una firma de relleno de un byte.
_FRMA_MIN_BYTES = 64


def _check(key: str, label: str, state: str, detail: str, fix: str = "") -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail, "fix": fix}


def _peor(checks: list[dict]) -> str:
    estados = {c["state"] for c in checks}
    for s in ("error", "atencion"):
        if s in estados:
            return s
    return "ok"


# --------------------------------------------------------------------------- #
#  Emisor
# --------------------------------------------------------------------------- #


def _emisor(customer: Customer) -> list[dict]:
    salida = []
    if customer.environment == SiiEnvironment.CERTIFICATION:
        salida.append(_check("ambiente", "Ambiente", "ok", "certificación (Maullín)"))
    else:
        salida.append(
            _check(
                "ambiente",
                "Ambiente",
                "error",
                "el cliente está en PRODUCCIÓN: los sets de prueba se envían a Maullín",
                "Usa la ficha de certificación de esta empresa, no la de producción.",
            )
        )

    salida.append(_perfil_emisor(customer))

    # «Indique el número y fecha que está publicado en los datos de su empresa
    # en el ambiente de certificación.» No hay un valor universal: lo que se
    # puede detectar es que siga el que pone el sistema por defecto.
    fecha, numero = customer.resolution_date, customer.resolution_number
    donde = (
        "Cópialos de los datos de tu empresa en el ambiente de certificación (Maullín)"
        " y guárdalos en la ficha del cliente o desde Odoo: van en la carátula de todos los envíos."
    )
    if fecha > dt.date.today():
        salida.append(
            _check(
                "resolucion", "Resolución", "error", f"fecha {fecha:%d-%m-%Y} en el futuro", donde
            )
        )
    elif fecha == _FECHA_POR_DEFECTO:
        salida.append(
            _check(
                "resolucion",
                "Resolución",
                "atencion",
                f"número {numero} del {fecha:%d-%m-%Y}: es el valor por defecto del sistema,"
                " no el de tu empresa",
                donde,
            )
        )
    else:
        salida.append(
            _check(
                "resolucion",
                "Resolución",
                "ok",
                f"número {numero} del {fecha:%d-%m-%Y}"
                " · debe coincidir con la publicada en Maullín",
            )
        )
    return salida


#: Lo que el esquema pone si nadie dice otra cosa. Es de un ejemplo, no de
#: ningún contribuyente: que siga ahí es señal de que nadie lo configuró.
_FECHA_POR_DEFECTO = dt.date(2014, 8, 22)


def _perfil_emisor(customer: Customer) -> dict:
    """Los datos del emisor que el sistema pone en cada documento."""
    from app.services import customer_service

    faltan = customer_service.issuer_missing(customer)
    if faltan:
        return _check(
            "emisor",
            "Datos del emisor",
            "error",
            "faltan: " + ", ".join(faltan),
            "Complétalos en la ficha del cliente («Datos del emisor») o desde Odoo."
            " Van en el encabezado de cada documento.",
        )
    p = customer_service.issuer_profile(customer)
    return _check(
        "emisor",
        "Datos del emisor",
        "ok",
        f"{p['legal_name']} · ACTECO {p['economic_activity']} · {p['address']}, {p['commune']}",
    )


# --------------------------------------------------------------------------- #
#  Certificado
# --------------------------------------------------------------------------- #


def _certificado(db, customer: Customer) -> tuple[list[dict], object | None]:
    """Devuelve las comprobaciones y el certificado resuelto, para reusarlo."""
    from cryptography import x509

    from app.services import certificate_service

    hoy = dt.date.today()
    fila = (
        db.query(CustomerCertificate)
        .filter(
            CustomerCertificate.customer_id == customer.id,
            CustomerCertificate.due_date >= hoy,
        )
        .order_by(CustomerCertificate.created_at.desc())
        .first()
    )
    if fila is None:
        return [
            _check(
                "certificado",
                "Certificado de firma",
                "error",
                "no hay un certificado vigente",
                "Sube el .pfx del firmante en la ficha del cliente.",
            )
        ], None

    try:
        cert = certificate_service.resolve_certificate(db, customer)
    except Exception:  # noqa: BLE001 - un .pfx ilegible es un resultado, no un fallo
        return [
            _check(
                "certificado",
                "Certificado de firma",
                "error",
                "el .pfx no se pudo abrir",
                "Vuelve a subirlo y revisa la contraseña.",
            )
        ], None

    if cert is None:
        return [
            _check(
                "certificado",
                "Certificado de firma",
                "error",
                "no se pudo resolver el certificado vigente",
                "Vuelve a subir el .pfx en la ficha del cliente.",
            )
        ], None

    quien = f"{fila.holder or 'titular desconocido'} · RUT {fila.rut or '—'}"
    salida = []
    autofirmado = None
    try:
        x = x509.load_pem_x509_certificate(cert.cert_pem)
        autofirmado = x.subject == x.issuer
    except Exception:  # noqa: BLE001
        pass

    if autofirmado:
        salida.append(
            _check(
                "certificado",
                "Certificado de firma",
                "error",
                f"{quien} — es AUTOFIRMADO",
                "Un certificado autofirmado firma bien pero no autentica ante el SII:"
                " el envío vuelve con «rechazó la semilla firmada». Sube el de la"
                " entidad acreditada (e-certchile, Acepta, Certinet…).",
            )
        )
    elif not fila.rut:
        salida.append(
            _check(
                "certificado",
                "Certificado de firma",
                "atencion",
                f"{quien} — no se pudo leer el RUT del firmante",
                "Sin el RUT no se puede comprobar a quién darle el permiso «Enviar"
                " Doctos» en Maullín.",
            )
        )
    else:
        dias = (fila.due_date - hoy).days
        estado = "atencion" if dias < 30 else "ok"
        salida.append(
            _check(
                "certificado",
                "Certificado de firma",
                estado,
                f"{quien} · emitido por {fila.issuer or '—'} · vence en {dias} días",
                "Renuévalo antes de que venza: la certificación puede durar semanas."
                if estado == "atencion"
                else "",
            )
        )
        salida.append(_permiso(db, customer, fila.rut, fila.thumbprint))
    return salida, cert


def _permiso(db, customer: Customer, rut: str, huella: str | None) -> dict:
    """El permiso «Enviar Doctos» no se puede consultar: se deduce de envíos.

    Un RFR sólo dice algo del permiso si el sobre se firmó con el certificado
    ACTUAL. El primer RFR de esta plataforma vino de un certificado autofirmado
    de prueba; contarlo contra el permiso habría dado un error falso justo
    cuando el permiso ya estaba bien.
    """
    envios = (
        db.query(CertificationSubmission)
        .filter(
            CertificationSubmission.customer_id == customer.id,
            CertificationSubmission.sii_state.isnot(None),
        )
        .order_by(CertificationSubmission.sent_at.desc().nullslast())
        .all()
    )
    donde = (
        "Mi SII → Administración de Empresa Autorizada → Mantención de Usuarios, EN EL"
        " AMBIENTE DE CERTIFICACIÓN: el permiso de producción no vale ahí."
    )
    propios = [e for e in envios if huella and e.signed_thumbprint == huella]
    if propios and propios[0].sii_state == "RFR":
        return _check(
            "permiso",
            "Permiso de envío",
            "error",
            f"el último envío con este certificado volvió RFR: lo habitual es que el"
            f" RUT {rut} no tenga «Enviar Doctos» en Maullín",
            f"Habilítalo en {donde}",
        )
    if propios and propios[0].sii_state in ("EPR", "LOK"):
        return _check(
            "permiso",
            "Permiso de envío",
            "ok",
            "el SII procesó el último envío firmado con este certificado",
        )
    if any(e.sii_state in ("EPR", "LOK") for e in envios):
        return _check(
            "permiso",
            "Permiso de envío",
            "ok",
            "el SII ya procesó envíos de este cliente: el permiso de envío existe",
        )
    if any(e.sii_state == "RFR" for e in envios):
        return _check(
            "permiso",
            "Permiso de envío",
            "atencion",
            "hubo un RFR, pero firmado con otro certificado: no dice nada del actual",
            f"Comprueba el permiso del RUT {rut} en {donde}",
        )
    return _check(
        "permiso",
        "Permiso de envío",
        "atencion",
        f"sin envíos todavía: no hay forma de saber si el RUT {rut} tiene «Enviar Doctos»",
        f"Compruébalo en {donde} La prueba de conexión confirma que el certificado"
        " autentica, pero no el permiso.",
    )


# --------------------------------------------------------------------------- #
#  CAF y folios
# --------------------------------------------------------------------------- #


def _txt(nodo, nombre: str) -> str | None:
    hijo = nodo.find(f".//{nombre}")
    return hijo.text.strip() if hijo is not None and hijo.text else None


def _necesarios(definiciones: dict[str, CertificationDefinition]) -> Counter:
    """Cuántos folios de cada tipo consume emitir todos los sets una vez."""
    cuenta: Counter = Counter()
    for kind, d in definiciones.items():
        if kind in _LIBROS:
            continue
        for doc in (d.payload or {}).get("documents", []):
            tipo = doc.get("type") or (43 if d.endpoint == "issue-settlement-batch" else None)
            if tipo:
                cuenta[int(tipo)] += 1
    return cuenta


def _caf(db, customer: Customer, necesarios: Counter) -> tuple[list[dict], dict]:
    """Una comprobación por tipo de documento que piden los sets.

    Devuelve además los CAF cargados y válidos, por tipo, para la prueba de
    timbre y la comprobación de fechas.
    """
    from dte_chile.caf import load_caf_bytes

    tipos = sorted({t for s in SET_TYPES for t in s.doc_types} | set(necesarios))
    punteros = {
        p.doc_type: p.last_folio
        for p in db.query(FolioPointer).filter(FolioPointer.customer_id == customer.id)
    }
    filas = (
        db.query(Caf)
        .filter(Caf.customer_id == customer.id, Caf.exhausted.is_(False))
        .order_by(Caf.doc_type, Caf.folio_from)
        .all()
    )
    por_tipo: dict[int, list[Caf]] = {}
    for f in filas:
        por_tipo.setdefault(f.doc_type, []).append(f)

    salida, validos = [], {}
    for tipo in tipos:
        etiqueta = f"CAF tipo {tipo}"
        cafs = por_tipo.get(tipo, [])
        if not cafs:
            salida.append(
                _check(
                    f"caf_{tipo}",
                    etiqueta,
                    "error",
                    "no hay un CAF vigente",
                    f"Descarga en Maullín el CAF del tipo {tipo} y súbelo en la ficha.",
                )
            )
            continue

        fila = cafs[0]
        try:
            caf = load_caf_bytes(crypto.decrypt(fila.xml_encrypted))
        except Exception:  # noqa: BLE001
            salida.append(
                _check(
                    f"caf_{tipo}",
                    etiqueta,
                    "error",
                    "el archivo no se pudo leer",
                    "Vuelve a descargarlo desde Maullín.",
                )
            )
            continue

        nodo = caf.caf_element
        problemas = []
        if (_txt(nodo, "RE") or "") != customer.rut:
            problemas.append(f"es de otro RUT ({_txt(nodo, 'RE')})")
        try:
            firma = base64.b64decode(_txt(nodo, "FRMA") or "")
        except ValueError:
            firma = b""
        if len(firma) < _FRMA_MIN_BYTES:
            problemas.append("no trae la firma del SII: no es un CAF emitido por el Servicio")
        if not _par_de_claves_coincide(caf):
            problemas.append("su clave privada no corresponde a su clave pública")
        if problemas:
            salida.append(
                _check(
                    f"caf_{tipo}",
                    etiqueta,
                    "error",
                    "; ".join(problemas),
                    f"Descarga de nuevo el CAF del tipo {tipo} en Maullín y reemplázalo.",
                )
            )
            continue

        ultimo = punteros.get(tipo, 0)
        quedan = sum(max(0, c.folio_to - max(ultimo, c.folio_from - 1)) for c in cafs)
        falta = necesarios.get(tipo, 0)
        rango = f"folios {fila.folio_from}-{cafs[-1].folio_to}"
        detalle = f"{rango} · siguiente {ultimo + 1} · quedan {quedan}" + (
            f" · un intento usa {falta}" if falta else ""
        )
        if falta and quedan < falta:
            estado, arreglo = (
                "error",
                (f"No alcanzan para emitir los sets una vez. Pide más folios del tipo {tipo}."),
            )
        elif falta and quedan < 2 * falta:
            estado, arreglo = (
                "atencion",
                ("Alcanza para un solo intento. Un rechazo más y habrá que pedir folios."),
            )
        else:
            estado, arreglo = "ok", ""
        salida.append(_check(f"caf_{tipo}", etiqueta, estado, detalle, arreglo))
        validos[tipo] = (caf, dt.date.fromisoformat(_txt(nodo, "FA") or "1900-01-01"))
    return salida, validos


def _par_de_claves_coincide(caf) -> bool:
    """La clave privada del CAF (RSASK) corresponde a su clave pública (RSAPK)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    try:
        privada = serialization.load_pem_private_key(
            caf.rsa_private_key_pem.encode("latin-1"), password=None
        )
        m = int.from_bytes(base64.b64decode(_txt(caf.caf_element, "M") or ""), "big")
        e = int.from_bytes(base64.b64decode(_txt(caf.caf_element, "E") or ""), "big")
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(privada, rsa.RSAPrivateKey):
        return False  # el SII firma los CAF con RSA: otra clave no es de un CAF
    numeros = privada.public_key().public_numbers()
    return numeros.n == m and numeros.e == e


# --------------------------------------------------------------------------- #
#  Definiciones de los sets
# --------------------------------------------------------------------------- #


def _documentos_aceptados(db, customer: Customer) -> set[tuple[int, int]]:
    """(tipo, folio) de los documentos que el SII aceptó en algún envío."""
    from app.db.models import CertificationDocument
    from app.services.certification_service import entregado

    envios = (
        db.query(CertificationSubmission)
        .filter(CertificationSubmission.customer_id == customer.id)
        .all()
    )
    salida = set()
    for e in envios:
        if not entregado(e):
            continue
        for d in db.query(CertificationDocument).filter_by(submission_id=e.id):
            salida.add((d.doc_type, d.folio))
    return salida


def _cargar(db, customer: Customer) -> tuple[dict, dict]:
    """Los sets dados de alta y sus definiciones, por kind."""
    sets = {
        s.kind: s
        for s in db.query(CertificationSet).filter(CertificationSet.customer_id == customer.id)
    }
    definiciones = {}
    for kind, s in sets.items():
        d = db.query(CertificationDefinition).filter_by(set_id=s.id).one_or_none()
        if d is not None:
            definiciones[kind] = d
    return sets, definiciones


def _definiciones(db, customer: Customer, sets: dict, definiciones: dict) -> list[dict]:
    """Que cada set esté definido y que lo que el sistema completa se pueda completar.

    Ya no se revisa el emisor ni la fecha de cada definición: los pone el
    sistema al emitir y lo que traiga la definición se ignora. Lo que sí puede
    faltar es lo que el sistema necesita para completarlos.
    """
    from app.services import certification_fill

    salida = []
    for tipo_set in SET_TYPES:
        kind, etiqueta = tipo_set.kind, tipo_set.label
        s = sets.get(kind)
        if s is None:
            # Un set que el SII no numera no se da de alta copiando un número
            # que no existe: entra al expediente al cargar las definiciones.
            # Y no bloquea la emisión de los demás, que son independientes.
            sin_numero = not tipo_set.declarable
            salida.append(
                _check(
                    f"def_{kind}",
                    etiqueta,
                    "atencion" if sin_numero else "error",
                    "el set no está en el expediente"
                    if sin_numero
                    else "el set no está dado de alta",
                    "Cárgalo con «Cargar los sets del contribuyente»: este set no lleva"
                    " número de atención."
                    if sin_numero
                    else "Copia su número de atención desde Mi SII en el expediente.",
                )
            )
            continue
        d = definiciones.get(kind)
        if d is None:
            salida.append(
                _check(
                    f"def_{kind}",
                    etiqueta,
                    "error",
                    f"set {s.code} sin definir qué emite",
                    "Ábrelo con «Editar definición», o clónalo de otro cliente ya certificado.",
                )
            )
            continue

        if kind in certification_fill.GENERATED_BOOKS:
            # Sus líneas salen de los documentos aceptados: sin ellos, no hay libro.
            lineas, avisos = certification_fill.book_lines(db, customer, kind)
            if lineas:
                salida.append(
                    _check(
                        f"def_{kind}",
                        etiqueta,
                        "atencion" if avisos else "ok",
                        f"set {s.code} · {len(lineas)} línea(s) de documentos aceptados"
                        + (f" · {avisos[0]}" if avisos else ""),
                    )
                )
            else:
                salida.append(
                    _check(
                        f"def_{kind}",
                        etiqueta,
                        "atencion",
                        f"set {s.code} · se arma con los documentos aceptados de sus sets;"
                        " todavía no hay ninguno",
                        "Envía primero los sets de documentos. El libro se completa solo.",
                    )
                )
            continue

        docs = (d.payload or {}).get("documents", [])
        n = len(docs) or len((d.payload or {}).get("lines", []))
        unidad = "línea(s)" if kind in _LIBROS else "documento(s)"
        salida.append(
            _check(
                f"def_{kind}",
                etiqueta,
                "ok",
                f"set {s.code} · {n} {unidad}"
                + (f" · casos {s.code}-1 a {s.code}-{len(docs)}" if docs else ""),
            )
        )
    return salida


#: IndServicio 4: servicios de hotelería en una factura de exportación.
_HOTELERIA = 4
#: TpoDocRef 813: pasaporte del cliente extranjero.
_PASAPORTE = 813


def _contenido(definiciones: dict) -> list[dict]:
    """Errores de contenido que el SII rechazó en la revisión del set.

    Ninguno lo detecta el XSD: el documento es válido y el SII lo acepta en la
    validación del sobre. Lo rechaza después, al compararlo con el enunciado del
    caso, cuando los folios ya están gastados. Cada regla es un rechazo real del
    16-09-2026.
    """
    salida = []

    liq = definiciones.get("liquidacion")
    for n, doc in enumerate(((liq.payload or {}).get("documents") or []) if liq else [], 1):
        for linea in doc.get("lines") or []:
            if (
                "ANTICIPO" in str(linea.get("name", "")).upper()
                and str(linea.get("liquidated_type")) != "99"
            ):
                salida.append(
                    _check(
                        "contenido_liquidacion_anticipo",
                        f"Liquidación · caso {n}",
                        "error",
                        f"«{linea.get('name')}» va con tipo {linea.get('liquidated_type')}",
                        "Un anticipo no liquida ningún documento: el formato del SII pide"
                        " «99 en caso de anticipo u otras transacciones».",
                    )
                )

    for kind in ("exportacion_1", "exportacion_2"):
        d = definiciones.get(kind)
        for n, doc in enumerate(((d.payload or {}).get("documents") or []) if d else [], 1):
            etiqueta = f"Exportación ({kind[-1]}) · caso {n}"
            for item in doc.get("items") or []:
                if item.get("quantity") is None or item.get("unit_price") is None:
                    salida.append(
                        _check(
                            f"contenido_{kind}_cantidad",
                            etiqueta,
                            "error",
                            f"«{item.get('name')}» no trae cantidad y precio unitario",
                            "El SII responde «Los Datos de la Linea … No Cuadran con lo"
                            " Especificado». Si la hoja del set sólo da el valor de la"
                            " línea, usa cantidad 1 y ese valor como precio.",
                        )
                    )
            refs = doc.get("references") or []
            if doc.get("service_indicator") == _HOTELERIA and not any(
                r.get("doc_type") == _PASAPORTE for r in refs
            ):
                salida.append(
                    _check(
                        f"contenido_{kind}_pasaporte",
                        etiqueta,
                        "error",
                        "factura de hotelería sin referencia al pasaporte del cliente",
                        "El SII exige una 2ª línea de referencia: tipo 813 (pasaporte),"
                        " además de la del caso del set.",
                    )
                )
    return salida


def _receptores(customer: Customer, definiciones: dict) -> dict:
    """Clientes reales para las facturas del set, distintos entre sí.

    El instructivo del SII: «Agregue un Rut receptor de un cliente existente.
    Utilice RUT distintos para las distintas facturas». Hacen falta tantos como
    facturas y guías de venta tenga el set que más traiga.
    """
    from app.services.certification_fill import _OWN_CUSTOMER_DOCS, SII_RUT

    maximo = 0
    for d in definiciones.values():
        n = sum(
            1
            for doc in (d.payload or {}).get("documents", [])
            if doc.get("type") in _OWN_CUSTOMER_DOCS
            and doc.get("transfer_type") != 5
            and (doc.get("receiver") or {}).get("rut") in (SII_RUT, None)
        )
        maximo = max(maximo, n)
    lista = [r for r in (customer.cert_receivers or []) if r.get("rut")]
    donde = "Agrégalos en «Receptores de prueba», en el expediente."
    if not maximo:
        return _check("receptores", "Receptores de prueba", "ok", "ningún set los necesita")
    if not lista:
        return _check(
            "receptores",
            "Receptores de prueba",
            "atencion",
            f"sin configurar: las facturas irían al RUT del propio SII. Hacen falta {maximo}",
            "El instructivo pide RUT de clientes existentes y distintos por factura. " + donde,
        )
    if len(lista) < maximo:
        return _check(
            "receptores",
            "Receptores de prueba",
            "atencion",
            f"{len(lista)} de {maximo}: algunos se repetirían en un mismo set",
            "El instructivo pide un RUT distinto por factura. " + donde,
        )
    return _check(
        "receptores",
        "Receptores de prueba",
        "ok",
        f"{len(lista)} cliente(s): " + ", ".join(r["rut"] for r in lista[:5]),
    )


# --------------------------------------------------------------------------- #
#  Prueba de firma, sin gastar folios
# --------------------------------------------------------------------------- #


def _timbres(customer: Customer, validos: dict) -> list[dict]:
    """Firma un timbre de prueba con cada CAF y lo verifica como el SII.

    Usa el último folio del rango sin asignarlo: el puntero no se mueve, así que
    no se gasta nada. Comprueba que el DD vaya plano y que su FRMT valide con la
    clave pública del propio CAF. Es la prueba que habría detectado los 32
    rechazos antes del primer envío.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from dte_chile.ted import build_ted, dd_bytes

    malos, bien = [], []
    for tipo, (caf, fa) in sorted(validos.items()):
        doc = SimpleNamespace(
            type=tipo,
            folio=caf.folio_to,
            issue_date=max(fa, dt.date.today()),
            issuer=SimpleNamespace(rut=SimpleNamespace(value=customer.rut)),
            receiver=SimpleNamespace(
                rut=SimpleNamespace(value="60803000-K"),
                business_name="SERVICIO DE IMPUESTOS INTERNOS",
            ),
            total_amount=1190,
            items=[SimpleNamespace(name="PRUEBA DE TIMBRE")],
        )
        try:
            ted = build_ted(doc, caf, dt.datetime.now().replace(microsecond=0))
            datos = dd_bytes(ted.find("DD"))
            nodo = caf.caf_element
            m = int.from_bytes(base64.b64decode(_txt(nodo, "M") or ""), "big")
            e = int.from_bytes(base64.b64decode(_txt(nodo, "E") or ""), "big")
            publica = rsa.RSAPublicNumbers(e, m).public_key()
            publica.verify(
                base64.b64decode(ted.find("FRMT").text),
                datos,
                padding.PKCS1v15(),
                hashes.SHA1(),
            )
            if re.search(rb">\s+<", datos):
                malos.append(f"{tipo} (DD con espacios entre etiquetas)")
            else:
                bien.append(tipo)
        except (InvalidSignature, ValueError, TypeError) as ex:
            malos.append(f"{tipo} ({type(ex).__name__})")

    if not validos:
        return [
            _check(
                "timbre",
                "Timbre",
                "error",
                "no hay CAF válidos con los que probar",
                "Resuelve primero los CAF.",
            )
        ]
    if malos:
        return [
            _check(
                "timbre",
                "Timbre",
                "error",
                "no valida en: " + ", ".join(malos),
                "El SII rechazaría esos documentos. No emitas hasta resolverlo.",
            )
        ]
    return [
        _check(
            "timbre",
            "Timbre",
            "ok",
            f"firmado y verificado con la clave de cada CAF: tipos {', '.join(map(str, bien))}",
        )
    ]


def _firma_documento(customer: Customer, cert, validos: dict) -> list[dict]:
    """Arma un documento y su sobre con el certificado real y verifica las firmas.

    Es lo que el SII comprueba antes de mirar el contenido. Tampoco gasta folios:
    el documento se construye en memoria y se descarta.
    """
    if cert is None or 33 not in validos:
        return []
    try:
        from dte_chile.document_types import DTEType
        from dte_chile.envelope import Cover, build_envelope, serialize
        from dte_chile.models import DTE, Issuer, Item, Receiver
        from dte_chile.signer import sign_document, verify_signatures
        from dte_chile.xml_builder import build_document
    except ImportError:
        return [
            _check(
                "firma",
                "Firma del documento",
                "atencion",
                "no se pudo verificar: falta xmlsec en el servidor",
            )
        ]

    caf, fa = validos[33]
    try:
        ts = dt.datetime.now().replace(microsecond=0)
        dte = DTE(
            type=DTEType.AFFECTED_INVOICE,
            folio=caf.folio_to,
            issue_date=max(fa, dt.date.today()),
            issuer=Issuer(
                rut=customer.rut,
                business_name=customer.name[:100],
                activity="PRUEBA",
                economic_activity=479100,
                address="PRUEBA",
                commune="SANTIAGO",
                city="SANTIAGO",
            ),
            receiver=Receiver(
                rut="60803000-K",
                business_name="SERVICIO DE IMPUESTOS INTERNOS",
                activity="ADMINISTRACION PUBLICA",
                address="TEATINOS 120",
                commune="SANTIAGO",
            ),
            items=[Item("PRUEBA DE FIRMA", quantity=1, unit_price=1000)],
        )
        firmado = sign_document(build_document(dte, caf, ts), cert)
        cover = Cover(
            issuer_rut=customer.rut,
            sender_rut=cert.rut or customer.rut,
            resolution_date=customer.resolution_date,
            resolution_number=customer.resolution_number,
            subtotals=[(33, 1)],
        )
        raiz = etree.fromstring(serialize(build_envelope([firmado], cover, cert, ts)))
    except Exception as ex:  # noqa: BLE001
        return [
            _check(
                "firma",
                "Firma del documento",
                "error",
                f"no se pudo armar un documento de prueba: {ex}",
            )
        ]

    # La verificación la hace el motor, que sabe con qué contexto corresponde
    # comprobar cada firma: la de un <DTE> con ese <DTE> aislado —que es como lo
    # lee el SII— y la del sobre con el árbol entero. Aquí había una copia de esa
    # lógica que verificaba TODO contra el sobre completo, y por eso daba por
    # buenos documentos que el SII rechazaba con «Firma DTE Incorrecta».
    resultados = verify_signatures(raiz)
    validas = sum(resultados)
    firmas = resultados
    if firmas and validas == len(firmas):
        return [
            _check(
                "firma",
                "Firma del documento",
                "ok",
                f"documento y sobre firmados y verificados ({validas} de {len(firmas)})",
            )
        ]
    return [
        _check(
            "firma",
            "Firma del documento",
            "error",
            f"{len(firmas) - validas} de {len(firmas)} firmas no validan",
            "El SII rechazaría el sobre entero. Revisa el certificado.",
        )
    ]


# --------------------------------------------------------------------------- #
#  Todo junto
# --------------------------------------------------------------------------- #


def run(db, customer: Customer) -> dict:
    """Todas las comprobaciones, agrupadas, con un veredicto global."""
    grupos: list[dict] = []

    grupos.append({"key": "emisor", "label": "Emisor", "checks": _emisor(customer)})

    certificado, cert = _certificado(db, customer)
    grupos.append({"key": "certificado", "label": "Certificado", "checks": certificado})

    sets, definiciones = _cargar(db, customer)

    cafs, validos = _caf(db, customer, _necesarios(definiciones))
    grupos.append({"key": "caf", "label": "CAF y folios", "checks": cafs})

    defs = (
        [_receptores(customer, definiciones)]
        + _definiciones(db, customer, sets, definiciones)
        + _contenido(definiciones)
    )
    grupos.append({"key": "definiciones", "label": "Qué emite cada set", "checks": defs})

    prueba = _timbres(customer, validos) + _firma_documento(customer, cert, validos)
    grupos.append({"key": "prueba", "label": "Prueba de firma", "checks": prueba})

    for g in grupos:
        g["state"] = _peor(g["checks"])
    errores = sum(1 for g in grupos for c in g["checks"] if c["state"] == "error")
    avisos = sum(1 for g in grupos for c in g["checks"] if c["state"] == "atencion")
    return {
        "ready": errores == 0,
        "errors": errores,
        "warnings": avisos,
        "checked_at": dt.datetime.now().replace(microsecond=0),
        "groups": grupos,
    }


def sii_auth(customer: Customer, cert, timeout_s: int) -> dict:
    """Pide un token a Maullín con el certificado del cliente.

    Es la única comprobación que sale a la red, por eso va aparte y la dispara
    una persona. Confirma que el certificado autentica; NO confirma el permiso
    «Enviar Doctos», que el SII sólo revisa al recibir un envío.
    """
    from dte_chile.sii_client import Environment, SIIClient

    cli = SIIClient(cert, Environment[customer.environment.name], timeout=timeout_s)
    try:
        cli.authenticate()
        return _check(
            "sii",
            "Conexión con el SII",
            "ok",
            "Maullín entregó un token: el certificado autentica",
            "Esto no prueba el permiso «Enviar Doctos»; el SII lo revisa al recibir el envío.",
        )
    except Exception as ex:  # noqa: BLE001 - el motivo es lo que se muestra
        return _check(
            "sii",
            "Conexión con el SII",
            "error",
            str(ex)[:300],
            "Si dice «rechazó la semilla firmada», el certificado no es de una entidad"
            " acreditada o el RUT no tiene permiso en este ambiente.",
        )
    finally:
        cli.session.close()
