"""Segundo factor del portal.

Lo que se fija aquí es el comportamiento del que depende que nadie se quede
fuera del portal ni entre sin el segundo factor.
"""

import time

import pyotp
import pytest

from app.db.models import RecoveryCode, User
from tests.conftest import auth_header, make_user


def _siguiente(secret: str) -> str:
    """Código del paso siguiente.

    Activar gasta el código con el que se confirma —los TOTP son de un solo
    uso—, así que para entrar justo después hace falta el siguiente. La ventana
    de ±1 paso lo acepta.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


def _login(client, email="admin@dimabe.cl", password="secret", **extra):
    return client.post("/auth/login", json={"email": email, "password": password, **extra})


def _activar(client, db, headers) -> tuple[str, list[str]]:
    """Da de alta el segundo factor y devuelve (secreto, códigos)."""
    r = client.post("/auth/totp/setup", headers=headers)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = client.post(
        "/auth/totp/activate",
        json={"code": pyotp.TOTP(secret).now(), "password": "secret"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def test_sin_segundo_factor_el_login_no_cambia(client, db):
    make_user(db)
    assert _login(client).status_code == 200


def test_el_alta_no_activa_nada_hasta_confirmar(client, db):
    """Entre pedir el alta y confirmar el primer código el usuario debe poder
    seguir entrando: si no, cerrar la pestaña a medias lo deja fuera."""
    make_user(db)
    h = auth_header(client)
    assert client.post("/auth/totp/setup", headers=h).status_code == 200
    assert _login(client).status_code == 200  # todavía sin segundo factor


def test_una_vez_activo_la_contrasena_sola_no_basta(client, db):
    make_user(db)
    secret, _ = _activar(client, db, auth_header(client))

    r = _login(client)
    assert r.status_code == 401
    # Detalle distinguible para que la SPA sepa pedir el código.
    assert r.json()["detail"] == "totp_required"

    assert _login(client, totp_code="000000").status_code == 401
    assert _login(client, totp_code=_siguiente(secret)).status_code == 200


def test_el_secreto_no_se_guarda_en_claro(client, db):
    """Un secreto TOTP en claro es equivalente a la credencial."""
    make_user(db)
    secret, _ = _activar(client, db, auth_header(client))
    guardado = db.query(User).filter(User.email == "admin@dimabe.cl").one().totp_secret
    assert guardado and secret not in guardado


def test_codigo_de_recuperacion_entra_y_se_gasta(client, db):
    make_user(db)
    _, codes = _activar(client, db, auth_header(client))

    assert _login(client, recovery_code=codes[0]).status_code == 200
    # De un solo uso: el mismo código ya no vale.
    assert _login(client, recovery_code=codes[0]).status_code == 401
    # Los demás siguen sirviendo.
    assert _login(client, recovery_code=codes[1]).status_code == 200


def test_los_codigos_de_recuperacion_van_hasheados(client, db):
    make_user(db)
    _, codes = _activar(client, db, auth_header(client))
    guardados = [r.code_hash for r in db.query(RecoveryCode).all()]
    assert len(guardados) == 8
    assert all(c not in guardados for c in codes)


def test_apagarlo_exige_la_contrasena(client, db):
    """Desde una sesión robada no debe bastar un clic."""
    make_user(db)
    h = auth_header(client)
    secret, _ = _activar(client, db, h)

    assert (
        client.post("/auth/totp/disable", json={"password": "otra"}, headers=h).status_code == 401
    )
    assert (
        client.post("/auth/totp/disable", json={"password": "secret"}, headers=h).status_code == 204
    )
    assert _login(client).status_code == 200  # vuelve a entrar sin código


def test_un_superadmin_puede_resetear_el_de_otro(client, db):
    """Salida cuando alguien pierde el teléfono Y los códigos."""
    victima = make_user(db, "victima@dimabe.cl", "secret", "operator")
    hv = auth_header(client, "victima@dimabe.cl", "secret")
    _activar(client, db, hv)
    assert _login(client, "victima@dimabe.cl").status_code == 401

    make_user(db, "su@dimabe.cl", "secret", "superadmin")
    hs = auth_header(client, "su@dimabe.cl", "secret")
    assert client.post(f"/users/{victima.id}/totp/reset", headers=hs).status_code == 200
    assert _login(client, "victima@dimabe.cl").status_code == 200


def test_un_operador_no_puede_resetear_el_de_otro(client, db):
    otro = make_user(db, "otro@dimabe.cl", "secret", "operator")
    make_user(db, "op@dimabe.cl", "secret", "operator")
    h = auth_header(client, "op@dimabe.cl", "secret")
    assert client.post(f"/users/{otro.id}/totp/reset", headers=h).status_code == 403


def test_el_estado_se_puede_consultar(client, db):
    make_user(db)
    h = auth_header(client)
    assert client.get("/auth/totp", headers=h).json() == {
        "enabled": False,
        "recovery_codes_left": 0,
    }
    _, codes = _activar(client, db, h)
    assert client.get("/auth/totp", headers=h).json() == {
        "enabled": True,
        "recovery_codes_left": 8,
    }


@pytest.mark.parametrize("codigo", ["", "12345", "abcdef"])
def test_activar_con_un_codigo_invalido_no_activa(client, db, codigo):
    make_user(db)
    h = auth_header(client)
    client.post("/auth/totp/setup", headers=h)
    r = client.post("/auth/totp/activate", json={"code": codigo, "password": "secret"}, headers=h)
    assert r.status_code == 400
    assert _login(client).status_code == 200


def test_un_codigo_no_sirve_dos_veces(client, db):
    """Un TOTP es de un solo uso (RFC 6238 §5.2). Sin consumirlo, quien lo vea
    una vez —phishing, un hombro, el portapapeles— tiene minuto y medio de barra
    libre en vez de un único disparo."""
    make_user(db)
    secret, _ = _activar(client, db, auth_header(client))

    codigo = _siguiente(secret)
    assert _login(client, totp_code=codigo).status_code == 200
    assert _login(client, totp_code=codigo).status_code == 401
    assert _login(client, totp_code=codigo).status_code == 401


def test_un_codigo_anterior_al_ya_usado_tampoco_sirve(client, db):
    """La tolerancia de ±1 paso no debe convertirse en una puerta hacia atrás."""
    make_user(db)
    secret, _ = _activar(client, db, auth_header(client))
    totp = pyotp.TOTP(secret)

    assert _login(client, totp_code=_siguiente(secret)).status_code == 200
    # El del paso actual es anterior al que se acaba de gastar.
    assert _login(client, totp_code=totp.now()).status_code == 401


def test_activar_exige_la_contrasena(client, db):
    """Con sólo una cookie robada, un atacante daba de alta un segundo factor
    suyo sobre una cuenta que no lo tenía y dejaba fuera al titular."""
    make_user(db)
    h = auth_header(client)
    secret = client.post("/auth/totp/setup", headers=h).json()["secret"]

    r = client.post(
        "/auth/totp/activate",
        json={"code": pyotp.TOTP(secret).now(), "password": "no-es-la-suya"},
        headers=h,
    )
    assert r.status_code == 401
    # Y no quedó activado: el titular sigue entrando con su contraseña.
    assert _login(client).status_code == 200


def test_activar_y_desactivar_quedan_en_la_auditoria(client, db):
    from app.db.models import AdminAudit

    make_user(db)
    h = auth_header(client)
    _activar(client, db, h)
    client.post("/auth/totp/disable", json={"password": "secret"}, headers=h)

    acciones = [a.action for a in db.query(AdminAudit).all()]
    assert "user.totp_enable" in acciones
    assert "user.totp_disable" in acciones


def test_reactivar_tras_apagarlo_no_arrastra_el_corte(client, db):
    """El paso gastado pertenece al secreto viejo: si no se olvida, el secreto
    nuevo empezaría rechazando códigos válidos."""
    make_user(db)
    h = auth_header(client)
    _activar(client, db, h)
    client.post("/auth/totp/disable", json={"password": "secret"}, headers=h)

    secret2, _ = _activar(client, db, h)
    assert _login(client, totp_code=_siguiente(secret2)).status_code == 200
