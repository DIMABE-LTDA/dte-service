# Pendientes

Lo que queda por hacer fuera de la certificación ante el SII, que tiene su
propio archivo en [`certificacion-sii.md`](certificacion-sii.md).

**Actualizar al avanzar.** Última revisión: **2026-09-07** (segunda auditoría,
tras cerrar los tres primeros huecos).

---

## 1. Endurecer el servicio para exponerlo a internet

El servicio es multiempresa y va a quedar público, así que estos huecos dejan
de ser teóricos. Salen de una auditoría del código, no de una lista genérica:
cada uno se verificó leyendo la implementación.

### Lo que ya está bien resuelto — no gastar ahí

Las `apiKey` van hasheadas con argon2 y se verifican en tiempo constante, con
verificación señuelo para que un `customerCode` inexistente no responda antes
(`app/security/tenant.py`). Certificados y CAF están cifrados en reposo con
Fernet. La cookie de sesión es `httponly` + `secure` + `SameSite=Strict`
(`app/routers/auth.py:34`). Contraseña mínima de 12. Un cliente archivado no
autentica. Hay auditoría de cambios y de requests. Existen `MachineKey` por
consumidor, hasheadas en base y con rol propio.

### Cerrado el 2026-09-07

Cinco de los ocho huecos del inventario:

- **Límite de intentos sobre `X-Admin-Key`.** Antes era la única credencial que
  se podía probar sin tope, y es la que escribe sobre **todos** los clientes.
  Ahora cuenta fallos por IP (`DTE_ADMIN_KEY_FAILURES_PER_5MIN`, 10 por defecto)
  y bloquea **antes** de verificar el hash, para no gastar argon2 en el
  atacante. Un 403 por rol no cuenta: esa credencial es válida.
- **La clave de bootstrap se puede apagar.** `DTE_ADMIN_BOOTSTRAP_KEY_ENABLED=false`
  deja `DTE_ADMIN_API_KEY` sin efecto (y la variable puede ir vacía). Hacerlo en
  cuanto Odoo y los demás consumidores usen `MachineKey`. Si se apaga sin que
  exista ninguna, el arranque lo advierte en el log: `/admin` queda solo con el
  JWT del portal.
- **Cabeceras de seguridad.** `X-Frame-Options: DENY`, CSP `frame-ancestors
  'none'`, `X-Content-Type-Options` y `Referrer-Policy` en toda respuesta, más
  HSTS donde hay TLS delante (`DTE_COOKIE_SECURE=true`). El nginx del portal y
  el del sitio de boletas ponen las mismas sobre el HTML que sirven ellos, que
  es donde estaba el riesgo real de clickjacking.

  Va como middleware **ASGI puro** (`app/security/headers.py`), no como
  `BaseHTTPMiddleware`: esa clase envuelve cada request en un task group y mueve
  el cierre de las dependencias. Con ella, el insert del access-log llegaba a
  perderse. Para agregar cuatro cabeceras no hace falta nada de eso.
- **El API ya no publica la administración.** `API_PUBLIC` por omisión en
  `false`: el API no se enruta en Traefik salvo decisión explícita, y cuando se
  publica sale **sólo la superficie de máquina** (`/dte`, `/boletas`, `/rcv`,
  `/books`, `/bhe`, `/exchange`, `/health`). `/admin`, `/auth`, `/users`,
  `/machine-keys`, `/audit` y `/docs` quedan únicamente en el dominio del
  portal, que sí tiene lista blanca. Verificado e2e contra un Traefik real,
  incluidos diez intentos de saltarse el prefijo con `..` y codificaciones: la
  app no colapsa `..`, así que ninguno alcanza el router de administración.
- **`cors_origins` se valida al arrancar** (`app/core/config.py`): rechaza `*`
  —prohibido junto a `allow_credentials=True`— y exige el esquema, porque el
  header `Origin` siempre lo trae y sin él la regla no casa nunca y falla en
  silencio.

### Huecos, por gravedad

**1. El límite de tasa vive en la memoria de cada proceso**
(`app/security/ratelimit.py`, ya documentado ahí). Con 2 workers el límite
efectivo es el doble; con varias réplicas se multiplica. Para internet hay que
moverlo a Redis, o aplicarlo además en Traefik.

**2. Sin cuota por cliente.** El único freno es sobre *fallos* de
autenticación. Un cliente autenticado llama sin tope, y las operaciones caras
—firmar, hablar con el SII— no tienen límite: un cliente puede degradar el
servicio de los demás. En multiempresa importa.

**3. Sin segundo factor en el portal.** Quien administra el material tributario
de todos los clientes entra sólo con correo y contraseña. Es lo más caro de
implementar y lo que menos urge si el portal queda restringido por IP.

### Orden sugerido

El 1 y el 2 son los que de verdad importan para multiempresa en serio, y van
juntos: el estado compartido es lo que hace exacta cualquier cuota. El 3, al
final — es lo más caro y lo que menos urge con el portal restringido por IP.

---

## 2. Despliegue en Dokploy

`docker-compose.dokploy.yml` está listo y validado, **pero no se ha desplegado**.
Publica tres superficies en dominios separados:

| Dominio | Qué es |
|---|---|
| `boletas.dimabe.cl` | Sitio público y anónimo. Su nginx sólo proxya `/api/public/` |
| `dte.dimabe.cl` | Portal de administración |
| `api.dimabe.cl` | API para clientes máquina (Odoo) |

Separados a propósito: el sitio anónimo no comparte origen con el portal que
custodia certificados, ni el API que autentica por `apiKey` con el navegador
que autentica por cookie. La base nunca sale de la red interna.

Al desplegar:
- Definir `DTE_SUPERADMIN_EMAIL` / `DTE_SUPERADMIN_PASSWORD`, o el portal queda
  sin forma de entrar.
- Considerar `PORTAL_ALLOWED_IPS` (lista blanca en Traefik). Vacía, la única
  barrera es el login.
- El API sale a internet **sólo si se crea el registro DNS** de `API_DOMAIN`.
  Si Odoo corre en el mismo servidor, es más seguro no crearlo y conectar Odoo
  a la red interna.

**La instancia arranca con la base vacía**: sin clientes, certificados ni CAF.
Hay que cargarlos por el portal o por la API de administración. Y si ese va a
ser el backend definitivo, conviene **emitir el set de boletas desde ahí**, no
desde la instancia local: si no, las boletas quedan en una base y el sitio
público consulta la otra.

Quedó ofrecido y **sin hacer** un script que cargue empresa, certificado, los
11 CAF y los servicios de una pasada.

---

## 3. Conector de Odoo

Verificado por un agente el 2026-09-07 contra la instancia de pruebas
(`odoo-community-test-odoo-1`, puerto 8169, base `cl_test`). El módulo está
instalado (19.0.1.4.0) y los 13 campos de configuración están expuestos en el
formulario de compañía. El manejo de errores es sólido: configuración
incompleta o servicio caído producen un `UserError` legible, no un traceback, y
un fallo de emisión al postear no revierte el posteo.

**Bug de gravedad media, sin corregir:** el botón *Probar conexión* no prueba
las credenciales que se usan para emitir. Siempre llama a `/rcv/documents` con
las credenciales genéricas de RCV (`dte_service_client.py:150-167`), nunca los
pares certificación/producción que usa la emisión (`_dte_env_credentials`,
`dte_service_client.py:97-119`). Una empresa que sólo emita y no use RCV verá
el botón fallar siempre, aunque su configuración de emisión esté correcta.
Debería probar el par del ambiente del diario.

**También pendiente:** la guía de despacho (tipo 52) está modelada con
transporte y chofer pero **nunca se probó de punta a punta** desde Odoo.

---

## 4. Deuda del propio servicio

- **Exportaciones en el Libro de Ventas**: entran en moneda extranjera tratadas
  como si fueran pesos (una factura de USD 15,40 va como `MntExe=15`). No es lo
  que bloquea la certificación —se descartó como causa—, pero está mal y hay
  que resolverlo antes de producción. El IECV es en pesos y espera la
  conversión al tipo de cambio observado.
- **Las tres ramas están empujadas pero sin mergear** a la principal:
  `feat/certificacion-sii` en el motor y en el servicio, `feat/guia-despacho`
  en el conector. Son fast-forward limpios.

---

## Ambientes: ya resuelto, para no volver a preguntarlo

`Customer.environment` es `CERTIFICATION` (Maullín) o `PRODUCTION` (Palena),
**por cliente**. Una empresa que opere en ambos son dos registros, cada uno con
su certificado, sus CAF y sus folios — y esa separación es la correcta, no un
rodeo: los CAF son distintos, los correlativos independientes, y mezclarlos
llevaría a emitir en producción con un folio de prueba. El conector de Odoo ya
tiene los dos pares de credenciales y elige según el campo
`dte_service_environment` del diario de ventas.

**Se evaluó unificarlo** —un solo cliente con N ambientes— y se descartó. El
puntero de folios está clavado en `(customer_id, doc_type)`
(`app/db/models.py:137`), así que unificar obliga a meter el ambiente en esa
clave y en la del CAF, el certificado y la credencial del SII. El ambiente
dejaría de ser un dato del inquilino para pasar a ser un parámetro que hay que
arrastrar por cada consulta, y basta olvidarlo una vez para emitir en
producción con un folio de certificación. Hoy eso es irrepresentable: el
ambiente se resuelve al autenticar y las cuatro líneas que lo leen lo toman del
cliente ya resuelto (`app/routers/dte.py`, `app/services/sii_upload.py`,
`app/services/receipt_service.py`).

Hay además un argumento de seguridad: con el modelo actual una `apiKey` de
certificación no puede alcanzar Palena por construcción. Unificar convertiría
una credencial de pruebas filtrada —que siempre circulan más— en una que emite
en producción.

Lo que sí molestaba era de presentación, y está resuelto: la lista de clientes
agrupa las fichas por RUT, así que la empresa se ve una vez aunque siga siendo
dos clientes independientes.
