# Pendientes

Lo que queda por hacer fuera de la certificación ante el SII, que tiene su
propio archivo en [`certificacion-sii.md`](certificacion-sii.md).

**Actualizar al avanzar.** Última revisión: **2026-09-15**, corrigiendo el §2, que
daba por no desplegado algo que lleva meses en producción.

---

## 1. Endurecer el servicio para exponerlo a internet — **cerrado**

El servicio es multiempresa y va a quedar público, así que estos huecos dejaban
de ser teóricos. Salieron de dos auditorías del código y todos están cerrados,
cada uno verificado contra el servicio corriendo, no sólo con tests.

### Lo que ya está bien resuelto — no gastar ahí

Las `apiKey` van hasheadas con argon2 y se verifican en tiempo constante, con
verificación señuelo para que un `customerCode` inexistente no responda antes
(`app/security/tenant.py`). Certificados y CAF están cifrados en reposo con
Fernet. La cookie de sesión es `httponly` + `secure` + `SameSite=Strict`
(`app/routers/auth.py:34`). Contraseña mínima de 12. Un cliente archivado no
autentica. Hay auditoría de cambios y de requests. Existen `MachineKey` por
consumidor, hasheadas en base y con rol propio.

### Cerrado el 2026-09-07

**Los ocho huecos del inventario.** El servicio queda listo para exponerse:

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
- **Los límites pueden compartir estado.** Con `DTE_REDIS_URL` el contador vive
  en Redis y el tope vale para todo el despliegue; sin ella todo sigue igual, en
  la memoria de cada proceso. El cliente se importa de forma perezosa y, si
  Redis deja de responder, se degrada al limitador en memoria en vez de fallar
  el request: perder exactitud es mejor que tumbar el servicio. En el compose va
  con el perfil `escalado`, porque con un solo worker sobra.
  Medido con dos workers y tope 10: sin Redis, 13 intentos antes del primer 429
  y algunos 401 colándose después; con Redis, exactamente 10 y corta.
- **Cuota por cliente autenticado** (`DTE_CUSTOMER_REQUESTS_PER_MINUTE`, 120 por
  omisión, 0 la apaga). La llave es el cliente, no la IP: es el cliente quien
  consume, salga por donde salga. Se cobra **después** de autenticar, para que
  nadie pueda agotarle la cuota a un cliente ajeno sin conocer su credencial.
- **Segundo factor en el portal** (TOTP, RFC 6238). El secreto va cifrado con
  Fernet: en claro equivale a la credencial. El alta es en dos pasos y no activa
  nada hasta confirmar un código, porque si se activara al generar el secreto,
  cerrar la pestaña a medias dejaría al usuario fuera de su propio portal.
  Ocho **códigos de recuperación** de un solo uso, hasheados con argon2 — sin
  ellos, perder el teléfono deja fuera del portal que custodia los certificados
  de todas las empresas, y si eras el único superadmin no hay a quién pedirle
  ayuda. Como última salida, un superadmin puede resetear el de otro, y queda
  anotado en la auditoría de cambios.

  La tercera auditoría encontró dos fallos en esta parte, ya corregidos:
  **los códigos no se consumían** —el mismo valía durante toda su ventana de
  ±1 paso, hasta 90 s, así que verlo una vez daba barra libre en vez de un solo
  disparo— y **el alta no re-pedía la contraseña**, siendo que la baja sí: con
  una sesión robada se podía fijar un segundo factor ajeno sobre una cuenta que
  aún no lo tenía, quedarse con los ocho códigos y dejar fuera al titular. Ahora
  el paso usado se guarda y se avanza con un UPDATE condicional, para que dos
  workers no acepten el mismo código a la vez.
- **`cors_origins` se valida al arrancar** (`app/core/config.py`): rechaza `*`
  —prohibido junto a `allow_credentials=True`— y exige el esquema, porque el
  header `Origin` siempre lo trae y sin él la regla no casa nunca y falla en
  silencio.

---

## 2. Despliegue en Dokploy — **desplegado y con auto-deploy**

Está **en producción** en Dokploy (servidor `atlas.dimabe.cl`, compose
`dte-dte-service-ywgfby`), y **cada push a `main` dispara un despliegue solo**, por
el webhook de Dokploy. Un build tarda unos dos minutos.

> **Empujar a `main` ES desplegar a producción.** No es un guardado: el portal, el
> sitio de boletas y el API quedan actualizados sin que nadie apriete nada.
> Comprobado el 2026-09-15 siguiendo un commit por su hash hasta la pestaña
> Deployments. *(Hasta esa fecha esta sección afirmaba que el compose estaba
> "listo y validado, pero no se ha desplegado", y llevaba meses siendo falsa.)*

El auto-deploy **no** está en este repositorio: `.github/workflows/ci.yml` sólo
corre tests y lint. Sale del webhook propio de Dokploy, que se configura en su
panel, así que no hay forma de deducirlo leyendo el código — de ahí que este
archivo se quedara atrás.

**Las migraciones corren solas al arrancar el contenedor**: el `CMD` del Dockerfile
es `alembic upgrade head && uvicorn …`. Encadenado con `&&`, así que si la
migración falla el API no levanta, y no queda sirviendo contra un esquema a medias.
El compose espera el healthcheck de Postgres antes de arrancar el API.

Pendiente ahí: si algún día se levanta **más de una réplica** (el compose ya trae
el perfil `escalado`), todas correrían `alembic upgrade head` a la vez y no hay
ningún lock. Con una sola instancia no es un problema; con dos, lo es.

Publica tres superficies en dominios separados:

| Dominio | Qué es |
|---|---|
| `boletas.dimabe.cl` | Sitio público y anónimo. Su nginx sólo proxya `/api/public/` |
| `dte.dimabe.cl` | Portal de administración |
| `api.dimabe.cl` | API para clientes máquina (Odoo) |

Separados a propósito: el sitio anónimo no comparte origen con el portal que
custodia certificados, ni el API que autentica por `apiKey` con el navegador
que autentica por cookie. La base nunca sale de la red interna.

Lo que hubo que definir en el despliegue (queda como referencia para levantar otra
instancia; **no está verificado desde el repo cómo quedó cada variable en el panel
de Dokploy** — eso sólo se ve ahí, en Environment):
- `DTE_SUPERADMIN_EMAIL` / `DTE_SUPERADMIN_PASSWORD`, o el portal queda sin forma
  de entrar.
- `PORTAL_ALLOWED_IPS` (lista blanca en Traefik). **Conviene comprobar que esté
  puesta**: vacía, la única barrera del portal es el login, y desde ahí se
  administra material tributario.
- El API **no se publica** salvo que pongas `API_PUBLIC=true`, y aun entonces
  sale sólo su superficie de máquina. Si Odoo corre en el mismo servidor, deja
  eso como está y conéctalo a la red interna (`http://api:8000`).
  *(Antes esta línea decía que el API salía a internet sólo si existía el
  registro DNS de `API_DOMAIN`. Era falso —Traefik enruta por la cabecera
  `Host`— y de ahí salió el hallazgo de la segunda auditoría.)*

**Una instancia nueva arranca con la base vacía**: sin clientes, certificados ni
CAF. Hay que cargarlos por el portal o por la API de administración. Si el
despliegue es el backend definitivo, conviene **emitir el set de boletas desde
ahí**, no desde una instancia local: si no, las boletas quedan en una base y el
sitio público consulta la otra. *(Qué tiene cargado hoy la base de producción no
se puede saber leyendo el repo: hay que mirarlo en el portal.)*

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

- ~~**Exportaciones en el Libro de Ventas**~~ — **resuelto el 2026-09-08.** La
  línea del libro acepta ahora `currency` y `exchange_rate`, y el servicio
  convierte a pesos con redondeo medio hacia arriba. Antes los montos eran
  enteros: una factura de USD 15,40 sólo podía declararse como `15` y entraba al
  libro como 15 pesos, el monto del documento leído como si fuera nacional.
  Cada línea se convierte al tipo de cambio de **su** fecha, porque el del
  documento es el que corresponde. Si la línea cerraba antes de convertir
  (`total = exento + neto + IVA`) se fuerza a que siga cerrando después: el SII
  cuadra el libro sumando, y redondear cada parte por su cuenta puede dejar el
  total a un peso de la suma. Y un decimal sin moneda declarada se rechaza, que
  es justo el error que se buscaba evitar.
- ~~**Funciones de la declaración de cumplimiento de boleta**~~ — **agregado el
  2026-09-17** (motor v0.4.29). La declaración pide, entre otras, «cuadratura de
  envíos aceptados, rechazados y aceptados con reparos» y «generar, guardar y
  enviar al SII las boletas cuando sean solicitadas». Faltaba:
  - Cada `EnvioBOLETA` queda en `receipt_submission` con su TrackID.
    `GET /boletas/submissions` los lista con la cuadratura y
    `POST /boletas/submissions/{track}/refresh` consulta al SII.
  - `GET /boletas/{tipo}/{folio}/xml` entrega el XML firmado guardado.
  - **CAF**: al cargarlo se rechaza si su llave privada no corresponde a la
    pública, si es de otro ambiente (IDK 100 = certificación) o si venció. Los
    de documentos con crédito fiscal (33, 43, 46, 56, 61) vencen a los seis meses
    de su autorización (Res. Ex. SII N° 58/2017) y el SII rechaza sus folios. El
    asignador salta los CAF vencidos o de otro ambiente, también los cargados
    antes (completa sus fechas al usarlos).
  - `GET /dte/folios` y `GET /admin/customers/{id}/folios`: por tipo, estado de
    cada CAF, folios utilizables, folios **sin usar** de CAF vencidos (hay que
    anularlos en el SII) y folios fallidos, sin desenlace o huérfanos (asignados
    sin desenlace hace más de 15 minutos).
  - **Folios sin desenlace** (22-09-2026): si el envío al SII se corta a medias
    el folio queda `unknown`, no `failed`. No se sabe si el documento llegó, y
    anularlo en el SII por las dudas sería peor que revisarlo. Se dan por no
    enviados sólo los casos en que consta que no salieron: autenticación
    fallida o sin conexión. Odoo los muestra en «Sin desenlace».
- **Pendiente, del mismo trabajo:** mostrar el inventario de folios y la
  cuadratura de boletas en el portal (hoy sólo por API); un aviso activo
  (correo) cuando un CAF está por vencer o quedan pocos folios; y consultar
  automáticamente el estado de los envíos de boleta en vez de a pedido. El RCOF
  diario existe (`POST /boletas/folio-report`) pero es a pedido; dejó de ser
  obligatorio en 2022 (Res. Ex. 53).
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
