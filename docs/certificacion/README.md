# Definiciones de los sets de certificación

`definiciones-77262159-0.json` son los once sets del trámite de CONSTRUCTORA
DIMABE SPA, con el cuerpo exacto que hay que emitir en cada uno.

**No se transcribieron a mano.** Se extrajeron ejecutando los scripts originales
—los que el SII ya aceptó en nueve de diez sets— con el envío interceptado, así
que el cuerpo lo construyó el mismo código que funcionó. `extraer_sets.py` es
esa herramienta, y sirve para volver a hacerlo si los scripts cambian.

Se cargan en el portal con:

```
POST /admin/customers/{id}/certification/import
{"sets": { ...contenido del JSON... }}
```

Con eso el expediente queda con sus diez sets dados de alta y sabiendo qué
emitir en cada uno.

## Ojo con reutilizarlo para otro contribuyente

El SII asigna a **cada** RUT sus propios casos y su propio número de atención.
Este archivo es de Dimabe: para otra empresa sirve como punto de partida —el
portal tiene «Clonar» justo para eso— pero hay que revisar montos, receptores y
el número de atención de cada set antes de emitir.
