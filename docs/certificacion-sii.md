# Certificación SII — CONSTRUCTORA DIMABE SPA (77262159-0)

Estado y plan de la certificación como emisor de documentos tributarios
electrónicos. **Actualizar este archivo al avanzar**: es el punto de retome.

Última actualización: **2026-09-02**

---

## 1. Dónde estamos

En Mi SII → *Ver Avance de la Postulación*, la empresa está en el paso
**SET DE PRUEBAS** y los 10 sets figuran **POR REALIZAR**.

Eso **no** significa que no se haya enviado nada: los envíos están hechos y
aceptados. Significa que falta el paso de **Declarar Avance**, que es manual y
lo hace el Usuario Administrador informando fecha y TrackID de cada envío. El
SII no da un set por realizado hasta que se lo declaran.

### Envíos aceptados (ambiente de certificación, Maullín)

| Set | N° atención | TrackID | Estado |
|-----|-------------|---------|--------|
| Set básico | 5038170 | `0257259806` | EPR Envío Procesado |
| Guía de despacho | 5038173 | `0257259812` | EPR |
| Factura exenta | 5038175 | `0257259816` | EPR |
| Documentos de exportación (1) | 5038176 | `0257259820` | EPR |
| Documentos de exportación (2) | 5038177 | `0257259824` | EPR |
| Liquidación factura | 5038178 | `0257259826` | EPR |
| Caso general factura de compra | 5038180 | `0257259828` | EPR |
| Libro de guías | 5038174 | `0257259954` | LOK Aceptado - Cuadrado |
| Libro de compras | 5038172 | `0257260578` | LOK Aceptado - Cuadrado |
| Libro de ventas | 5038171 | `0257264862` | LOK, pero con contenido incompleto (§3) |

`EPR` = el **sobre** fue procesado. Puede haber documentos con reparos dentro:
conviene revisar el detalle en Mi SII o en el correo que el SII envía a la
casilla de contacto de la empresa.

---

## 2. Plan

### Paso 1 — Set de pruebas *(en curso)*

- [x] Enviar los 9 sets de documentos y libros aceptados
- [ ] **Cerrar el Libro de Ventas** (§3)
- [ ] **Declarar el avance** de cada set en Mi SII, con fecha y TrackID
- [ ] Revisar que ningún documento quede con reparos

### Paso 2 — Boletas *(bloqueado por decisión del usuario)*

El set de boletas es aparte y tiene reloj: al descargar el CAF corren **24
horas** para enviar el set completo en **un solo envío**.

- [ ] Pedir el CAF de **5 folios** del tipo 39 — *sólo cuando todo lo demás
      esté listo*
- [ ] Retirar el CAF vigente 1-100 con
      `POST /admin/customers/9/cafs/{id}/retire` (si no, el asignador sigue
      entregando folios del viejo y nunca usa el nuevo)
- [ ] Cargar el CAF nuevo y correr `set_boletas.py`
- [ ] Enviar el RCOF (reporte de consumo de folios) del día
- [ ] Publicar el sitio de consulta de boletas (`boletas.dimabe.cl`) — el SII
      exige que la boleta impresa indique dónde consultarla, y que el sitio
      esté publicado antes de aprobar. El código está listo en `boletas-web/`.

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

## 3. Lo único pendiente del paso 1: el Libro de Ventas

**Hay un libro de ventas ACEPTADO** (`LOK`, TrackID `0257264862`), pero con un
contenido que no es el que pide el set: sólo 4 documentos. Conviene revisar en
Mi SII si el Servicio dio el set por realizado con ése o si espera otro.

### Bitácora de intentos

Cada fila es un envío real. Sirve para no repetir el mismo experimento.

| TrackID | Contenido | TipoLibro | Respuesta |
|---|---|---|---|
| `0257260576` | 8 docs del set básico | ESPECIAL | LRH descuadrado |
| `0257260754` | + `TotMntPeriodo` | ESPECIAL | LRH descuadrado |
| `0257261788` | 27 docs (todo el período) | ESPECIAL | LRS schema |
| `0257262778` | + comisiones de liquidación | ESPECIAL | LRS schema |
| `0257264456` | 17 docs, sin exportación ni 43 | ESPECIAL | LRS schema |
| `0257264862` | **4 docs: 33/18 + 34×3** | ESPECIAL | **LOK cuadrado** |
| `0257265434` | 27 docs + `TpoDocRef` en notas | ESPECIAL | LRS schema |
| `0257265702` | 33/18 + 61×6 | ESPECIAL | LRH descuadrado |
| `0257265912` | 33/18 + 56×3 | ESPECIAL | LRH descuadrado |
| `0257266180` | 33×5 | ESPECIAL | LNC tipo de envío |
| `0257266494` | 8 docs del set básico | ESPECIAL | LRH descuadrado |
| `0257266994` | + montos en cero declarados | ESPECIAL | LNC tipo de envío |
| `0257267198` | igual | RECTIFICA | LRC carátula inválida |

### Lo que quedó descartado

- **No es la firma.** Se verificaron con `xmlsec` los archivos enviados y todas
  las firmas validan criptográficamente.
- **No es el permiso.** Está habilitado (§5) y el resto de los envíos pasa.
- **No es nuestro XSD.** El libro valida contra `LibroCV_v10.xsd` en local, y
  ese archivo es **idéntico** al que publica el SII (se descargó y comparó).
- **No es la exportación ni la liquidación.** Un libro sin los tipos 110/111/112
  ni 43 igual dio `LRS`.
- **No es el tipo 34.** El libro aceptado los incluye.
- **No es que falten documentos del período.** El libro aceptado tiene 4 de 33,
  así que el SII no exige declarar todo el período. *(Esto contradice una
  hipótesis anterior de este documento, que era incorrecta.)*

### Lo que sí se observó

- **Todos los libros rechazados contienen documentos con total cero**; el
  aceptado, ninguno. De ahí el cambio de declarar siempre los tres montos
  aunque valgan cero. **Sin confirmar**: el Servicio empezó a responder por el
  estado del período antes de volver a evaluar el contenido.
- Después del `LOK`, los envíos siguientes del mismo período responden `LNC`
  ("tipo de envío no corresponde"). Ojo: `RECTIFICA` es un **TipoLibro**, no un
  TipoEnvio; enviarlo así devuelve `LRC`, así que la carátula de una
  rectificación necesita algo más que aún no identificamos.

### Cómo seguir

El camino barato ya no es probar: **es leer el motivo del rechazo**. El SII lo
detalla en Mi SII → Revisar envíos, y lo manda por correo a la casilla de
contacto de la empresa. Ahí dice qué total no le cuadra, en vez de deducirlo a
ciegas. Con eso se cierra en un intento.

### Composición del libro

`set_5038171_libro_ventas.py` acepta tres modificadores para experimentar:
`--setbasico` (sólo los 8 documentos del set), `--solo=33,61` (limita los
tipos) y `--tipo=RECTIFICA` (cambia el TipoLibro).

Quedan fuera a propósito:
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

| Problema | Causa | Dónde |
|---|---|---|
| Envíos rechazados (`RFR`) | El RUT que firma no tenía permiso de **envío** en la empresa. El SII lo reporta como error de firma | Mi SII (§5) |
| Libro descuadrado (`LRH`) | Campos cruzados entre libros: `TotOpIVARec` es (LC) y se emitía en ventas; `IVARetTotal` es (LV) y se emitía en compras | `book.py` |
| Carátula inválida (`LRC`) | El IECV se enviaba siempre como `MENSUAL`. El set se entrega como **ESPECIAL** con su número de atención | `book.py`, API |
| Línea de liquidación que no cierra | Faltaban las comisiones, que se descuentan del total. Van dentro de `<Liquidaciones>`, no colgando del `<Detalle>` | `book.py` |
| Libro mal formado llegaba al SII | Los libros no se validaban contra el XSD antes de enviarse, a diferencia de los documentos | `book_service.py` |
| Consulta de estado imposible | `getEstUp` mandaba los parámetros como `Rut`/`Dv`; el WSDL declara `RutCompania`/`DvCompania` | `sii_client.py` |

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

## 6. Herramientas del servicio

| Para qué | Endpoint |
|---|---|
| Estado de un envío | `GET /dte/status/{track_id}` |
| Conciliar SII contra el ERP | `POST /rcv/reconcile` |
| Documentos registrados en el SII | `POST /rcv/documents` |
| Sacar un CAF de circulación | `POST /admin/customers/{id}/cafs/{caf_id}/retire` |
| Impresos (con copias cedibles) | `POST /dte/print` |

`/rcv/reconcile` existe justamente por el problema del §3: cruza lo que el SII
tiene registrado contra lo que trae Odoo y separa qué falta en cada lado y qué
está en ambos con montos distintos.

> Ojo: en certificación el RCV viene **vacío** (`count: 0`). La conciliación
> sirve en producción; para armar los libros de certificación hay que leer los
> sobres enviados.

---

## 7. Dónde está cada cosa

| Qué | Dónde |
|---|---|
| Scripts de los sets | scratchpad de la sesión, `set_50381*.py` |
| Sobres enviados y TrackIDs | `C:\desarrollo\caf\envios-prueba\` |
| CAF y certificado | `C:\desarrollo\caf\` (fuera de todo repo) |
| Set de pruebas del SII | `SIISetDePruebas772621590.txt`, `Set Prueba BE.txt` |
| Manual del ambiente | https://www.sii.cl/servicios_online/docs/manual_certificacion.pdf |

Cliente en el servicio: **id 9**, RUT 77262159-0, ambiente `CERTIFICATION`,
con los 11 CAF y el certificado de **12291733-9** (vence 2029-08-18).
