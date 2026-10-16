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
| `tta/metadata.json` | Fallos relevantes de los Tribunales Tributarios y Aduaneros (TTA): id, fecha, carátula, materia, etiquetas, URL oficial de la reseña y de cada PDF |
| `tta/texto/tta-{id}.md` | Reseña del TTA + texto extraído de cada sentencia en PDF, con front-matter y nota de vigencia (se genera en la primera corrida del workflow en `main`) |
| `documentos/*.md` | 5 informes de investigación jurídica (prescripción, usufructo vs. hipoteca, derechos del niño, jurisprudencia administrativa SII, magíster UAI) |

## Actualización desde fuentes oficiales

| Herramienta | Qué hace |
|---|---|
| `herramientas/descargar_normas.py` | Baja cada código de `index.json` desde el servicio XML oficial de LeyChile (`https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=<id>`) y escribe `codigos/<slug>.md` con front-matter (`idNorma`, `titulo`, `fuente`, `fecha_version`, nota de vigencia). Todo o nada: si una norma falla, no escribe nada. Solo biblioteca estándar. |
| `herramientas/descargar_circulares.py` | Lee los índices anuales del SII (`https://www.sii.cl/normativa_legislacion/circulares/{año}/indcir{año}.htm`), escribe `circulares/metadata/{año}.json` y extrae el texto de cada PDF a `circulares/texto/{año}/circu{n}.txt` (requiere `pypdf`). Incremental. |
| `herramientas/descargar_fallos_tta.py` | Lee el RSS público de WordPress `https://www.tta.cl/category/fallos-relevantes/feed/?paged=N`, escribe `tta/metadata.json` y `tta/texto/tta-{id}.md` (reseña + texto de los PDF de sentencias, con `pypdf`). Revisa `robots.txt` antes de pedir nada, User-Agent con la URL de este repo, ≥5 s entre pedidos, respeta `Retry-After`. Incremental. |
| `.github/workflows/actualizar-base.yml` | Corre los scripts cada lunes y a mano; si hay cambios en `main`, hace commit con el bot de Actions. Con `solo_probar: true` descarga y valida sin commitear. |

```bash
pip install -r herramientas/requirements.txt
python -m pytest -q tests
python herramientas/descargar_normas.py --solo-probar   # valida sin escribir
python herramientas/descargar_normas.py                 # escribe codigos/*.md
python herramientas/descargar_circulares.py --anos 2026
python herramientas/descargar_fallos_tta.py --solo-probar   # robots.txt + 1 página RSS + 2 fallos, sin escribir
python herramientas/descargar_fallos_tta.py                 # escribe tta/
```

El paso de fallos TTA corre con `continue-on-error`: si falla, avisa y no bloquea los códigos.
En `push` a ramas de trabajo el workflow corre en modo prueba con un solo código (`codigo-tributario`).

## Fuentes evaluadas (octubre 2026)

Revisión hecha desde GitHub Actions (esta es la red que usa el workflow). Solo se descarga
contenido público, sin login, sin captchas y respetando `robots.txt` y términos de uso.

| Fuente | Decisión | Evidencia |
|---|---|---|
| TTA — fallos relevantes (`tta.cl`) | **Implementada** | `robots.txt`: `User-agent: * / Disallow:` (solo bloquea un JSON de plugin). El sitio no publica página de términos de uso (no aparece en `page-sitemap.xml`). Se usa el RSS público de la categoría. |
| TTA — buscador de jurisprudencia OJV (`ojv.tta.cl`) | No implementada | Aplicación Angular cuya API interna (`https://ojv.tta.cl/api`, `/buscador`) usa token de aplicación y reCAPTCHA; no hay descarga documentada. |
| SII — jurisprudencia administrativa (oficios) | **Descartada** | `sii.cl/sobre_el_sii/terminos_sitio_web.html` §4: "Cualquier captura y posterior reproducción de parte de la información contenida en este sitio, por cualquier medio, debe ser autorizada expresamente por el SII" y el uso "sólo podrá ser de manera personal y no comercial"; §5 prohíbe reproducir o distribuir los contenidos "para propósitos públicos o comerciales" sin autorización escrita. Requiere autorización expresa del SII. |
| Contraloría — dictámenes | **Descartada** | `https://www.contraloria.cl/robots.txt`: `User-agent: * / Disallow: /`. No se encontró conjunto de dictámenes en datos.gob.cl (cuyo `robots.txt` además prohíbe `/api/`). |
| Tribunal Constitucional | **Descartada** | `tribunalconstitucional.cl` responde HTTP 403 a `/robots.txt` y a la portada desde GitHub Actions; no se evaden bloqueos. |
| Poder Judicial — `juris.pjud.cl` | **Descartada** | `robots.txt`: `User-agent: * / Disallow: /` (y bloqueos explícitos a bots de IA); la búsqueda usa reCAPTCHA. |

> Atención: la cláusula del SII citada arriba también alcanza a las circulares que ya descarga
> `descargar_circulares.py`. Antes de seguir publicándolas en este repositorio público conviene
> pedir autorización expresa al SII o dejarlas fuera del repositorio.

## Reglas de uso (Kickoff)

1. Leer primero `index.json`; retrieval por temas + palabras clave.
2. Citar siempre norma, artículo y fuente oficial (bcn.cl/leychile, sii.cl, tta.cl).
3. Todo texto lleva la nota: **verificar vigencia antes de citar**.

Fuentes oficiales: https://www.bcn.cl/leychile · https://www.sii.cl · https://www.tta.cl
Actualizado: 2026-08-27
