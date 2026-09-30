# Pendientes

Lo que queda por hacer fuera de la certificación ante el SII, que tiene su
propio archivo en [`certificacion-sii.md`](certificacion-sii.md).

**Actualizar al avanzar.** Última revisión: **2026-09-30**: el §3 daba por
abiertos dos puntos del conector ya resueltos, y se cerraron la tabla de
países de Aduana, el ILA y las BTE emitidas.

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

Vive en `core_community` (DIMABE-LTDA/core_community, rama 19.0) junto con la
base y RRHH; sus pruebas e2e están en `tools/dte/e2e` y su README manda.

- ~~*Probar conexión* no probaba las credenciales de emisión~~ — resuelto:
  llama a `/me` con el par del ambiente elegido.
- ~~La guía de despacho nunca se probó de punta a punta~~ — resuelto:
  `guia.spec.ts` la emite desde el albarán con los datos de la Res. 154.
- **BHE recibidas y BTE emitidas** (2026-09-30): se registran como factura de
  proveedor con la retención que informa el SII, vía `/bhe/received` y
  `/bte/issued`. La consulta real al portal no se ha probado: el cliente de
  pruebas no tiene clave tributaria y la constructora no tiene boletas.
- **Pendiente — BHE emitidas propias** (sociedad de profesionales): se conoce
  la página del SII, no los nombres de sus columnas. Falta una boleta real.

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
- **Procedimientos del emisor** (22-09-2026): lo declarado al SII en la
  declaración de cumplimiento está escrito en `docs/procedimientos.md` —las
  siete funciones críticas, qué hace el sistema y qué hace una persona, la
  cuadratura mensual y las contingencias—. Es lo que el SII puede auditar.
  Las dos cosas que ahí siguen siendo manuales: el intercambio con proveedores
  (nadie recibe todavía el correo del proveedor) y la emisión de excepción con
  Odoo caído.
- **Alta del emisor desde Odoo** (22-09-2026): *DTE / SII → Certificado y CAF*
  carga el .pfx y los CAF con la credencial de la compañía, sin clave de
  administración y sin entrar al portal. Queda pendiente la clave tributaria
  (`/me/sii-key`), que sólo hace falta para las BHE recibidas.
- **Intercambio con proveedores** (22-09-2026, motor v0.4.31): el DTE del
  proveedor entra por la casilla de intercambio, se registra en Odoo con su
  plazo de ocho días y se responde desde ahí —acuse, aceptar o reclamar con su
  motivo, y recibo de mercaderías—. Además del correo al proveedor, cada
  respuesta **se registra en el SII** (`POST /exchange/claim`, WS
  `WSREGISTRORECLAMODTE`): eso es lo que corre el plazo de la Ley 19.983, y es
  lo que faltaba. Si el SII no contesta, la respuesta al proveedor se mantiene
  y el registro queda pendiente con reintento horario.
  - ~~Crear la factura de proveedor desde el documento recibido~~ — **hecho
    el 24-09-2026** (sin commitear). `/exchange/inspect` entrega ahora el
    detalle, los montos por impuesto y las referencias, leídos sin recalcular
    (un documento ajeno puede traer precios con decimales). En Odoo la
    factura se crea sola y **se publica sólo si calza con su OC**; si no calza
    o no trae OC, queda en borrador con la alerta y cada diferencia. La nota
    de crédito salda la factura que corrige. El cruce vive en un módulo
    puente (`l10n_cl_dte_service_purchase`) que se instala solo con Compras.
    Detalle en el manual del conector (`core_community/docs/dte/MANUAL.md`),
    §4.1.
  - **Falta, del mismo trabajo:** la nota de crédito no descuenta lo
    facturado en la OC (una devolución no reabre la cantidad por facturar), y
    el descuento o recargo global del documento siempre deja la factura para
    revisión, porque la OC no lo trae.
- **Cesión de facturas** (23-09-2026, motor v0.4.32): `POST /cession` arma el
  AEC —documento cedido, contrato con la declaración jurada de la Ley 19.983 y
  sobre, cada uno con su firma— y lo anota en el RPETC
  (`/cgi_rtc/RTC/RTCAnotEnvio.cgi`); `POST /cession/status` consulta cómo
  quedó. El documento cedido sale del archivo del facturador: el ERP sólo dice
  qué folio cede, así que el AEC lleva exactamente lo que se emitió.
  - En Odoo, botón «Ceder a factoring» en la factura, con las guardas del
    SII: publicada, aceptada, no pagada, tipo cedible y monto dentro del total.
  - ~~El asiento contable de la cesión~~ — **hecho el 25-09-2026** (sin
    commitear). Al ceder, la cuenta por cobrar pasa del cliente al factoring;
    la «Liquidación del factoring» compensa su diferencia de precio y su
    comisión contra la cuenta por pagar, porque el gasto y el IVA van en la
    factura que emite el factoring; el anticipo y el excedente se concilian
    en el banco. Si el SII rechaza la cesión, se revierte. Detalle en el
    `MANUAL.md` del conector, §5.1.
- **Exportación desde Odoo** (23-09-2026, motor v0.4.33): los tres documentos
  (110/111/112) se emiten desde la factura, con su pestaña de Aduana y los
  códigos del Compendio que publica `GET /dte/customs` —el ERP no lleva copia
  propia—. Las monedas guardan el nombre literal que exige `<TpoMoneda>`.
  Lo que salió al probarlo, y que habría costado rechazos del SII:
  - el comprador extranjero no tiene RUT: va el `55555555-5` y su
    identificación real en `<Extranjero>`;
  - `<OtraMoneda>` es obligatoria: se declara el tipo de cambio **del día de la
    factura**, no el de hoy;
  - el folio reservado no viajaba y se gastaban **dos folios** por documento;
  - no se arrastraba la opción de validar XSD de la compañía.
  - El archivo del facturador sólo reconocía `<Documento>`: **exportaciones y
    liquidaciones no quedaban archivadas** y no se podían recuperar ni
    reimprimir. Corregido para las tres raíces.
- ~~**La tabla de países de Aduana tiene huecos**~~ — **resuelto el
  2026-09-30** (motor v0.4.37). La primera transcripción perdió las filas del
  Anexo 51-9 que traen «Abreviatura»: faltaban 24 países, Estados Unidos (225)
  entre ellos. Se contrastó con el anexo publicado; 237-239 no existen.
- **ILA y demás impuestos adicionales** (2026-09-30, motor v0.4.36): facturas
  33 y notas 56/61 con `additional_taxes` e `items[].additional_tax_code`; el
  libro los informa en `<OtrosImp>`. El plan de Odoo trae el ILA de 18 % con
  el código 26 (cervezas en el SII); el conector lo envía como 271.

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
