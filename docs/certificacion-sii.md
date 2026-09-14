# Certificación SII — CONSTRUCTORA DIMABE SPA (77262159-0)

Estado y plan de la certificación como emisor de documentos tributarios
electrónicos. **Actualizar este archivo al avanzar**: es el punto de retome.

Última actualización: **2026-09-14**

---

## 1. Dónde estamos

Todo el trámite se hace ya **desde el portal**, no con scripts. El expediente
vive en `/customers/9/certification`, con su verificación previa, la emisión y
el envío.

Los envíos de agosto quedaron **obsoletos**: los 32 documentos de aquella tanda
fueron rechazados, aunque el sobre volviera `EPR`. Lo que sigue es la tanda de
hoy, con el motor ya corregido (§4).

| Set | N° atención | TrackID | Resultado |
|-----|-------------|---------|-----------|
| Set básico | 5038170 | `0258723732` | **8 de 8 aceptados** |
| Documentos de exportación (1) | 5038176 | `0258731398` | 1 aceptado, 2 con reparo → corregido, falta reenviar |
| Caso general factura de compra | 5038180 | `0258732820` | 3 con reparo → corregido, falta reenviar |
| Guía de despacho | 5038173 | — | por enviar |
| Factura exenta | 5038175 | — | por enviar |
| Documentos de exportación (2) | 5038177 | — | por enviar |
| Liquidación factura | 5038178 | — | por enviar |
| Libro de ventas | 5038171 | — | al final: se arma con los documentos aceptados |
| Libro de compras | 5038172 | — | al final |
| Libro de guías | 5038174 | — | al final |

**`EPR` describe al sobre, no a su contenido.** Un sobre procesado puede traer
todos sus documentos rechazados dentro. El portal ya lo distingue: pinta rojo si
no hay ninguno aceptado y ámbar si hay reparos.

**Un documento «aceptado con reparo» está aceptado.** El SII lo registra y anota
una observación; los rechazados van en su propia columna. Cuenta para el Libro
de Ventas.

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

## 3. Los libros

Los tres libros van **al final**, cuando todos los sets de documentos tengan su
envío aceptado. No se transcriben: el sistema arma las líneas de los libros de
ventas y de guías con los documentos que el SII aceptó de cada set
(`certification_fill.book_lines`). El de compras sí es dato del caso — sus
documentos los entrega el SII en el propio set, no los emite el contribuyente.

De cada set se toma el **último envío aceptado**, que es el que tiene los folios
que el Servicio conoce. Un set sin envío aceptado no aporta líneas y la
verificación lo avisa, en vez de armar un libro que declara folios inexistentes.

Los envíos de libros de agosto (`0257259954`, `0257260578`, `0257264862`)
quedaron obsoletos: declaraban documentos de la tanda rechazada.

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

### Errores anteriores (agosto)

| Problema | Causa | Dónde |
|---|---|---|
| Envíos rechazados (`RFR`) | El RUT que firma no tenía permiso de **envío** en la empresa. El SII lo reporta como error de firma | Mi SII (§5) |
| Libro descuadrado (`LRH`) | Campos cruzados entre libros: `TotOpIVARec` es (LC) y se emitía en ventas; `IVARetTotal` es (LV) y se emitía en compras | `book.py` |
| Carátula inválida (`LRC`) | El IECV se enviaba siempre como `MENSUAL`. El set se entrega como **ESPECIAL** con su número de atención | `book.py`, API |
| Línea de liquidación que no cierra | Faltaban las comisiones, que se descuentan del total. Van dentro de `<Liquidaciones>` | `book.py` |
| Libro mal formado llegaba al SII | Los libros no se validaban contra el XSD antes de enviarse | `book_service.py` |
| Consulta de estado imposible | `getEstUp` mandaba `Rut`/`Dv`; el WSDL declara `RutCompania`/`DvCompania` | `sii_client.py` |

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

Todo el trámite se opera desde `/customers/9/certification`.

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

---

## 7. Dónde está cada cosa

| Qué | Dónde |
|---|---|
| Definiciones de los sets | `docs/certificacion/definiciones-77262159-0.json` |
| Plantilla de los 10 sets | `app/services/certification_template.py` |
| Lo que el sistema completa al emitir | `app/services/certification_fill.py` |
| Verificación previa | `app/services/certification_checks.py` |
| Set de pruebas del SII | `SIISetDePruebas772621590.txt` (Descargas). **Es la fuente de los montos**: dice qué ítems y cantidades lleva cada caso, y cuándo un documento hereda los de otro |
| CAF y certificado | `C:\desarrollo\caf\` (fuera de todo repo) |
| Manual del ambiente | https://www.sii.cl/servicios_online/docs/manual_certificacion.pdf |
| Mantención de usuarios en Maullín | https://maullin.sii.cl/cvc_cgi/dte/eu_enrola_usuarios |

Cliente en el servicio: **id 9**, RUT 77262159-0, ambiente `CERTIFICATION`, los
11 CAF (rangos 1-50, el 39 hasta 100) y el certificado de **12291733-9**, que
tiene los seis atributos en Maullín y vence el 2029-08-18.
