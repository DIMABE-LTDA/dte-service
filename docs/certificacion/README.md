# Definiciones de los sets de certificación

`definiciones-77262159-0.json` son los once sets con que CONSTRUCTORA DIMABE SPA
obtuvo los diez `SOK` el 16-09-2026. Es el registro histórico de lo aprobado.

## Para un contribuyente nuevo no hace falta

Las definiciones se arman solas desde los archivos que entrega el SII: en el
expediente, **«Cargar los sets del contribuyente»**, subir el set de pruebas
(`SIISetDePruebas<RUT>.txt`) y el de boletas (`Set Prueba BE.txt`). El sistema
los lee, muestra qué va a cargar y recién entonces lo guarda. Si el archivo trae
algo que no entiende, se detiene y dice la línea.

El lector está en `app/services/certification_sheet.py`, con sus reglas y los
datos que la hoja no trae (receptor extranjero, mandante, contenedor, tipo de
cambio) declarados y explicados.

## Qué garantiza que funcione

`tests/test_certification_rehearsal.py` hace el camino completo con los archivos
de Dimabe (`tests/fixtures/sii/`): lee la hoja, carga los sets, emite los once y
compara el contenido de cada sobre con el del envío que el SII aprobó
(`tests/fixtures/certificacion/aprobados/`). Sólo se aceptan tres diferencias,
cada una con su regla en `app/services/certification_compare.py`:

- el destino de la guía de traslado interno y las marcas del bulto, que la hoja
  no trae y el set aprobado inventó distinto;
- las tildes de la liquidación: la hoja las trae y el set aprobado las quitó. El
  lector copia la hoja literal, como pide el instructivo.

Para verlo con los CAF reales, en local y sin enviar nada:

```
python scripts/certificacion_demo.py \
    --set tests/fixtures/sii/set_pruebas_77262159-0.txt \
    --boletas tests/fixtures/sii/set_boletas_77262159-0.txt \
    --cliente tests/fixtures/certificacion/cliente-77262159-0.json \
    --caf-dir C:/desarrollo/caf/77262159-0-certificacion \
    --aprobados <carpeta con los sobres aprobados, basico.xml, …>
```

## El JSON sigue sirviendo

`POST /admin/customers/{id}/certification/import` y la misma tarjeta del portal
aceptan este JSON, por ejemplo para clonar un expediente. `extraer_sets.py` es la
herramienta con que se generó desde los scripts originales.
