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

Los tres huecos más expuestos, que eran también los más baratos:

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

### Huecos, por gravedad

**1. El API publica la administración fuera de la lista blanca del portal.**
Hallado en la auditoría del 2026-09-07, en `docker-compose.dokploy.yml`. El
portal lleva un middleware `ipallowlist` de Traefik (`PORTAL_ALLOWED_IPS`)
porque desde ahí se administra el material tributario. Pero el servicio `api`
se publica en el mismo Traefik con `traefik.enable=true` y **sin ese
middleware**, y sirve exactamente los mismos routers: `/admin/*`, `/auth/login`,
`/users`, `/machine-keys`, `/audit`.

El comentario del propio archivo dice que el API «sale a internet sólo si
creas el registro DNS de `API_DOMAIN`». **Es falso**: Traefik enruta por la
cabecera `Host`, no por DNS. Basta conectar a la IP del servidor mandando
`Host: api.dimabe.cl` e ignorar el aviso de certificado —sin registro DNS, Let's
Encrypt no puede emitirlo y Traefik sirve el suyo por defecto— para alcanzar la
administración desde cualquier punto de internet.

No es un bypass de autenticación: sigue haciendo falta una credencial válida.
Lo que rompe es el control de red que el operador cree haber puesto, y deja la
fuerza bruta contra `X-Admin-Key` y contra el login del portal accesible desde
fuera del perímetro. Como todavía **no se ha desplegado**, corregirlo ahora es
gratis. Tres caminos, y hay que elegir:

- aplicar el mismo `ipallowlist` al router `dteapi` — simple, pero Odoo tendría
  que salir por una IP de la lista;
- publicar en `api.dimabe.cl` sólo los prefijos de máquina (`/dte`, `/boletas`,
  `/books`, `/rcv`, `/bhe`, `/exchange`) y bloquear el resto en Traefik — ojo:
  el README documenta que Odoo use `X-Admin-Key` contra
  `/admin/customers/{id}/rcv`, así que ese flujo habría que moverlo;
- quitarle `traefik.enable=true` al servicio `api` y dejarlo sólo en la red
  `interna`, que es lo correcto si Odoo corre en el mismo servidor.

**2. El límite de tasa vive en la memoria de cada proceso**
(`app/security/ratelimit.py`, ya documentado ahí). Con 2 workers el límite
efectivo es el doble; con varias réplicas se multiplica. Para internet hay que
moverlo a Redis, o aplicarlo además en Traefik.

**3. Sin cuota por cliente.** El único freno es sobre *fallos* de
autenticación. Un cliente autenticado llama sin tope, y las operaciones caras
—firmar, hablar con el SII— no tienen límite: un cliente puede degradar el
servicio de los demás. En multiempresa importa.

**4. Sin segundo factor en el portal.** Quien administra el material tributario
de todos los clientes entra sólo con correo y contraseña. Es lo más caro de
implementar y lo que menos urge si el portal queda restringido por IP.

**5. `cors_origins` no se valida** (`app/core/config.py:29` y `:77`). Acepta
cualquier valor, incluido `*`, y se usa con `allow_credentials=True`. Debería
rechazar el comodín al arrancar, como ya hace con las claves débiles.

### Orden sugerido

El 1 va primero: no se ha desplegado todavía, así que sale gratis y es el único
que deja una puerta abierta en producción. El 5 es de un rato. El 2 y el 3 son
los que de verdad importan para multiempresa en serio, y son más trabajo. El 4,
al final.

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
