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

Fuentes oficiales: https://www.bcn.cl/leychile · https://www.sii.cl
Actualizado: 2026-08-27
