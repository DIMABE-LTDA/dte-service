"""Demo local de una certificación: de los archivos del SII a los sobres, sin enviar nada.

Arma en una base de datos temporal un contribuyente con su configuración, carga
sus CAF, lee los archivos del set de pruebas con el mismo código que usa el
portal, emite los sets y compara el **contenido** de cada sobre con el que el SII
aprobó. Nada sale a la red.

    python scripts/certificacion_demo.py \\
        --set  SIISetDePruebas772621590.txt \\
        --boletas "Set Prueba BE.txt" \\
        --cliente tests/fixtures/certificacion/cliente-77262159-0.json \\
        --caf-dir C:/desarrollo/caf/77262159-0-certificacion \\
        --aprobados <carpeta con basico.xml, exenta.xml, …> \\
        --salida <carpeta donde dejar los sobres emitidos>

Firma con un certificado de prueba generado aquí, no con el del contribuyente:
las firmas no son contenido del caso y el comparador las excluye, así que no hace
falta tocar la clave privada de nadie para esta prueba.

Los libros de ventas y de guías se arman con los documentos «aceptados»: la demo
marca como aceptado cada sobre que emite, porque aquí no hay SII que responda.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def _entorno(db_path: Path) -> None:
    from cryptography.fernet import Fernet

    os.environ["DTE_DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ.setdefault("DTE_FERNET_KEYS", Fernet.generate_key().decode())
    os.environ.setdefault("DTE_ADMIN_API_KEY", "demo-admin-key-0123456789")
    os.environ.setdefault("DTE_JWT_SECRET", "demo-jwt-secret-at-least-32-bytes-long-000")


def _certificado_de_prueba(rut: str):
    """Certificado autofirmado con el RUT del firmante: sólo para firmar la demo."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from dte_chile.certificate import Certificate

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"DEMO {rut}")])
    ahora = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(days=1))
        .not_valid_after(ahora + dt.timedelta(days=30))
        .sign(clave, hashes.SHA256())
    )
    return Certificate(
        private_key_pem=clave.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        rut=rut,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--set", required=True, type=Path, help="archivo del set de pruebas del SII")
    ap.add_argument("--boletas", type=Path, help="archivo del set de boletas del SII")
    ap.add_argument(
        "--cliente", required=True, type=Path, help="JSON con emisor, resolución y receptores"
    )
    ap.add_argument("--caf-dir", required=True, type=Path, help="carpeta con los CAF (.xml)")
    ap.add_argument("--aprobados", type=Path, help="carpeta con los sobres aprobados, <kind>.xml")
    ap.add_argument("--salida", type=Path, help="carpeta donde dejar los sobres emitidos")
    args = ap.parse_args()

    trabajo = Path(tempfile.mkdtemp(prefix="cert-demo-"))
    _entorno(trabajo / "demo.db")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.db.models  # noqa: F401
    import app.db.session as db_session
    from app.db.base import Base
    from app.services import (
        certification_compare,
        certification_rehearsal,
        certification_sheet,
        customer_service,
    )

    motor = create_engine(os.environ["DTE_DATABASE_URL"])
    Base.metadata.create_all(motor)
    Sesion = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)
    db_session._engine, db_session.SessionLocal = motor, Sesion
    db = Sesion()

    config = json.loads(args.cliente.read_text(encoding="utf-8"))
    print(f"· base de datos temporal: {trabajo / 'demo.db'}")

    cliente = customer_service.create_customer(
        db,
        type(
            "Datos",
            (),
            {
                "name": config["name"],
                "key": "demo",
                "rut": config["rut"],
                "environment": "CERTIFICATION",
                "resolution_number": config["resolution_number"],
                "resolution_date": dt.date.fromisoformat(config["resolution_date"]),
            },
        )(),
    )
    customer_service.set_issuer(cliente, config["issuer"])
    cliente.cert_receivers = config["receivers"]
    db.commit()
    print(
        f"· cliente {cliente.name} ({cliente.rut})"
        f" con {len(config['receivers'])} receptores de prueba"
    )

    cafs = sorted(args.caf_dir.glob("*.xml"))
    for caf in cafs:
        customer_service.add_caf(db, cliente, base64.b64encode(caf.read_bytes()).decode())
    print(f"· {len(cafs)} CAF cargados desde {args.caf_dir}")

    hoja = certification_sheet.parse(args.set.read_bytes())
    if args.boletas:
        hoja.sets.update(certification_sheet.parse(args.boletas.read_bytes()).sets)
    certification_rehearsal.cargar(db, cliente, hoja)
    print(f"· {len(hoja.sets)} sets leídos de los archivos del SII y cargados")

    cert = _certificado_de_prueba(config["signer_rut"])
    salida = args.salida or (trabajo / "sobres")
    salida.mkdir(parents=True, exist_ok=True)

    emitidos = certification_rehearsal.emitir_todo(db, cliente, cert)
    for kind in certification_rehearsal.ORDEN:
        r = emitidos.get(kind)
        if r is None:
            print(f"  – {kind}: no está en los archivos")
        elif r.error:
            print(f"  ✗ {kind}: no se pudo emitir — {r.error}")
        else:
            (salida / f"{kind}.xml").write_bytes(r.xml)
            forma = "esquema y firmas OK" if not r.forma else "; ".join(r.forma)
            print(
                f"  {'✓' if not r.forma else '✗'} {kind}: emitido ({len(r.xml):,} bytes) · {forma}"
            )

    fallas = sum(1 for r in emitidos.values() if r.error or r.forma)
    if not args.aprobados:
        print(f"\nSobres en {salida}. Sin --aprobados no hay con qué comparar.")
        return 1 if fallas else 0

    print("\nComparación de contenido contra lo aprobado por el SII")
    print("(sin folios, fechas, período, timbre ni firmas)\n")
    for kind in certification_rehearsal.ORDEN:
        r = emitidos.get(kind)
        if r is None or r.error:
            continue
        aprobado = args.aprobados / f"{kind}.xml"
        if not aprobado.exists():
            print(f"· {kind}: sin sobre aprobado con qué comparar (sólo se validó su forma)")
            continue
        campos = len(certification_compare.contenido(aprobado.read_bytes()))
        difs = certification_compare.comparar(aprobado.read_bytes(), r.xml)
        aceptadas, inesperadas = certification_compare.clasificar(kind, difs)
        if inesperadas:
            fallas += 1
            print(f"✗ {kind}: {len(inesperadas)} diferencia(s) inesperada(s) en {campos} campos")
            for d in inesperadas:
                print(f"    {d}")
        elif aceptadas:
            print(
                f"✓ {kind}: igual salvo {len(aceptadas)} diferencia(s) conocida(s)"
                f" ({campos} campos)"
            )
        else:
            print(f"✓ {kind}: idéntico ({campos} campos)")
        motivos: dict[str, int] = {}
        for _d, motivo in aceptadas:
            motivos[motivo] = motivos.get(motivo, 0) + 1
        for motivo, cuantas in motivos.items():
            print(f"    · {cuantas} × {motivo}")

    print(f"\nSobres emitidos en {salida}")
    print(
        "RESULTADO:", "con diferencias inesperadas" if fallas else "todo coincide con lo aprobado"
    )
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
