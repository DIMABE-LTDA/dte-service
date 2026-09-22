# Plan: dejar operativo el facturador con Odoo Community

Qué hay que construir y arreglar para que una empresa emita todos sus
documentos tributarios desde **Odoo Community** contra este servicio, y en qué
orden. Cubre dos repositorios:

- `dte-service` (este) — el facturador.
- `l10n_cl_dte_service` (`C:\desarrollo\l10n_cl_dte_service`) — el **módulo
  core** del conector, que en Community es el único responsable de la emisión
  electrónica: la capa EDI de Odoo no existe fuera de Enterprise.

Escrito el **21-09-2026** a partir de dos auditorías del módulo (cobertura
funcional y robustez) y una del flujo de certificación. Lo que aquí se afirma
del módulo se verificó leyendo su código; nada se probó en ejecución, que es
justamente lo que corrige la §1.

> **Fecha dura del calendario:** la Resolución Ex. SII N° 154 entra en vigencia
> el **01-11-2026**. Desde ese día la guía de despacho debe informar los datos
> de transporte. Hoy la guía sólo se puede emitir fabricando una factura de
> venta, y nunca se probó de punta a punta.

---

## 1. Las pruebas de punta a punta son parte del trabajo, no el final

**Toda etapa de este plan se da por terminada cuando tiene una prueba e2e en
Playwright que la ejerce por la interfaz, como lo haría el usuario.** No basta
con tests unitarios: los tres defectos más graves que encontraron las auditorías
—el número impreso que no es el folio, el veredicto del SII que nunca llega y el
folio que se quema en un timeout— son todos de integración, y ninguno se ve
desde un test unitario de ninguno de los dos lados.

### El banco de pruebas — **montado el 21-09-2026**

Vive en `l10n_cl_dte_service/e2e` (ver su README). Se levanta con
`node scripts/entorno.js up` y se corre con `npm test`. Odoo queda en el puerto
8070 para no chocar con la instancia de desarrollo, y el facturador en el 8001.

Cuatro pruebas hoy: la compañía lee su configuración del facturador; una factura
confirmada emite su DTE, consume **un** folio y queda archivada; el documento no
se duplica al reintentar; y —fallando a propósito— que el número que muestra
Odoo sea el folio timbrado, que es el defecto D-01.

Montarlo ya destapó tres cosas que ninguna lectura de código había visto:

- **Los botones de la ficha de compañía no hacían nada.** Estaban dentro de un
  `<group>`, y Odoo 19 no los compila ahí: se ven, se pulsan y no pasa nada.
  Corregido en el módulo.
- **«Leer del servicio» guarda pero no refresca** el formulario: el usuario cree
  que no funcionó hasta que recarga.
- **Sin el plan de cuentas chileno cargado**, Odoo numera con su correlativo
  interno y el conector no puede emitir.

El repo del conector ya traía `docker-compose.yml` con Odoo 19 Community y
Postgres; se le agregó el facturador y la siembra:

| Pieza | Cómo |
|---|---|
| Odoo 19 Community + Postgres | ya existe en `docker-compose.yml` |
| `dte-service` + su Postgres | añadir al compose, imagen construida del repo |
| Inquilino de prueba | siembra por API de administración: empresa, certificado de prueba, CAF sintéticos de rango amplio por tipo |
| Envío al SII | **apagado** (`send=false`). Las pruebas no tocan Maullín: se verifica el XML emitido, el folio y el estado local |
| Escenarios contra el SII real | un puñado, aparte, marcados y ejecutados a mano contra certificación, nunca en CI |

Los CAF sintéticos son los mismos que usan los tests del servicio: estructura y
par de llaves reales, sin la firma del SII. Alcanzan porque nada se envía.

### Qué se prueba por la interfaz de Odoo

- Configurar la compañía: credenciales, leer y guardar el perfil del emisor.
- Emitir una factura y comprobar que **el número que muestra Odoo es el folio
  del XML**, y que el XML queda adjunto.
- Consultar el estado y ver el documento pasar a aceptado y a rechazado.
- Reintentar tras un fallo **sin** que se emita dos veces.
- Imprimir y ver el timbre en el PDF.
- Emitir una guía de despacho desde el albarán, con los datos de transporte.
- Ver el inventario de folios y el aviso de folios bajos.
- Generar el libro mensual.
- Emitir boletas y ver su cuadratura.

### Qué se prueba en el portal del facturador

El mismo criterio para el trámite de certificación (§5): cargar la hoja del set,
emitir, declarar, generar las respuestas de intercambio y descargar las muestras.

### Reglas

- Las pruebas e2e corren en el CI de cada repo y **en local antes de cada push**,
  como el resto del CI.
- Cada defecto que se arregle abajo estrena su prueba: primero la prueba que
  falla, después el arreglo.
- Los escenarios de fallo (timeout, rechazo del SII, credencial equivocada) se
  simulan interceptando la red desde Playwright, no esperando a que ocurran.

---

## 2. Etapa 0 — Decisiones que bloquean el diseño

Ninguna cuesta código; todas cambian lo que se escribe después.

| # | Decisión | Opciones | Recomendación |
|---|---|---|---|
| D1 | **De dónde sale el número del documento** | (a) Odoo reserva el folio *antes* de postear y numera con él; (b) se escribe el folio sobre el número después de emitir | **(a)**. Es la única que garantiza que el papel y el XML digan lo mismo siempre. Obliga a un endpoint de reserva de folio en el servicio |
| D2 | **Cómo sube Odoo el certificado y los CAF** | (a) endpoints acotados al propio cliente; (b) clave de administración guardada en Odoo; (c) sólo lectura en Odoo | **Decidido (a) el 21-09-2026 y ya implementado en el servicio** (§3.6). La clave de administración no está acotada a un cliente: guardarla en Odoo le entregaría a esa instalación las llaves de todos los inquilinos |
| D3 | **Boletas en el POS** | (a) emitir una a una al validar el ticket; (b) cola asíncrona; (c) reservar rangos de folios en Odoo | **(a)** con reintento, salvo que el volumen del cliente no lo tolere. (b) incumple la entrega del documento timbrado |
| D4 | **Dónde vive el módulo** | (a) repo propio; (b) absorberlo al repo del cliente | (b) si lo mantiene el mismo equipo; da CI y revisión común |
| D5 | **Impreso en PDF** | (a) el servicio devuelve PDF; (b) Odoo convierte el HTML | (a). El servicio ya genera PDF para las muestras impresas y controla el formato del SII |
| D6 | **Recepción de DTE de proveedores** | (a) buzón en el servicio; (b) en Odoo; (c) no hacerlo por ahora | Decidir al llegar a la etapa 4: hoy **nadie** recibe el sobre, y sin eso el intercambio queda cojo |

---

## 3. Etapa 1 — Facturador: lo que bloquea todo lo demás

Trabajo en `dte-service`. Es el cuello de botella: sin esto, el módulo no puede
ser robusto por mucho que se arregle.

1. **Idempotencia de la emisión** — **hecha el 21-09-2026**. Cabecera
   `Idempotency-Key`, que el ERP deriva de su propio documento. La marca se
   inserta **antes** de emitir, así que dos peticiones simultáneas con la misma
   clave no emiten dos veces: la segunda recibe «en curso» (409). Un reintento
   posterior devuelve la respuesta guardada, con su folio y su track. Un error
   de datos libera la clave —el ERP corrige y reintenta—; un fallo de envío la
   deja marcada, porque ahí el folio pudo consumirse y el documento pudo llegar
   igual al SII. Sin clave, todo sigue como antes.
2. **Guardar el DTE emitido también en producción** — **hecho el 21-09-2026**.
   Cada documento de cada sobre queda archivado y cifrado, y se recupera con
   `GET /dte/{tipo}/{folio}/xml`. El emisor sigue siendo el responsable de
   conservarlos; esto es la red de seguridad para la respuesta que se pierde.
3. **Reserva de folio** (si se toma D1(a)): `POST /dte/folios/reserve` devuelve
   un folio asignado, y `/dte/issue` acepta emitir con un folio ya reservado.
   Mantiene el bloqueo del puntero y la trazabilidad actual.
4. **Estado del envío con su desglose.** Publicar en la respuesta de estado los
   contadores por tipo —informados, aceptados, rechazados, con reparos— que el
   motor ya entrega. Sin eso, quien consulta no puede distinguir «envío
   procesado» de «documento aceptado», que es el error que costó una semana en
   la certificación.
5. **Desenlace desconocido** — **hecho el 22-09-2026**. Si el envío al SII se
   corta a medias, el folio queda `unknown`, no `failed`: no se sabe si el
   documento llegó, y darlo por perdido llevaría a anular en el SII un
   documento que quizá existe allá. Sólo se da por no enviado cuando consta que
   no salió: falló la autenticación (el token se pide antes de subir) o no se
   llegó a conectar. El inventario de folios los cuenta aparte
   (`unknown`) y los lista en `to_review`; Odoo los muestra en su columna «Sin
   desenlace» con el aviso de consultarlos en el SII.
6. **Carga de certificado y CAF por el propio cliente** (D2) — **hecho el
   21-09-2026**. Con la credencial que la empresa ya tiene: `GET/POST
   /me/certificate(s)`, `GET/POST /me/caf(s)`, `POST /me/cafs/{id}/retire`,
   `GET /me/folios` y `GET/POST/DELETE /me/sii-key` (la clave tributaria, con
   la credencial de BHE). Las guardas son las mismas del portal —llaves que no
   corresponden, CAF de otro ambiente o vencido, rango solapado— y cada carga
   queda en la auditoría marcada como hecha por máquina. Falta la contraparte
   en Odoo (§5).
7. **Tiempos de espera coherentes** — **hecho el 22-09-2026**. El del servicio
   hacia el SII (45 s) es ahora menor que el del conector hacia el servicio
   (60 s): el servicio se rinde primero y alcanza a contestar, en vez de que el
   corte ocurra con el sobre en camino.

**Criterio de término:** prueba e2e que corta la red a mitad de una emisión y
comprueba que el reintento no gasta otro folio y recupera el XML.

---

## 4. Etapa 2 — Módulo: que lo que ya existe sea correcto

Trabajo en `l10n_cl_dte_service`. Nada nuevo: arreglar lo que hoy engaña.

1. **El folio es el número del documento** (D1). Hoy el módulo intenta copiarlo
   sólo si el número está vacío, y al llegar ahí nunca lo está: el folio no se
   copia nunca. Con reserva previa, Odoo numera con el folio y una restricción
   impide postear un DTE cuyo número no sea su folio.
2. **Circuito de estados.** Mapear los códigos reales del SII (`EPR`, `RCH`,
   `RFR`…) en vez de las palabras «ACEPTADO»/«RECHAZADO», que el SII no usa;
   dejar de marcar como enviado un envío que el SII rechazó; y **tarea
   programada** que reconsulte, reintente y avise. Hoy ningún documento alcanza
   jamás un estado definitivo.
3. **Sacar la emisión de la transacción del posteo.** Postear, confirmar, y
   emitir en una segunda fase, guardando folio, track y XML **antes** de tocar
   nada más del documento. Hoy una llamada de hasta 70 segundos bloquea la
   secuencia del diario, y si algo falla después de emitir, el rollback borra el
   rastro de un documento que sí se emitió.
4. **Credenciales.** Grupo propio de administración del DTE y restricción de los
   campos de clave: hoy cualquier empleado puede leer la clave de producción de
   la ficha de compañía y emitir desde fuera de Odoo. Quitar el campo «Enviar al
   SII», que no hace nada y miente, y arreglar «Probar conexión», que prueba el
   servicio equivocado.
5. **Ambiente.** Abortar si el diario no tiene ambiente, en vez de emitir en
   certificación en silencio, y cotejar al emitir que la credencial corresponda
   al ambiente declarado.
6. **Montos.** Enviar los descuentos de línea como descuentos en vez de fundirlos
   en el precio unitario, verificar que el total del XML coincida con el de la
   factura, y rechazar una factura en otra moneda en vez de mandar dólares en
   campos de pesos.
7. **Pruebas.** El módulo no tiene ninguna. Unitarias del mapeo factura → DTE y
   del cliente HTTP, más el banco e2e de la §1.

**Criterio de término:** prueba e2e que emite, imprime y compara el número
visible con el folio del XML; y otra que fuerza un rechazo del SII y comprueba
que el documento queda rechazado y se puede reintentar.

---

## 5. Etapa 3 — Lo que falta para operar

Por valor para la empresa, y con la fecha del 01-11 mandando en el orden.

| # | Qué | Por qué ahora |
|---|---|---|
| 0 | **Puesta en marcha desde Odoo**: asistentes para cargar el certificado y los CAF, ver los cargados y su vencimiento, y guardar la clave tributaria | Requisito del cliente: todo se registra desde Odoo. El servicio ya lo expone; falta la interfaz |
| 1 | **Inventario de folios en Odoo** | La mejor relación valor/esfuerzo: el endpoint existe y usa la credencial que el módulo ya tiene. Hoy la empresa se entera de que se quedó sin folios cuando falla una emisión |
| 2 | **Impresión con timbre** | Es lo que recibe el receptor. Hoy sale el PDF genérico de Odoo: sin timbre, sin folio en formato SII y sin copia cedible |
| 3 | **Guía de despacho desde el albarán + Res. 154** | Fecha dura 01-11-2026. Hoy obliga a fabricar una factura de venta, que además genera un asiento de venta para un traslado que no lo es |
| 4 | **Factura de compra (46) con retención** | El servicio ya la soporta; el módulo la bloquea por aceptar sólo documentos de venta |
| 5 | **Libro de compras y ventas, y libro de guías** | Obligación mensual. No existe en Odoo Community: es ganancia neta |
| 6 | **Boletas 39/41 + cuadratura** | Decisión D3 tomada primero. La cuadratura es, además, lo que declara el representante legal |
| 7 | **Intercambio con proveedores** | Requiere antes la decisión D6: hoy nadie recibe el sobre del proveedor |
| 8 | **Exportación 110/111/112** | Sólo si la empresa exporta; arrastra todos los datos de aduana |

Cada una entra con su prueba e2e por la interfaz de Odoo.

---

## 6. Etapa 4 — Certificar clientes nuevos sin dolor

Trabajo en este repo, independiente del anterior; se puede avanzar en paralelo.
Sale de la auditoría del flujo de certificación.

1. **Registrar el veredicto de la revisión del set** (`SOK`/`SRH`). Hoy el
   expediente puede decir «trámite completo» con los diez sets rechazados,
   porque sólo mira el primer portón.
2. **Pantalla del paso 4 (intercambio).** El backend genera las tres respuestas
   firmadas y validadas; no hay interfaz, así que hoy se hace con `curl`.
3. **Textos del catálogo y unidad del SII.** El paso de simulación dice «10 a
   100 documentos» y el sistema exige 20; el de muestras dice que se envían por
   correo, cuando van por la aplicación de upload. Y si la unidad del SII queda
   vacía, las muestras salen con «SANTIAGO».
4. **Reloj del CAF de boletas.** Aviso de las 24 horas desde la autorización y
   botón para retirar el CAF anterior, en el propio paso. Es donde se perdieron
   tres CAF.
5. **Asistente de la simulación.** Hoy es el único paso que obliga a escribir
   JSON a mano, y el formato que necesita ni siquiera está en el selector.
6. **Aislamiento entre contribuyentes.** Al clonar la definición de otro
   contribuyente se limpia el emisor pero **no los receptores**: los RUT que la
   otra empresa puso como proveedor o mandante quedan en el expediente nuevo.
7. **Acceso del propio contribuyente a su expediente.** Hoy ningún cliente
   puede entrar: el rol no llega a la sección de administración. Es lo
   estructural para que «el cliente cargue sus sets», y conviene diseñarlo
   aparte.

---

## 7. Orden sugerido

1. Etapa 0 — decisiones (D1, D2, D5 bloquean código inmediato).
2. Banco de pruebas e2e en marcha, aunque empiece con un solo escenario.
3. Etapa 1 — facturador (idempotencia, XML guardado, reserva de folio, estado).
4. Etapa 2 — módulo (folio, estados, transacción, credenciales).
5. Etapa 3.1 a 3.3 — folios, impresión y **guía desde el albarán antes del
   01-11**.
6. Etapa 3.4 en adelante, y etapa 4 en paralelo si hay otro par de manos.

## 8. Riesgos

- **La fecha del 01-11** es lo único que no se mueve. Si las etapas 1 y 2 se
  alargan, la guía se adelanta a costa de lo demás.
- **Sólo Constructora Dimabe está en certificación**, y bloqueada en las
  muestras impresas por un problema del SII. Las otras empresas del grupo no
  pueden emitir en producción hasta certificarse: cualquier plan que suponga
  mover al grupo entero de una vez es irreal.
- **No hay ninguna empresa emitiendo en producción** (confirmado el
  21-09-2026), así que el defecto del número impreso no dejó documentos malos.
  Todas las pruebas se hacen en **certificación, con CONSTRUCTORA DIMABE SPA**
  (77262159-0), que es el contribuyente que ya tiene sets, CAF y certificado.
- **Las pruebas e2e contra el SII real gastan folios.** Por eso el banco corre
  con el envío apagado, y los escenarios contra Maullín son pocos, marcados y
  manuales.
