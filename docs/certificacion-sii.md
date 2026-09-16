# Certificación SII — CONSTRUCTORA DIMABE SPA (77262159-0)

Estado y plan de la certificación como emisor de documentos tributarios
electrónicos. **Actualizar este archivo al avanzar**: es el punto de retome.

Última actualización: **2026-09-16**

---

## 1. Dónde estamos

Todo el trámite se hace ya **desde el portal**, no con scripts. El expediente
vive en `/customers/1/certification`, con su verificación previa, la emisión y
el envío.

**Set de pruebas terminado: 10 de 10 con `SOK`** (16-09-2026). Lo que sigue son
los pasos 2 a 6 del §2, que dependen de gestiones fuera del sistema.

| Set | N° atención | Envío aprobado |
|-----|-------------|----------------|
| Set básico | 5038170 | `0258973214` |
| Guía de despacho | 5038173 | `0258724922` |
| Factura exenta | 5038175 | `0258973570` |
| Documentos de exportación (1) | 5038176 | `0258742558` |
| Documentos de exportación (2) | 5038177 | `0259009472` |
| Caso general factura de compra | 5038180 | `0258986342` |
| Liquidación factura | 5038178 | `0259003800` |
| Libro de guías | 5038174 | `0259013134` |
| Libro de compras | 5038172 | `0259013806` |
| Libro de ventas | 5038171 | `0259016980` |

El estado de cada set se ve en «Ver Avance de la Postulación»
(`/cvc_cgi/dte/pe_avance5` en Maullín). Va **un paso atrás del correo**: un set
con `SOK` puede seguir «En revisión» un rato, y se actualiza solo.

### Los dos portones, que son distintos

Es **la** distinción que más tiempo costó entender, y explica por qué un envío
«aceptado» seguía sin avanzar el trámite:

1. **Validación del sobre** — automática, en minutos. Mira estructura, firma y
   folios. Devuelve `EPR`/`LOK` si pasa, o `LRH`/`LNC`/`LRS`/`RCH` si no.
   Llega con el asunto *Resultado de Validacion de Envio de DTE*.
2. **Revisión del set** — contrasta el **contenido** contra el enunciado del
   caso. Devuelve `SOK` o `SRH`, y llega como `SETMAIL000<número de atención>`,
   *Resultado de Revision del Set de Prueba*.

Un sobre puede pasar el primero con todos sus documentos aceptados y reprobar el
segundo. **`EPR` no significa que el set esté bien.**

La **declaración del avance** es un tercer paso, administrativo: en
`/cvc_cgi/dte/pe_avance1` se informa qué N° de envío vale para cada set, y eso es
lo que mueve el portal a «Revisado conforme». El formulario deja llenar **una
sola fila** y dejar el resto vacías, y conviene hacerlo así: la respuesta del SII
queda atribuible a un único cambio.

> El propio formulario advierte que el envío declarado «no debe contener
> Documentos con Reparos o Rechazos». Declarar un envío objetado no sirve de
> nada.

**`EPR` describe al sobre, no a su contenido.** Un sobre procesado puede traer
todos sus documentos rechazados dentro. El portal ya lo distingue: pinta rojo si
no hay ninguno aceptado y ámbar si hay reparos.

**Un documento «aceptado con reparo» está aceptado.** El SII lo registra y anota
una observación; los rechazados van en su propia columna. Cuenta para el Libro
de Ventas.

### El método que funciona

Un cambio → emitir → validar el sobre en local → enviar → esperar el `EPR` →
declarar **sólo ese set** → esperar el `SETMAIL`. Así cada respuesta del Servicio
es atribuible a un solo cambio. Mezclar varios envíos fue lo que hizo perder dos
días: no se sabía qué había arreglado qué, ni si un reparo nuevo era consecuencia
del arreglo anterior.

### Cómo saber por qué el SII objetó algo

Dos caminos, y conviene conocer los dos:

1. **Por documento, desde el portal** — botón «Ver cada documento» en la fila del
   envío. Usa `QueryEstDte.jws`, que el módulo chileno de Odoo no implementa.
   Dice si el documento está registrado y si los datos coinciden, **pero no da
   la glosa del reparo**.
2. **Por correo** — en la página de estado del envío en Mi SII, *Enviar Correo*.
   Manda el informe con la sección «Detalle de Rechazos y Reparos», que es el
   único sitio donde aparece el motivo. **Es el camino bueno.**

> Ojo con `DNK` en la consulta por documento: significa que los datos de **la
> consulta** no coinciden con lo registrado, no que el documento tenga un
> problema. Engaña.

### Certificar un contribuyente nuevo

Todo lo de este documento costó descubrirlo; para el siguiente ya está resuelto.
El set de pruebas **no se transcribe**:

1. Datos del emisor, resolución, certificado y CAF en la ficha del cliente, y los
   receptores de prueba en el expediente.
2. En el expediente, **«Cargar los sets del contribuyente»**: subir los archivos
   que entregó el SII, el set de pruebas y el de boletas. El sistema los lee,
   muestra qué cargará y las indicaciones del SII que no interpreta, y lo guarda.
3. **«Verificación antes de emitir»**: tiene que quedar sin errores.
4. Set por set: emitir, enviar, esperar el `EPR`, declarar ese set solo, esperar
   el `SETMAIL`. Los libros al final.

Lo que protege que esto funcione está en `docs/certificacion/README.md`: un test
que emite los once sets desde la hoja de Dimabe y exige el mismo contenido que el
SII aprobó.

---

## 2. Plan

### Paso 1 — Set de pruebas *(terminado el 16-09-2026)*

- [x] Enviar los 10 sets y obtener `SOK` en cada uno
- [x] **Declarar el avance** de cada set en Mi SII, con fecha y N° de envío

### Paso 2 — Boletas

Es un trámite aparte, con su propio portal. **Lo que manda es el correo del SII**
que llega al descargar el set («Set Prueba BE.txt»). Las páginas de sii.cl se
contradicen entre sí (10 o 5 folios, RCOF sí o no, muestras por correo), y el
correo recibido el 16-09-2026 dice:

> 1) Obtener un CAF de boletas electrónicas que contenga un rango de **5 folios**.
> 2) Generar las boletas con la información del Set de Pruebas […] utilizando
>    los folios obtenidos en el punto anterior.
> 3) Enviar al SII el Set de Boletas generado **y el Reporte de Consumo de Folios
>    (RCOF) asociado** […] El envío del Set de Boletas debe ser **en solo un
>    archivo (sobre)**.
> 4) Solicitar la revisión del Set de Boletas enviado, informando el track ID […]
>
> Deben hacer todo lo anterior en un **plazo máximo de 24 horas** […] El plazo
> comienza una vez que bajan los folios de boletas electrónicas.
>
> El sitio web para consultar la boleta electrónica debe estar señalado en las
> representaciones impresas […] y disponible en la web previa aprobación.

Aunque la Res. Ex. 53/2022 eliminó el RCOF para la operación normal desde el
01-08-2022, **en la certificación se pide**.

**Cada cosa va por su canal**, según la especificación de la API de boleta
(`openapi.yaml` 1.0.5): las boletas por la API REST —envío a `pangal.sii.cl`,
token y consulta en `apicert.sii.cl`—; el RCOF por el upload de Maullín, porque
«palena.sii.cl es la plataforma dedicada para la recepción de DTE y RVD». Desde
el expediente, «Enviar» y «Consultar» eligen el canal solos.

**La referencia del caso va en `CodRef`**, no en `TpoDocRef`: la hoja pide
«`<CodRef> SET` · `<RazonRef> CASO-1`», y en la boleta `TpoDocRef` es numérico.

Todo esto está ensayado en `tests/test_certification_rehearsal.py` y en la demo:
el sobre de boletas y su RCOF validan contra el XSD con sus firmas, y el RCOF
reporta exactamente los folios y montos del sobre.

Con reloj — **nada de esto antes de tener el resto listo**:

- [ ] Descargar el CAF de **5 folios** del tipo 39. Empieza el plazo de 24 h.
- [ ] Retirar el CAF 39 vigente (1-100) con `POST /admin/customers/1/cafs/{id}/retire`:
      si no, el asignador sigue entregando folios del viejo.
- [ ] Cargar el CAF nuevo en la ficha.
- [ ] En el set de boletas: **Revisar y emitir** → validar el sobre → **Enviar**
      (API REST) → **Consultar**.
- [ ] En la fila del envío de boletas: **Generar RCOF** → **Enviar** (Maullín) →
      **Consultar**.
- [ ] Solicitar la revisión con el TrackID de las boletas en
      https://www4.sii.cl/certBolElectDteInternet/?SET=2
- [x] Sitio de consulta publicado: `boletas.dimabe.cl` responde.

### Paso 3 — Simulación

Un envío con la facturación real de los últimos 2 meses: máximo 100
documentos, mínimo 10.

### Paso 4 — Intercambio de información

Responder acuses de recibo y respuestas de DTE recibidos.

### Paso 5 — Muestras de impresión

Un PDF con la impresión de **todos** los documentos del set de pruebas más 10
de la simulación, con el timbre PDF417, a **sii_dte_impresos@sii.cl**.
`POST /dte/print` genera los impresos, incluidas las copias cedibles.

### Paso 6 — Declaración de cumplimiento

La hace el **representante legal** en el web del SII. Después de eso el SII
autoriza a operar.

---

## 3. Los libros

Los tres libros van **al final**, cuando los sets de documentos que los
alimentan estén aprobados. No se transcriben: el sistema arma las líneas de los
libros de ventas y de guías con los documentos que el SII aceptó
(`certification_fill.book_lines`). El de compras sí es dato del caso — sus
documentos los entrega el SII en el propio set, no los emite el contribuyente.

De cada set se toma el **último envío aceptado**, que es el que tiene los folios
que el Servicio conoce. Un set sin envío aceptado no aporta líneas y la
verificación lo avisa, en vez de armar un libro que declara folios inexistentes.

> **Consulta el estado del último envío antes de armar un libro.** Un envío
> recién hecho queda sin estado hasta que alguien pulsa «Consultar», y mientras
> tanto no cuenta como aceptado: el libro cae al envío anterior. Pasó con el
> libro de ventas, que salió con los folios de exportación de un sobre
> rechazado.

### El libro de ventas lleva sólo el set básico

Costó dos rechazos con `El Numero de Lineas de Resumen No Cuadra`, y está escrito
en la hoja del set, en la sección de ese libro:

> «CONSTRUYA EL LIBRO DE VENTAS CON LOS DOCUMENTOS CON QUE GENERÓ EL SET BÁSICO O
> EL SET DE FACTURA EXENTA, SEGÚN CORRESPONDA. **SI OBTUVO AMBOS SET, UTILICE LOS
> DOCUMENTOS DEL SET BÁSICO.**»

Se mandaban 26 documentos de cinco sets en 8 líneas de resumen; el SII esperaba
los 8 del básico en 3 (33, 56 y 61). El sobre volvía `LOK` las dos veces, y eso
se leyó como confirmación: `LOK` sólo dice que el libro cuadra consigo mismo.

### Reenviar un libro: sí se puede

Se llegó a creer que un libro recibido no admitía otro envío, por un `LNC` en el
libro de compras. Era una mala lectura: `LNC` es **«tipo de envío de libro no
corresponde»**, un problema de carátula. El 16-09 los tres libros se reenviaron
—guías y compras por tercera vez, ventas por cuarta— sin ningún permiso, y los
tres salieron `SOK`. `LTC – Libro Cerrado – Información Cuadrada` es el estado
normal de un libro aceptado, no un bloqueo.

El reemplazo formal existe, para otro escenario: `<TipoLibro>` = `RECTIFICA` con
un `<CodAutRec>` que pide un **representante legal** en el menú de certificación,
«Descargar código de autorización reemplazo de libro electrónico»
(`https://maullin.sii.cl/cgi_dte/UPL/DTEauth?11`). Sólo ofrece COMPRA y VENTA, y
obliga a aceptar que el reemplazo puede exigir rectificar F22, F29 y F50. En la
certificación no hizo falta.

### Qué no va en cada libro

- **52 guía de despacho** → va en el Libro de Guías.
- **46 factura de compra** → la emite el comprador; es una *compra* nuestra.

### Los códigos que devuelve el SII

| Código | Significa |
|---|---|
| `EPR` | Envío procesado (puede traer documentos con reparos dentro) |
| `LOK` | Libro aceptado y cuadrado |
| `LRH` | Libro rechazado: descuadrado |
| `LRS` | Libro rechazado por schema |
| `LRC` | Carátula de envío inválida |
| `LRF` | Libro rechazado por firma |
| `LNC` | Tipo de envío de libro no corresponde |
| `RFR` | Rechazado por error en firma (incluye *usuario no autorizado*) |

---

## 4. Errores encontrados y corregidos

Todos verificados contra el ambiente de certificación real.

### La tanda de septiembre: por qué el SII rechazaba TODO

Tres causas encadenadas, y las tres daban el mismo mensaje engañoso
—`(DTE-3-505) Firma DTE Incorrecta`— aunque la firma fuera correcta. Costaron
tres envíos y dos diagnósticos equivocados (el timbre, el permiso).

| Problema | Causa | Dónde |
|---|---|---|
| Timbre inválido | El CAF se incrustaba en el `<DD>` con sus saltos de línea. El DD se firma **plano y en ISO-8859-1** | `ted.py` (v0.4.1) |
| `DTE-3-505` | lxml quitaba el `xmlns` del `<DTE>` por redundante. El SII valida cada `<DTE>` **como fragmento suelto**, donde el namespace heredado no existe | `envelope.py` (v0.4.2) |
| `DTE-3-505` (de fondo) | El `<DTE>` declaraba `xmlns:xsi`. La C14N **inclusiva** lo mete en el digest, y en el fragmento no está | `signer.py` (v0.4.3) |

**Cómo se verifica un sobre como lo hace el SII.** Ésta es la lección que evita
repetirlo: la firma de un `<DTE>` se verifica con **ese `<DTE>` aislado**, no
con el sobre entero. `signer.verify_signatures` ya lo hace. Verificar contra el
sobre completo daba por buenas justo las firmas que el SII rechaza, y fue lo que
dejó pasar dos envíos malos con luz verde.

Odoo (`l10n_cl_edi`, certificado) confirma el criterio: su plantilla escribe
`<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0">` —sin `xsi`— y calcula el
digest sobre el `<Documento>` serializado por separado.

### Reparos: aceptado, pero con observación

| Código | Causa | Dónde |
|---|---|---|
| `HED-3-834` | Exportación sin `<OtraMoneda>`. El XSD la declara opcional; el SII la exige | `export_invoice.py` (v0.4.4) |
| `HED-2-804` | Exportación sin `Marcas` en el grupo de bultos. Igual: opcional en el XSD, obligatoria para el Servicio | `export_invoice.py` (v0.4.4) |
| `HED-2-302` / `HED-2-300` | La retención total (código 15) no declaraba su tasa. Retiene el IVA entero, así que su tasa **es** la del IVA | `xml_builder.py` (v0.4.6) |
| `HED-1-803` | Con forma de pago `S/PAGO` (21), los montos en otra moneda deben ir en **cero** | `export_invoice.py` (v0.4.6) |
| `REF-2-780` | Una nota que anula debe valer **lo mismo** que el documento que anula. El set no le da ítems propios: hereda los de su objetivo | definiciones |
| `DET L[n] -2-200` | El motor restaba el descuento de línea del `MontoItem`. El SII lo contrasta literal contra `PrcItem × QtyItem`, sin descuento — si el descuento afecta el total, va como `DscRcgGlobal` (tipo "D"), no descontado del detalle | `export_invoice.py` en `cl_dte_lib` (fix aún sin tag; ver vendor/) |
| `HED-2-220` | Consecuencia directa del anterior: `MntExe` no cuadraba porque sumaba el detalle ya descontado | mismo fix |
| `HED-2-804` (Sello / Id. Container) | Grupo de bultos sin `IdContainer`/`Sello`: opcionales en el XSD, obligatorios para el Servicio en cuanto se informa `<TipoBultos>`. El motor ya sabe serializarlos (`_transport()`); faltaban en el dato del set | `docs/certificacion/definiciones-77262159-0.json` (set exportación 2, folio 8) y la plantilla `_aduana()` en `certification_template.py`, para que no se repita con otro contribuyente |

### Errores anteriores (agosto)

| Problema | Causa | Dónde |
|---|---|---|
| Envíos rechazados (`RFR`) | El RUT que firma no tenía permiso de **envío** en la empresa. El SII lo reporta como error de firma | Mi SII (§5) |
| Libro descuadrado (`LRH`) | Campos cruzados entre libros: `TotOpIVARec` es (LC) y se emitía en ventas; `IVARetTotal` es (LV) y se emitía en compras. *Se «corrigió» de vuelta en septiembre y volvió a romper: ver la tanda del 16* | `book.py` |
| Carátula inválida (`LRC`) | El IECV se enviaba siempre como `MENSUAL`. El set se entrega como **ESPECIAL** con su número de atención | `book.py`, API |
| Línea de liquidación que no cierra | Faltaban las comisiones, que se descuentan del total. Van dentro de `<Liquidaciones>` | `book.py` |
| Libro mal formado llegaba al SII | Los libros no se validaban contra el XSD antes de enviarse | `book_service.py` |
| Consulta de estado imposible | `getEstUp` mandaba `Rut`/`Dv`; el WSDL declara `RutCompania`/`DvCompania` | `sii_client.py` |

### La tanda del 16 de septiembre

Cuatro errores de **contenido**, todos con su respuesta en documentación oficial
que estaba enlazada en el portal y que no habíamos leído. Ninguno era de firma ni
de esquema: los cuatro sobres pasaron el primer portón sin problema.

| Reparo del SII | Causa | Arreglo |
|---|---|---|
| `Los Valores de la Linea 1 del Detalle No Cuadran` (set 5038178) | La línea «NETO ANTICIPO FACTURACION» iba con `TpoDocLiq` 33. Un anticipo **no es** una factura electrónica: no hay documento que liquidar | `TpoDocLiq` = **99**. El formato lo dice: «código de documento válido (electrónico o manual) **o 99 en caso de anticipo u otras transacciones**». Verificado: `SOK` |
| `Resumen Doc 46 — No Informa Adecuadamente IVA Retenido Total` (set 5038172) | Se declaraba la retención con `<IVARetTotal>`, que el formato IECV define **sólo en el libro de VENTAS** (§2.4). El detalle de COMPRAS (§3.4) ni lo lista | `<OtrosImp>` con `CodImp` 15, `TasaImp` 19 y `MntImp` = retenido; en el resumen, `<TotOtrosImp>`. Motor v0.4.18 |
| `El Monto de Guias Modificadas No Cuadra` (set 5038174) | Se marcó con `MntModificado` la guía facturada. El formato del Libro de Guías reserva ese campo para `ANULADO/MODIFICADO=3`, que es **recepción parcial** | Quitado. Que una guía se facturó ya lo dice su tipo de operación (el 1 es «facturado o se facturará posteriormente») |
| `No Tiene un SET GUia de Despacho Aprobado` (set 5038174, primer intento) | No era de contenido: el libro de guías **no se puede aprobar** antes que el set de guías que lo alimenta | Orden de envío, no código |

#### El total de la factura de compra en el libro de compras

Costó tres vueltas, incluida una en que llegué al resultado correcto por el
argumento equivocado. La regla está en el formato IECV §3.4, campo 25, y es del
libro de compras:

> «Monto Neto + Monto Exento + IVA recuperable + IVA Uso común + IVA no
> recuperable + … **− IVA Retenido parcial y total** − …»

Es decir, con retención total el `MntTotal` **es el neto**: el comprador retiene
el IVA, no se lo paga al proveedor. El `MntIVA` sí se informa completo.

Cuidado con el ejemplo del SII en *Ejemplos de Registro de Documentos en la
IECV*, que muestra exactamente este caso (neto 75.000 / IVA 14.250 / total
75.000): está en la sección **«II. … Información Electrónica de VENTAS»** y dice
«deberá registrarla en el **libro de ventas**». Es la contraparte —quien recibe
la factura de compra—, no nuestro caso. Aplicarlo al libro de compras es
razonar sobre el libro equivocado aunque el número final coincida.

### Exportación (2): tres cambios, `SOK` al primer intento

Los tres casos del set 5038177 traían `Los Datos de la Linea 1 del Detalle No
Cuadran con lo Especificado`. Se resolvió comparando el sobre rechazado
(`0258981808`) contra `SIISetDePruebas772621590.txt` línea por línea, y el envío
`0259009472` salió `SOK`.

| Caso | La hoja del set dice | Se corrigió a |
|---|---|---|
| 1 | `VALOR LINEA 14` + «%10 RECARGO EN LA LINEA DE ITEM» | `QtyItem 1`, `PrcItem 14`, `RecargoPct 10`, **`RecargoMonto 1`**, `MontoItem 15` |
| 2 | línea 1: `239 KN × 114`, «DESCUENTO LINEA # 1: 5%» | `DescuentoPct 5`, **`DescuentoMonto 1362`**, `MontoItem 25884` |
| 3 | `VALOR LINEA 42`, `NACIONALIDAD: ALEMANIA` | `QtyItem 1`, `PrcItem 42`, y **2ª referencia** `TpoDocRef 813` (pasaporte) |

Tres lecciones que valen para cualquier set:

**1. El porcentaje nunca va solo.** La fórmula del campo 38 del formato DTE es
`MontoItem = (Precio Unitario × Cantidad) − Monto Descuento + Monto Recargo`, y
el propio formato exige que «si va el descuento en %, debe ir el monto
correspondiente». Declarar sólo el porcentaje deja al SII sin con qué reproducir
el monto, y los dos portones responden cosas distintas: la validación del sobre
da `DET L[n] -2-200` y la revisión del set, «no cuadra con lo especificado».

**2. Los montos de descuento y recargo son enteros aunque el documento vaya en
dólares.** `DescuentoMonto` y `RecargoMonto` son `MntImpType`, o sea
`xs:positiveInteger`: el SII reusó en exportación el tipo de los montos en
pesos. El 5% de 27.246 —1.362,30— se declara **1.362**, y el 10% de 14 se
declara **1**. No es una decisión: el XSD rechaza el documento si llevan
decimales.

**3. La línea lleva cantidad y precio aunque la hoja sólo dé el valor.** Los
casos 1 y 3 dan sólo «VALOR LINEA», y el instructivo dice «debe registrar los
valores de cantidad y precio unitario que se indiquen en cada caso». Se dedujo
por eliminación —la única línea del set que el SII **no** objetó era la única con
cantidad y precio— y resultó correcto: van con `QtyItem 1` y el valor como
`PrcItem`.

Y un dato que hubo que poner sin que la hoja lo diera: el caso 3 es hotelería
(`IndServicio` 4) y el SII exige 2 líneas de referencia. La primera es la del
`SET`; la segunda, según la FAQ oficial de factura de exportación, es el
**pasaporte del cliente extranjero** (`TpoDocRef` **813**). El enunciado no trae
número, así que se usó uno con formato válido. El SII verifica que la referencia
exista, no que el pasaporte sea real.

### La documentación del SII que hay que leer (y dónde está)

Casi todo lo de arriba estaba escrito. El error de método fue corregir por
inferencia en vez de buscar la fuente.

| Documento | Para qué sirve | URL |
|---|---|---|
| Instrucciones del set de pruebas | Reglas de construcción de **todos** los casos | https://www.sii.cl/factura_electronica/inst_set_pruebas.pdf |
| Formato IECV | Libros de compras y ventas, campo por campo y **por libro** | https://www.sii.cl/factura_electronica/factura_mercado/formato_iecv.pdf |
| Formato del Libro de Guías | Libro de guías | https://www.sii.cl/factura_electronica/formato_lgd.pdf |
| Ejemplos de registro en la IECV | Casos especiales con el XML literal | https://www.sii.cl/factura_electronica/casos_especiales_registro_documentos.pdf |
| Nuevas validaciones a la IECV | Las validaciones numeradas que el SII aplica a los libros | https://www.sii.cl/factura_electronica/compra_venta.pdf |
| Formato DTE | Todos los DTE, exportación incluida | https://www.sii.cl/factura_electronica/factura_mercado/formato_dte_202602.pdf |

Tres cosas del instructivo del set que valen por sí solas:

- **«En la glosa del ítem debe anotar exactamente lo indicado en el caso; debe
  incluir acentos, ñ u otros que se indiquen.»** Normalizar el texto costó siete
  rechazos.
- **«Las líneas de detalle deben ir en el orden especificado.»**
- **«En la primera línea de referencia … "SET" y "CASO xxxxx-x". Las otras
  referencias que sea preciso agregar … deben ir a partir de la línea 2.»** De
  ahí sale el reparo «El Documento Debe Tener N Linea(s) de Referencia».

Y una advertencia sobre el XSD: sus anotaciones `(LC)`/`(LV)` **no son fiables
por sí solas** para decidir en qué libro va un campo. `IVARetTotal` está anotado
`(LV)` y eso resultó correcto, pero la validación 31 del SII lo admite «en
liquidaciones, liquidaciones factura, facturas de compra, notas de crédito y
notas de débito», lo que parecía autorizarlo en compras. Quien manda es la tabla
de campos del formato, que es **distinta para cada libro**.

### La regla que resume todo

**El XSD describe la forma del documento, no las reglas del Servicio.** Van
cinco veces que un documento válido contra el esquema oficial se rechaza o se
objeta igual. Por eso las reglas que el SII no publica en el XSD se exigen en
`validate_content()`: es el único punto donde se ven **antes de gastar un
folio**.

---

## 5. El permiso de envío (resuelto, pero conviene saberlo)

**Los ambientes tienen registros de usuarios separados**: `maullin.sii.cl`
autentica contra `zeusr.sii.cl` y `palena.sii.cl` contra `zeus.sii.cl`. Un
permiso otorgado en producción **no** aplica en certificación.

Se habilita en **https://maullin.sii.cl/cvc_cgi/dte/eu_enrola_usuarios**
(Administración de Empresa Autorizada → *Mantención de Usuarios*), sólo por el
Usuario Administrador, marcando el atributo **Enviar Doctos**.

Sin ese atributo el SII **rechaza los envíos completos**, no sólo la consulta,
y lo informa como error de firma. Costó 10 envíos rechazados descubrirlo.

---

## 6. Herramientas del portal

Todo el trámite se opera desde `/customers/1/certification`.

| Para qué | Dónde |
|---|---|
| Ver qué falta antes de emitir | **Verificación antes de emitir** (firma un timbre por CAF, prueba el token; no gasta folios) |
| Empezar un contribuyente nuevo | **Empezar desde la plantilla del SII** — crea los 10 sets con su estructura; sólo se piden los números de atención |
| Cargar sets desde archivo | **Cargar los sets del contribuyente** |
| Ver qué se va a emitir | **Revisar y emitir** en cada set |
| Descartar un sobre sin enviar | **Descartar** en la fila del sobre (los folios no vuelven; para el SII siguen disponibles) |
| Motivo de un reparo | **Ver cada documento**, y sobre todo el correo del SII (§1) |
| Impresos con copias cedibles | `POST /dte/print` |
| Conciliar SII contra el ERP | `POST /rcv/reconcile` (en certificación el RCV viene vacío) |
| Cambiar qué emite un set | **Editar definición** en el set. Ojo: **las definiciones viven en la base de datos**, no en el JSON del repo. Cambiar `docs/certificacion/definiciones-…json` no cambia lo que se emite hasta que se reimporta |

### En el portal del SII: la opción que no hay que tocar

En `https://maullin.sii.cl/cvc/dte/postulacion.html` está **«Generación de Nuevo
Set de Pruebas»**, y su propio texto advierte: «al ejecutar esta operación
**vuelve al estado inicial**». Borraría los sets ya aprobados. La hoja del set ya
está descargada (§7); no hay motivo para volver a pedirla.

Las otras opciones del mismo menú, todas útiles:

| Opción | URL |
|---|---|
| Declarar Avance de la Postulación | `/cvc_cgi/dte/pe_avance1` |
| Ver Avance de la Postulación | `/cvc_cgi/dte/pe_avance5` |
| Documentación para Contribuyentes Postulantes | `/cvc_cgi/dte/ce_documentos` |
| Declaración de Cumplimiento de Requisitos | `/cvc_cgi/dte/pe_avance7` |

Todas piden autenticación **con certificado digital**; la sesión caduca sola y
hay que rehacerla desde `postulacion.html`.

---

## 7. Dónde está cada cosa

| Qué | Dónde |
|---|---|
| Definiciones de los sets | `docs/certificacion/definiciones-77262159-0.json` |
| Plantilla de los 10 sets | `app/services/certification_template.py` |
| Lo que el sistema completa al emitir | `app/services/certification_fill.py` |
| Verificación previa | `app/services/certification_checks.py` |
| **Hoja del set de pruebas** | `C:\Users\leonardo.norambuena\Downloads\SIISetDePruebas772621590.txt`. **Es la fuente de la verdad**: cuando el SII dice «no cuadra con **lo especificado**», se refiere a este archivo. Trae, caso por caso, la glosa exacta del ítem (con acentos), cantidad, precio unitario, descuentos y recargos, qué referencias lleva y las instrucciones al contribuyente. Se obtiene del portal de postulación, opción *Generación de Nuevo Set de Pruebas* — **pero esa opción reinicia el trámite** (ver §6), así que conviene guardar este archivo y no volver a pedirlo |
| CAF y certificado | `C:\desarrollo\caf\` (fuera de todo repo) |
| Manual del ambiente | https://www.sii.cl/servicios_online/docs/manual_certificacion.pdf |
| Mantención de usuarios en Maullín | https://maullin.sii.cl/cvc_cgi/dte/eu_enrola_usuarios |

Cliente en el servicio: **id 1**, RUT 77262159-0, ambiente `CERTIFICATION`, los
11 CAF (rangos 1-50, el 39 hasta 100) y el certificado de **12291733-9**, que
tiene los seis atributos en Maullín y vence el 2029-08-18.
