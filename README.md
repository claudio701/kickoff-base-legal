# Kickoff — Base Legal Chile

Base legal chilena de dominio público para el asistente jurídico-contable **Kickoff**.
Servida vía jsDelivr/raw para consumo directo desde la app web (frontend-only).

## Estructura

| Ruta | Contenido |
|---|---|
| `index.json` | Índice maestro: códigos, circulares y documentos con temas y rutas |
| `codigos/*.md` | 20 textos legales consolidados (Constitución, C. Tributario, IVA, Renta, Civil, Trabajo, Penal, Comercio, CPC, CPP, COT, LGB, LSA, LPI 19.039, Insolvencia 20.720, Consumidor 19.496, Ley 18.092, DFL 707, Ley 20.027, Territorial 17.235) con front-matter (idNorma, fuente LeyChile) |
| `circulares/metadata/{año}.json` | 873 circulares SII 2013–2026: número, fecha, materia, URL oficial del PDF |
| `circulares/texto/{año}/circu{n}.txt` | Texto completo de cada circular (despliegue progresivo) |
| `documentos/*.md` | 5 informes de investigación jurídica (prescripción, usufructo vs. hipoteca, derechos del niño, jurisprudencia administrativa SII, magíster UAI) |

## Reglas de uso (Kickoff)

1. Leer primero `index.json`; retrieval por temas + palabras clave.
2. Citar siempre norma, artículo y fuente oficial (bcn.cl/leychile, sii.cl).
3. Todo texto lleva la nota: **verificar vigencia antes de citar**.

## Procedencia de `codigos/`

Los 20 textos se tomaron del espejo público [pisanvs/ley-chile](https://github.com/pisanvs/ley-chile)
(rama `historial`, commit `5bea0546`, corte 2026-08-27), que los reconstruye desde LeyChile/BCN.
Cada archivo se emparejó por `idNorma` con este índice y lleva en su front-matter la URL oficial
y el origen. Se quitaron solo los comentarios internos `<!-- parte:N -->` del espejo.
Antes de citar: **verificar vigencia en LeyChile**.

Las circulares SII (`circulares/`) siguen pendientes.

Fuentes oficiales: https://www.bcn.cl/leychile · https://www.sii.cl
Actualizado: 2026-08-27
