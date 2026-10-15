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

## Actualización desde fuentes oficiales

| Herramienta | Qué hace |
|---|---|
| `herramientas/descargar_normas.py` | Baja cada código de `index.json` desde el servicio XML oficial de LeyChile (`https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=<id>`) y escribe `codigos/<slug>.md` con front-matter (`idNorma`, `titulo`, `fuente`, `fecha_version`, nota de vigencia). Todo o nada: si una norma falla, no escribe nada. Solo biblioteca estándar. |
| `herramientas/descargar_circulares.py` | Lee los índices anuales del SII (`https://www.sii.cl/normativa_legislacion/circulares/{año}/indcir{año}.htm`), escribe `circulares/metadata/{año}.json` y extrae el texto de cada PDF a `circulares/texto/{año}/circu{n}.txt` (requiere `pypdf`). Incremental. |
| `.github/workflows/actualizar-base.yml` | Corre ambos scripts cada lunes y a mano; si hay cambios en `main`, hace commit con el bot de Actions. Con `solo_probar: true` descarga y valida sin commitear. |

```bash
pip install -r herramientas/requirements.txt
python -m pytest -q tests
python herramientas/descargar_normas.py --solo-probar   # valida sin escribir
python herramientas/descargar_normas.py                 # escribe codigos/*.md
python herramientas/descargar_circulares.py --anos 2026
```

## Reglas de uso (Kickoff)

1. Leer primero `index.json`; retrieval por temas + palabras clave.
2. Citar siempre norma, artículo y fuente oficial (bcn.cl/leychile, sii.cl).
3. Todo texto lleva la nota: **verificar vigencia antes de citar**.

Fuentes oficiales: https://www.bcn.cl/leychile · https://www.sii.cl
Actualizado: 2026-08-27
