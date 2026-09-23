# Procedimientos del emisor electrónico

Constructora Dimabe SpA · RUT 77.262.159-0

En la declaración de cumplimiento (Res. 45/2003, art. 3) el representante legal
declaró contar con procedimientos para las siete funciones que el SII considera
críticas. El SII puede auditarlos. Esto es lo declarado, escrito: qué hace el
sistema solo, qué hace una persona, y dónde queda la evidencia.

Lo que el sistema no hace se dice aquí como procedimiento manual. Declarar
automático algo que no lo es sería el peor resultado de una auditoría.

Los tres sistemas involucrados:

- **Odoo** (ERP): donde nacen la venta, la compra y el despacho.
- **dte-service** (el facturador): folios, firma, envío al SII, archivo.
- **SII**: Maullín en certificación, Palena en producción.

---

## 1. Gestión de CAF

**Quién:** el administrador de la empresa, con su clave tributaria.

1. Los folios se piden en el SII (`Solicitud de folios`) para cada tipo de
   documento y se descargan como archivo CAF.
2. El CAF se carga en Odoo → *DTE / SII* → *Certificado y CAF* → pestaña
   *CAF* (o en el portal del facturador). El facturador lo rechaza si:
   - la llave privada no corresponde a la pública del propio archivo;
   - es de otro ambiente (IDK 100 es certificación, y sólo sirve en Maullín);
   - ya venció;
   - su rango se solapa con uno ya cargado.
3. El archivo original se guarda fuera de los sistemas, en la carpeta de
   respaldo de la empresa. **La llave privada del CAF no se copia a ningún
   repositorio ni se manda por correo.**

**Vencimiento.** Los CAF de documentos con derecho a crédito fiscal (33, 43,
46, 56, 61) valen seis meses desde su autorización (Res. Ex. SII N° 58/2017).
Pasado eso el SII rechaza sus folios. El inventario de folios muestra la fecha
de vencimiento del CAF en uso y avisa de los folios que quedarían sin usar; el
asignador salta por sí solo los CAF vencidos o de otro ambiente.

**Evidencia:** cada carga queda en la auditoría del facturador con quién la
hizo y cuándo; el inventario de folios muestra el estado de cada CAF.

---

## 2. Foliación controlada

**Quién:** el sistema. Ninguna persona elige un folio.

- El folio se **reserva antes** de numerar el documento: Odoo pide el folio al
  facturador, numera el asiento con él y recién entonces emite. Así el número
  impreso y el del timbre son el mismo, siempre.
- El puntero de folios es por cliente y tipo de documento, con bloqueo: dos
  emisiones simultáneas no pueden recibir el mismo folio.
- Cada folio entregado queda registrado con su desenlace:
  - `issued` — el sobre salió al SII;
  - `failed` — consta que no salió (el documento no se llegó a armar, falló la
    autenticación, o no hubo conexión): folio quemado sin documento;
  - `unknown` — **el envío se cortó a medias y no se sabe si llegó**;
  - `assigned` sin cerrar por más de 15 minutos: huérfano (el proceso murió).

**Revisión (mensual, junto con la cuadratura).** En Odoo → *DTE / SII* →
*Folios del SII*, la columna «A revisar» y «Sin desenlace»:

- **Sin desenlace:** consultar el documento en el SII por RUT, tipo y folio
  antes de hacer nada. Si está allá, se le hace nota de crédito si corresponde;
  si no está, se declara **folio anulado** en el SII.
- **Fallidos y huérfanos:** declarar **folio anulado** en el SII.

Nunca se «reusa» un folio quemado: el SII exige que cada folio tenga un
destino, y anularlo es el destino correcto.

---

## 3. Respaldo

- **Del documento emitido:** el facturador archiva el XML firmado de cada
  documento, cifrado, y lo devuelve con `GET /dte/{tipo}/{folio}/xml`. No
  depende de que la respuesta HTTP llegue a Odoo.
- **En Odoo:** el XML y el PDF quedan adjuntos al asiento contable.
- **De la base de datos:** respaldo diario del servidor (Dokploy) con
  retención mensual.
- **Del certificado digital y los CAF:** copia en la carpeta de respaldo de la
  empresa, fuera de los servidores.

El emisor es el responsable de conservar los documentos por seis años; esto es
la red, no un sustituto del respaldo de la empresa.

---

## 4. Envío al SII

**Quién:** el sistema, al confirmar el documento en Odoo.

1. Odoo reserva folio, arma el documento y lo manda al facturador.
2. El facturador lo firma, lo mete en un `EnvioDTE` con la carátula (número y
   fecha de la resolución) y lo sube al SII.
3. El SII devuelve un **Track ID**, que queda en el asiento.
4. Una tarea programada consulta el estado de cada envío hasta que el SII
   cierra: aceptado (EPR/LOK), aceptado con reparos (RLV/RSC) o rechazado
   (RCH/RFR/RCT). Un rechazo o un reparo abre una **actividad** en el asiento
   para que alguien lo mire; no se archiva solo.

«Envío procesado» no es «documento aceptado»: el estado del envío trae el
desglose por tipo —informados, aceptados, con reparos, rechazados— y es ese el
que se mira.

---

## 5. Intercambio con el proveedor (Ley 19.983)

**Quién:** quien lleva la contabilidad. **Cuándo:** dentro de los **8 días
corridos** desde que el documento se recibe. Pasado el plazo, la factura se
entiende irrevocablemente aceptada —con o sin mercadería— y se hace cedible:
el proveedor puede venderla a un factoring y ya no se discute.

**Lo que enviamos.** El PDF con su timbre se manda al cliente desde el propio
asiento de Odoo («Enviar e imprimir»). Si el cliente pide el XML, se descarga
del asiento y se le manda.

**Lo que recibimos.** Los proveedores mandan sus DTE a la casilla de
intercambio registrada en el SII. Esa casilla se configura en Odoo como
servidor de correo entrante apuntando al modelo de DTE recibidos, y cada
documento aparece en *DTE / SII → DTE recibidos* con su plazo a la vista. Un
sobre puede traer documentos de otro receptor: esos se distinguen y no se
aceptan.

Desde el propio documento, en orden:

1. **Acusar recibo del envío** — que el archivo llegó y está bien formado.
2. **Aceptar** o **Reclamar** el contenido. El reclamo pide su motivo: es lo
   que va a leer el proveedor para corregir.
3. **Recibo de mercaderías** — el que hace cedible la factura. Se da cuando la
   mercadería llegó de verdad; darlo antes es firmar por algo que no se
   recibió.

Cada uno hace dos cosas, y las dos importan:

- manda al proveedor el XML firmado que exige la ley, y lo deja archivado;
- **registra el evento en el SII** (ACD aceptar, RCD reclamar, ERM recibo). Lo
  que corre el plazo es este registro: el SII no se entera de los correos que
  se manden entre las dos empresas.

Si el SII no contesta, la respuesta al proveedor **no se deshace** —el correo
ya salió—: el registro queda pendiente, se reintenta solo cada hora y se puede
forzar con el botón «Registrar en el SII». Conviene comprobarlo, porque un
reclamo que no llegó al SII es un reclamo que no existe. «Ver historial en el
SII» dice qué tiene registrado el Servicio sobre ese documento.

**Aviso del plazo:** una tarea diaria abre una actividad cuando quedan dos
días o menos.

**Pendiente:** crear la factura de proveedor en Odoo desde el documento
recibido; hoy se registra aparte.

---

## 5 bis. Cesión de facturas (factoring)

**Quién:** quien decide el financiamiento, con el visto bueno de contabilidad.

Ceder una factura es venderle a un tercero el derecho a cobrarla. Para que el
deudor quede obligado a pagarle al factoring y no a nosotros, la cesión se
anota en el **Registro Público Electrónico de Transferencia de Créditos** del
SII. Lo que se manda es un AEC: la factura original con su timbre, el contrato
de cesión y la declaración jurada del artículo 3 de la Ley 19.983.

Desde la factura, botón **«Ceder a factoring»**. Antes de dejar ceder se
comprueba lo que el SII —y el sentido común— exigen:

- la factura está **publicada** y tiene folio del SII;
- el SII ya la **aceptó** (aceptada o con reparos). Una factura en proceso o
  rechazada no se cede: sería vender un crédito que puede no existir;
- **no está pagada**: si ya se cobró, no hay crédito que ceder;
- es de un tipo cedible (33, 34, 43, 46);
- el monto cedido no pasa del total del documento;
- el factoring tiene RUT y correo, porque el SII le avisa por ahí.

La declaración jurada dice que el **recibo de las mercaderías** está a
disposición del cesionario. Por eso importa haberlo dado (§5): sin recibo, la
factura no es cedible y la declaración sería falsa.

El AEC queda adjunto a la factura, y «Estado de la cesión» pregunta al SII en
qué quedó: **EOK** es anotada.

**Lo que no hace el sistema:** el asiento contable de la cesión —el traspaso
del cliente al factoring y el gasto financiero— se registra aparte.

---

## 6. Cuadratura de envíos

**Quién:** quien lleva la contabilidad. **Cuándo:** una vez al mes, antes de
declarar, y siempre antes del día 20.

1. **Documentos contra el SII.** En Odoo, los documentos del período con su
   estado. Ninguno puede quedar «pendiente» ni «en proceso»: si lo está, se
   consulta el Track ID y se resuelve.
2. **Aceptados, con reparos y rechazados.** Los rechazados se reemiten con
   folio nuevo —el rechazado se anula— y los reparos se corrigen en el
   documento siguiente.
3. **Boletas.** `GET /boletas/submissions` lista cada envío de boletas con su
   cuadratura (aceptadas, con reparos, rechazadas). Se compara el total de
   boletas del período en Odoo con lo aceptado por el SII.
4. **Folios.** El inventario de folios: que lo emitido más lo anulado cubra
   todo el rango usado, sin huecos. Los «sin desenlace» y «a revisar» se
   resuelven aquí (§2).
5. **Libros.** Se genera el Libro de Ventas y el de Compras del período desde
   Odoo (*DTE / SII* → *Libro de Compras y Ventas*) y se comparan sus totales
   con los del Registro de Compras y Ventas del SII antes de declarar el F29.

**Evidencia:** el XML de cada libro queda adjunto en Odoo con su Track ID.

---

## 7. Administración de contingencias

Qué hacer cuando algo se cae. El principio es el mismo en todos los casos: **no
emitir dos veces el mismo documento, y no dar por perdido lo que no consta que
se perdió.**

### El SII no responde

La venta sigue. Odoo confirma el documento con su folio reservado y lo deja
**pendiente de envío**; una tarea programada reintenta. El documento impreso es
válido: el timbre lo autoriza el CAF, no el envío. Si la caída dura más de un
día, se avisa al SII por la mesa de ayuda y se deja constancia.

### El envío se cortó y no se sabe si llegó

El folio queda **«sin desenlace»**. No se reintenta a ciegas: la clave de
idempotencia del documento impide que Odoo emita otro por la misma venta. Se
consulta el documento en el SII (por RUT, tipo y folio) y:

- si está allá, se marca como emitido y se sigue;
- si no está, se declara folio anulado y se emite de nuevo con folio nuevo.

### El facturador no responde

Odoo **no postea** la factura: sin folio no hay número, y un documento con un
número que no es el timbrado es peor que un documento no emitido. La venta
queda en borrador y se confirma cuando el servicio vuelve.

### Se acaban los folios

El inventario avisa cuando quedan menos de 20. Pedir CAF al SII toma su tiempo,
así que se piden con holgura. Sin folios no se puede emitir: la venta espera, o
se emite con otro tipo de documento si corresponde.

### El CAF venció

El asignador lo salta solo y usa el siguiente. Los folios que quedaron sin usar
dentro del CAF vencido se declaran **anulados** en el SII.

### El certificado digital venció

No se puede firmar nada. Se renueva con el proveedor, se carga el nuevo en Odoo →
*DTE / SII* → *Certificado y CAF* → pestaña *Certificado digital*, y se
comprueba con el botón *Probar conexión* de la ficha de la compañía. Conviene renovarlo un mes antes: el aviso de vencimiento está en la
ficha de la compañía.

### Odoo no está disponible

La venta espera. Si no puede esperar, se emite por el portal gratuito del SII
(«Facturación electrónica MIPYME»), que usa folios del propio Servicio y no los
nuestros, y después se registra la venta en Odoo **sin volver a emitirla**: el
documento ya existe. Es el camino de excepción y hay que dejar constancia de
cada documento emitido así, para que la cuadratura del mes cuadre.

### Contingencia del SII declarada (emisión en papel)

Si el SII autoriza emitir en papel por contingencia, se usan facturas
preimpresas timbradas y después se informan al SII en el plazo que fije la
resolución. Para poder acogerse a esto hay que tener el talonario timbrado
**antes**: pedirlo cuando ya ocurrió la contingencia no sirve.

---

## Quién hace qué

| Función | Sistema | Persona |
| --- | --- | --- |
| Gestión de CAF | valida y controla el rango | pide y carga los CAF |
| Foliación | asigna y registra el desenlace | revisa y anula lo que corresponde |
| Respaldo | archiva el XML firmado | guarda certificado y CAF |
| Envío al SII | envía y consulta el estado | atiende rechazos y reparos |
| Intercambio | recibe, responde y registra en el SII | decide aceptar o reclamar |
| Cuadratura | entrega los datos | cuadra y declara |
| Contingencias | reintenta y no duplica | decide y deja constancia |
| Cesión | arma el AEC y lo anota en el SII | decide qué cede y a quién |

---

*Actualizado el 22-09-2026. Cualquier cambio en el sistema que afecte a un
procedimiento se refleja aquí en el mismo cambio.*
