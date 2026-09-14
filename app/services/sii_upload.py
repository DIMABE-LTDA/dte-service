"""Subida de archivos al SII (DTEUpload).

El mismo CGI recibe los sobres de documentos y los libros, así que el envío
vive acá y no dentro del servicio de DTE: el de libros necesita exactamente lo
mismo, y duplicarlo dejaría dos sitios donde recordar los requisitos del SII
(User-Agent, token, RUT de quien envía).
"""

from __future__ import annotations

from dte_chile.certificate import Certificate
from dte_chile.sii_client import Environment, SIIClient, SubmissionResult

from app.db.models import Customer
from app.services import certification_service


def upload(
    customer: Customer,
    cert: Certificate,
    xml: bytes,
    issuer_rut: str,
    timeout_s: int,
    *,
    capture: bool = True,
) -> SubmissionResult:
    """Sube el archivo al ambiente del cliente y devuelve el TrackID.

    Como es el paso obligado de todos los envíos —documentos, boletas y
    libros—, es también el único sitio donde hace falta enganchar el registro
    del expediente de certificación. Enchufarlo en cada emisor habría dejado
    ocho lugares donde acordarse.
    """
    client = SIIClient(cert, Environment[customer.environment.name], timeout=timeout_s)
    try:
        result = client.send_dte(xml, issuer_rut, cert.rut or issuer_rut)
    finally:
        client.session.close()  # liberar la sesión HTTP (no hay caché de cliente)
    # Después del envío: el TrackID ya existe y el folio ya se gastó. La captura
    # se traga sus propios errores para no convertir un envío bueno en un fallo.
    # ``capture=False`` cuando el expediente ya tiene la fila y sólo le falta el
    # TrackID: es el caso de reenviar un sobre guardado.
    if capture:
        certification_service.capture(customer, xml, result.track_id)
    return result
