# dte-service — API multi-cliente sobre el motor `dte_chile`

Servicio **FastAPI multi-tenant** que expone el motor de facturación electrónica
`dte_chile` (SII Chile) por HTTP: RCV (conciliación con Odoo), emisión de DTE,
libro IECV y acuses de intercambio. Cada cliente tiene su **certificado**, su
**API key** y sus **servicios** habilitados.

## Arquitectura

- **Multi-tenant:** el request trae headers `apiKey` + `customerCode`; cada endpoint
  exige un `service_code`. Se resuelve el cliente y se inyecta su `Certificate`.
- **Certificados/CAF cifrados** en BD (Fernet, `DTE_FERNET_KEYS`).
- **Folios en BD** (`SELECT ... FOR UPDATE`) → asignación segura entre workers/hosts.
- **Ambiente SII por cliente** (Maullín/Palena).
- El motor es síncrono → cada llamada corre en threadpool (`run_blocking`).

## Capas

```
routers/  → HTTP (deps, threadpool)      schemas/  → Pydantic (espejo dataclasses)
services/ → envuelven dte_chile          db/       → modelos + sesión
security/ → auth tenant + apikeys        deps/     → tenant + certificate
core/     → config, crypto, logging      errors/   → DteError → HTTP
```

## Desarrollo (Linux)

```bash
sudo apt-get install -y libxml2-dev libxmlsec1-dev pkg-config   # deps del motor
python -m venv .venv && . .venv/bin/activate
pip install -e ../dte_chile          # el motor (path local) o índice/git privado
pip install -e ".[dev]"
export DTE_FERNET_KEYS=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
ruff check . && ruff format --check . && mypy && pytest -q
```

## Levantar

```bash
bash scripts/vendor_engine.sh                  # vendoriza el motor → vendor/dte_chile
# (en Windows: .\scripts\vendor_engine.ps1)
cp -r ../dte_chile/schemas ./schemas           # XSD del SII (opcional, para validar)
export DTE_FERNET_KEYS=...                      # genera una llave Fernet
docker compose up --build
# OpenAPI en http://localhost:8000/docs
```

## El motor `dte_chile`

Vive en el repo **privado** `github.com/leonardodimabe/cl_dte_lib`, **pineado al
tag `v0.1.0`**. Según el contexto:

- **Dev:** `pip install -e ../dte_chile` (clon local, editable).
- **CI / reproducible:** extra `engine` del `pyproject` → instala
  `dte_chile @ git+…/cl_dte_lib.git@v0.1.0` (requiere acceso al repo privado).
- **Docker:** vendorizado offline (`scripts/vendor_engine.*`), sin token en el build.

Subir de versión = nuevo tag en el motor + bump del `@vX.Y.Z` en el extra `engine`.

## CI

`.github/workflows/ci.yml` corre el gate (ruff + format + mypy + pytest) en Linux.
Instala el motor desde el tag con `pip install -e ".[dev,engine]"`, autenticando
git con el secreto **`ENGINE_TOKEN`** (PAT fine-grained / deploy key con lectura a
`cl_dte_lib`; el `github.token` por defecto no alcanza otro repo privado).

## Endpoints (v1)

| Servicio | Endpoint | Auth |
|---|---|---|
| RCV (per-cliente) | `POST /rcv/documents` | `apiKey` + `customerCode` (servicio RCV) |
| RCV (operador) | `POST /admin/customers/{id}/rcv` | `X-Admin-Key` |
| DTE (33/34/46/52/56/61) | `POST /dte/issue`, `GET /dte/status/{track_id}` | `apiKey` + `customerCode` (DTE) |
| DTE en lote (un sobre) | `POST /dte/issue-batch` | `apiKey` + `customerCode` (DTE) |
| Liquidación factura (43) | `POST /dte/issue-settlement` | `apiKey` + `customerCode` (DTE) |
| Exportación (110/111/112) | `POST /dte/issue-export` | `apiKey` + `customerCode` (DTE) |
| Representación impresa | `POST /dte/print` | `apiKey` + `customerCode` (DTE) |
| IECV | `POST /books` | `apiKey` + `customerCode` (BOOK) |
| Libro de Guías | `POST /books/guides` | `apiKey` + `customerCode` (BOOK) |
| Boleta electrónica (39/41) | `POST /boletas/issue-batch` | `apiKey` + `customerCode` (DTE) |
| Consumo de folios (RCOF) | `POST /boletas/folio-report` | `apiKey` + `customerCode` (DTE) |
| Intercambio | `POST /exchange/{ack,result,receipts}` | `apiKey` + `customerCode` (EXCHANGE) |
| Admin (datos maestros) | `POST /admin/customers[...]` | `X-Admin-Key` |
| Portal: auth | `POST /auth/login`, `GET /auth/me` | público / `Bearer` |
| Portal: usuarios | `POST /users`, `GET /users`, `PATCH /users/{id}/active` | `Bearer` (superadmin) |
| Portal: auditoría | `GET /audit/requests` (+`?format=csv`), `GET /audit/changes` | `Bearer` |

Flujo de alta (admin): crear cliente → subir certificado → subir CAF (crea el
puntero de folios) → habilitar servicios (devuelve la apikey una vez).

## Portal: autenticación, roles y auditoría

**Tres planos de acceso:** consumo de servicio (máquinas, `apiKey`+`customerCode`),
portal **admin** (operadores internos) y portal **cliente** (cada empresa ve lo suyo).
Los portales usan **JWT** (`POST /auth/login` → `access_token`; enviar
`Authorization: Bearer <token>`).

**Roles (RBAC):**
- `superadmin` — todo, incl. gestionar usuarios. Se **siembra al arranque** desde
  `DTE_SUPERADMIN_EMAIL`/`_PASSWORD` (idempotente). No se puede desactivar el último.
- `operator` — datos maestros + RCV de operador.
- `auditor` — solo lectura (auditoría).
- `client` — scoped a su `customer_id` (ve sus servicios y su consumo).

**Auditoría:**
- **Access-log** por middleware → `RequestLog` registra TODA petición (principal,
  servicio, endpoint, IP, resultado, latencia) sin secretos. Consultable en
  `GET /audit/requests` (el `client` solo ve lo suyo) y exportable a CSV.
- **Audit de cambios** → `AdminAudit` (quién modificó qué), en `GET /audit/changes`.

### Consultar el RCV (compras/ventas) de un cliente

El `issuer_rut` **no se pide**: se toma del cliente resuelto. Body = `{period, operation}`
(`operation` = `COMPRA` o `VENTA` → dos llamadas para ambos libros).

**Per-cliente** (cada empresa con su apiKey):

```bash
curl -X POST http://localhost:8000/rcv/documents \
  -H "customerCode: <key>" -H "apiKey: <apikey_rcv>" \
  -H "Content-Type: application/json" \
  -d '{"period":"202505","operation":"COMPRA"}'
```

**Operador / Odoo** (una sola credencial para cualquier cliente, usa su cert guardado):

```bash
curl -X POST http://localhost:8000/admin/customers/<id>/rcv \
  -H "X-Admin-Key: <clave>" -H "Content-Type: application/json" \
  -d '{"period":"202505","operation":"VENTA"}'
```

### Claves de máquina por consumidor

`X-Admin-Key` acepta dos credenciales: la de **bootstrap** (`DTE_ADMIN_API_KEY`,
entorno) y **claves por consumidor** hasheadas en BD, con rol propio
(`operator`=escritura, `auditor`=lectura), identidad en la auditoría y
revocables. Las gestiona un **superadmin** (JWT):

```bash
# Crear (devuelve la clave completa key_id.secret UNA sola vez):
curl -X POST http://localhost:8000/machine-keys \
  -H "Authorization: Bearer <jwt_superadmin>" -H "Content-Type: application/json" \
  -d '{"name":"odoo-prod","role":"operator"}'
# Listar / revocar:
curl http://localhost:8000/machine-keys -H "Authorization: Bearer <jwt>"
curl -X DELETE http://localhost:8000/machine-keys/<id> -H "Authorization: Bearer <jwt>"
```

Recomendado: usar `DTE_ADMIN_API_KEY` solo para crear la primera clave de
consumidor y migrar Odoo (y otros) a claves dedicadas. Hecha esa migración,
**apagar la de bootstrap** con `DTE_ADMIN_BOOTSTRAP_KEY_ENABLED=false`: deja de
autenticar (y puede ir vacía). Es la única credencial con poder sobre todos los
clientes que no es revocable ni deja identidad en la auditoría. Si se apaga sin
haber creado ninguna clave de máquina, `/admin` queda solo con el JWT del
portal y el servicio lo avisa en el log al arrancar.

## Endurecimiento

Pensado para que el servicio pueda quedar expuesto a internet:

- **Fuerza bruta.** El login del portal cuenta *todo* intento por IP
  (`DTE_LOGIN_ATTEMPTS_PER_MINUTE`); `X-Admin-Key` y las credenciales de cliente
  cuentan solo los **fallos** (`DTE_ADMIN_KEY_FAILURES_PER_5MIN`,
  `DTE_TENANT_AUTH_FAILURES_PER_5MIN`), así el tráfico legítimo de alto volumen
  no se penaliza. Una credencial válida rechazada por rol (403) no cuenta como
  fallo. El estado vive en la memoria de cada proceso: con N workers el límite
  efectivo es ~N x, suficiente como freno, no como cuota exacta.
- **Cabeceras.** Toda respuesta lleva `X-Frame-Options: DENY`,
  `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options` y
  `Referrer-Policy`; con `DTE_COOKIE_SECURE=true` (o sea, con TLS delante) se
  agrega HSTS. Los nginx del portal y del sitio de boletas ponen las mismas
  sobre el HTML que sirven ellos.

## Panel admin (SPA React)

En `frontend/` (React + Vite + TypeScript). **Sesión por cookie HttpOnly**
(inmune a robo por XSS): el login (`POST /auth/login`) setea la cookie
`access_token` (`HttpOnly; Secure; SameSite=Strict`) y `POST /auth/logout` la
borra. La SPA y el API se sirven en **mismo sitio** (sin CORS): el navegador
llama al API bajo `/api/*` (proxy de Vite en dev, nginx en prod).

> Diseño dual: el token también viaja en el body del login, así clientes
> **mobile/máquina** (Odoo) pueden seguir usando `Authorization: Bearer`. El
> API acepta el token por header (precedencia) **o** por cookie.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxy /api → API en :8000)
npm run lint         # eslint
npm run format       # prettier --write
npm run test         # vitest (watch) · test:run para CI
npm run build        # tsc + vite build → dist/
```

En producción, `docker compose up --build` levanta el servicio `web` (nginx)
que sirve la SPA y proxya `/api/*` al API. Los clientes máquina (Odoo) usan el
API directo en `:8000`. Sobre HTTP de demo, poné `DTE_COOKIE_SECURE=false`;
en prod con TLS, `true`.

Calidad: ESLint (+ react-hooks), Prettier y **Vitest + Testing Library**; el CI corre
`lint` + `format:check` + `test:run` + `build`. Hook `useApi` para carga/estado
uniforme y un `ErrorBoundary` global.

Páginas: **Login**, **Clientes** (alta + gestión: habilitar servicios, subir
certificado/CAF, correr RCV de operador), **Usuarios** (solo superadmin) y
**Auditoría** (peticiones con filtros + export CSV, y cambios). La navegación y
las acciones se muestran según el rol del usuario.

## Migraciones (Alembic)

La migración inicial ya está en `migrations/versions/`. Aplicar el esquema:

```bash
alembic upgrade head
```

Para futuros cambios de esquema (tras editar los modelos ORM):

```bash
alembic revision --autogenerate -m "descripcion" && alembic upgrade head
```

Alembic toma la URL de `DTE_DATABASE_URL` (ver `migrations/env.py`). El atajo
`python -m app.db.init_db` queda solo para pruebas rápidas sin Alembic.
