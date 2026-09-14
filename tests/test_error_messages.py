"""Los mensajes de error no filtran lo que el operador escribió."""

from tests.conftest import auth_header, make_user


def _admin(client, db):
    make_user(db, "jefe@dimabe.cl", "secret", "superadmin")
    return auth_header(client, "jefe@dimabe.cl", "secret")


def test_un_error_de_validacion_no_repite_el_valor_recibido(client, db):
    """`str(RequestValidationError)` trae el valor en `input`.

    Creando un usuario con una contraseña corta, ese mensaje llegaba entero a la
    pantalla: mostraba **la contraseña en claro** y la ruta del archivo del
    servidor, `/srv/app/routers/users.py`, con su número de línea.
    """
    r = client.post(
        "/users",
        json={"email": "nuevo@dimabe.cl", "password": "Inicio2026$", "role": "operator"},
        headers=_admin(client, db),
    )

    assert r.status_code == 422
    cuerpo = r.text
    assert "Inicio2026$" not in cuerpo
    assert "/srv/" not in cuerpo and "routers/users.py" not in cuerpo
    # Y el tipo crudo de Pydantic tampoco: no le dice nada a quien lo lee.
    assert "string_too_short" not in cuerpo


def test_dice_qué_corregir_en_castellano(client, db):
    r = client.post(
        "/users",
        json={"email": "nuevo@dimabe.cl", "password": "corta", "role": "operator"},
        headers=_admin(client, db),
    )
    assert (
        r.json()["error"]["message"] == "La contraseña es demasiado corta (mínimo 12 caracteres)."
    )


def test_con_varios_campos_malos_los_lista_todos(client, db):
    """De uno en uno, el operador corrige, reenvía y descubre el siguiente."""
    r = client.post("/users", json={"password": "corta"}, headers=_admin(client, db))

    cuerpo = r.json()["error"]
    assert "campos que corregir" in cuerpo["message"]
    assert any("correo" in d for d in cuerpo["details"])
    assert any("contraseña" in d for d in cuerpo["details"])


# El caso de un `value_error` con mensaje propio —«el período termina antes de
# empezar»— ya lo cubre test_receipts.py::test_backwards_period_is_rejected, que
# es donde vive ese validador. Duplicarlo aquí obligaría a montar el cliente y la
# autenticación de boletas para comprobar lo mismo.
