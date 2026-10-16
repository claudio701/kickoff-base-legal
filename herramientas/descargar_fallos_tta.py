#!/usr/bin/env python3
"""Descarga los "Fallos Relevantes" que publican los Tribunales Tributarios y Aduaneros (TTA).

Fuente oficial (formato verificado en corridas de GitHub Actions sobre tta.cl, oct. 2026):

- Canal RSS 2.0 público de WordPress de la categoría "Fallos Relevantes":
  ``https://www.tta.cl/category/fallos-relevantes/feed/?paged={n}`` (10 entradas por página; la
  página siguiente a la última responde 404). Cada ``<item>`` trae ``<title>``, ``<link>``,
  ``<pubDate>``, ``<guid>https://www.tta.cl/?p={id}</guid>``, ``<category>`` (etiquetas),
  ``<description>`` (extracto) y ``<content:encoded>`` (reseña completa en HTML con enlaces a
  los PDF de las sentencias en ``https://www.tta.cl/wp-content/uploads/...pdf``).
- ``https://www.tta.cl/robots.txt`` permite todo salvo un JSON interno de un plugin. El script
  lo lee al empezar y no pide ninguna URL que robots.txt prohíba.

Salida (rutas registradas en ``index.json``, sección ``fallos_tta``):

- ``tta/metadata.json``: lista con ``id``, fecha de publicación, título (carátula), materia
  (extracto oficial), etiquetas, URL oficial de la entrada y de cada PDF, y la ruta del texto.
- ``tta/texto/{id}.md``: reseña del TTA + texto extraído de cada sentencia en PDF, con
  front-matter (``id``, ``titulo``, ``fuente``, ``fecha``) y la nota "verificar vigencia antes
  de citar".

Reglas de cortesía: User-Agent identificable con la URL del repositorio, al menos 5 s entre
pedidos, respeta ``Retry-After`` y es incremental (no vuelve a bajar los PDF de una entrada ya
completa cuyos enlaces no cambiaron; ``--forzar`` lo obliga). Escrituras atómicas; la metadata
solo se escribe si el canal RSS se leyó completo. Si algo falla, termina con código 1.

Requiere ``pypdf`` (versión fijada en ``herramientas/requirements.txt``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from descargar_circulares import ErrorCircular, extraer_texto  # noqa: E402
from descargar_normas import _espera_reintento, escribir_atomico  # noqa: E402

URL_REPO = "https://github.com/claudio701/kickoff-base-legal"
USER_AGENT = f"Mozilla/5.0 (compatible; kickoff-base-legal/1.0; +{URL_REPO})"
AGENTE_ROBOTS = "kickoff-base-legal"
SITIO = "https://www.tta.cl"
URL_CATEGORIA = f"{SITIO}/category/fallos-relevantes/"
URL_FEED = f"{URL_CATEGORIA}feed/?paged={{pagina}}"
URL_ROBOTS = f"{SITIO}/robots.txt"
NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
RAIZ_REPO = Path(__file__).resolve().parent.parent
CARPETA = "tta"
PAUSA_MINIMA = 5.0
MAX_PAGINAS = 60
MIN_CARACTERES_PDF = 200
ETIQUETAS_GENERICAS = {"fallos relevantes", "noticias"}
NOTA_VIGENCIA = (
    "Reseña y sentencias publicadas por los Tribunales Tributarios y Aduaneros en tta.cl; "
    "el texto de los PDF se extrajo automáticamente. Verificar vigencia antes de citar."
)

_RE_ID = re.compile(r"[?&]p=(\d+)")
_RE_PDF = re.compile(r"<a\b[^>]*href=[\"']([^\"']+\.pdf)[\"'][^>]*>", re.IGNORECASE)
_RE_ETIQUETA = re.compile(r"<[^>]+>")
_RE_PIE = re.compile(r"^La entrada .* se publicó primero en .*$")


class ErrorTTA(Exception):
    """No se pudo bajar o interpretar el canal RSS o un documento del TTA."""


# --------------------------------------------------------------------------- parseo


def html_a_texto(fragmento: str) -> str:
    """HTML de WordPress a texto plano por líneas (sin estilos, scripts ni pie del RSS)."""
    cuerpo = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", fragmento, flags=re.I | re.S)
    cuerpo = re.sub(r"<!--.*?-->", " ", cuerpo, flags=re.S)
    cuerpo = re.sub(r"<br\s*/?>|</(p|div|h\d|li|tr|ul|ol|table|section)\s*>", "\n", cuerpo, flags=re.I)
    lineas = []
    for linea in cuerpo.splitlines():
        limpia = re.sub(r"[ \t ]+", " ", html.unescape(_RE_ETIQUETA.sub(" ", linea))).strip()
        if limpia and not _RE_PIE.match(limpia):
            lineas.append(limpia)
    return "\n".join(lineas)


def _fecha_iso(pub_date: str) -> str | None:
    try:
        return email.utils.parsedate_to_datetime(pub_date).date().isoformat()
    except (TypeError, ValueError):
        return None


def parsear_feed(contenido: bytes) -> list[dict]:
    """Extrae las entradas de una página del canal RSS de "Fallos Relevantes"."""
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as exc:
        raise ErrorTTA(f"el canal RSS no es XML válido: {exc}") from exc
    canal = raiz.find("channel")
    if raiz.tag != "rss" or canal is None:
        raise ErrorTTA("la respuesta no es un canal RSS 2.0")
    entradas: list[dict] = []
    for item in canal.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        enlace = (item.findtext("link") or "").strip()
        coincidencia = _RE_ID.search(guid)
        if coincidencia is None or not enlace.startswith(SITIO + "/"):
            raise ErrorTTA(f"entrada sin id o enlace reconocible: guid={guid!r} link={enlace!r}")
        post_id = int(coincidencia.group(1))
        contenido_html = item.findtext(NS_CONTENT) or ""
        pdfs: list[str] = []
        for href in _RE_PDF.findall(contenido_html):
            url = urllib.parse.urljoin(enlace, html.unescape(href))
            if url not in pdfs:
                pdfs.append(url)
        etiquetas = [
            (c.text or "").strip()
            for c in item.findall("category")
            if (c.text or "").strip() and (c.text or "").strip().lower() not in ETIQUETAS_GENERICAS
        ]
        entradas.append(
            {
                "id": f"tta-{post_id}",
                "post_id": post_id,
                "titulo": html.unescape((item.findtext("title") or "").strip()),
                "fecha": _fecha_iso(item.findtext("pubDate") or ""),
                "materia": html_a_texto(item.findtext("description") or "").replace("\n", " "),
                "etiquetas": etiquetas,
                "url": enlace,
                "pdfs": pdfs,
                "resena": html_a_texto(contenido_html),
            }
        )
    return entradas


def _escapar_md(texto: str) -> str:
    """Evita que una línea del PDF que empiece con ``#`` se lea como encabezado markdown."""
    return "\n".join("\\" + l if l.lstrip().startswith("#") else l for l in texto.splitlines())


def _yaml(valor: str) -> str:
    return json.dumps(valor, ensure_ascii=False)


def componer_markdown(entrada: dict, sentencias: list[dict]) -> str:
    """Arma ``tta/texto/{id}.md``: front-matter, reseña oficial y texto de cada sentencia."""
    partes = [
        "---",
        f"id: {entrada['id']}",
        f"titulo: {_yaml(entrada['titulo'])}",
        f"fuente: {entrada['url']}",
        f"fecha: {entrada['fecha'] or 'desconocida'}",
        "tipo: fallo_relevante_tta",
        f"nota: {_yaml(NOTA_VIGENCIA)}",
        "---",
        "",
        f"# {entrada['titulo']}",
        "",
        f"> Fuente oficial: {entrada['url']} (publicado en tta.cl el {entrada['fecha'] or 'fecha desconocida'}).",
        f"> {NOTA_VIGENCIA}",
        "",
        "## Reseña publicada por el TTA",
        "",
        _escapar_md(entrada["resena"]) or "(sin reseña en el canal RSS)",
        "",
    ]
    for sentencia in sentencias:
        nombre = urllib.parse.unquote(sentencia["url"].rsplit("/", 1)[-1])
        partes += [f"## Documento: {nombre}", "", f"Fuente oficial: {sentencia['url']}", ""]
        if sentencia.get("texto"):
            partes += [_escapar_md(sentencia["texto"]), ""]
        else:
            partes += [f"(sin texto extraíble: {sentencia.get('motivo', 'PDF escaneado o vacío')})", ""]
    return "\n".join(partes).rstrip() + "\n"


# --------------------------------------------------------------------------- red


class Cliente:
    """GET con User-Agent identificable, pausa mínima entre pedidos, Retry-After y robots.txt."""

    def __init__(self, pausa: float, intentos: int = 4, timeout: float = 90.0) -> None:
        self.pausa = max(PAUSA_MINIMA, pausa)
        self.intentos = intentos
        self.timeout = timeout
        self._ultimo = 0.0
        self.pedidos = 0
        self._robots: urllib.robotparser.RobotFileParser | None = None

    def _esperar(self) -> None:
        falta = self._ultimo + self.pausa - time.monotonic()
        if falta > 0:
            time.sleep(falta)

    def get(self, url: str, *, permitir_404: bool = False) -> bytes | None:
        if self._robots is not None and not self._robots.can_fetch(AGENTE_ROBOTS, url):
            raise ErrorTTA(f"robots.txt de tta.cl prohíbe {url}; no se descarga")
        peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        ultimo: Exception | None = None
        for intento in range(1, self.intentos + 1):
            self._esperar()
            self._ultimo = time.monotonic()
            self.pedidos += 1
            try:
                with urllib.request.urlopen(peticion, timeout=self.timeout) as respuesta:
                    return respuesta.read()
            except urllib.error.HTTPError as exc:
                ultimo = exc
                if exc.code == 404 and permitir_404:
                    return None
                if exc.code not in (429, 500, 502, 503, 504):
                    raise ErrorTTA(f"HTTP {exc.code} en {url}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                ultimo = exc
            if intento < self.intentos:
                time.sleep(_espera_reintento(ultimo, intento))
        raise ErrorTTA(f"no se pudo descargar {url} tras {self.intentos} intentos: {ultimo}")

    def cargar_robots(self) -> str:
        """Lee robots.txt con este mismo agente. 404 = sin restricciones; otro error = abortar."""
        datos = self.get(URL_ROBOTS, permitir_404=True)
        texto = "" if datos is None else datos.decode("utf-8", errors="replace")
        analizador = urllib.robotparser.RobotFileParser()
        analizador.parse(texto.splitlines())
        self._robots = analizador
        for url in (URL_FEED.format(pagina=1), f"{SITIO}/wp-content/uploads/ejemplo.pdf"):
            if not analizador.can_fetch(AGENTE_ROBOTS, url):
                raise ErrorTTA(f"robots.txt de tta.cl prohíbe {url}; se aborta la fuente")
        return texto


# --------------------------------------------------------------------------- principal


def _resumen_github(filas: list[str]) -> None:
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if not ruta:
        return
    with open(ruta, "a", encoding="utf-8") as salida:
        salida.write("## Fallos relevantes TTA\n\n" + "\n".join(filas) + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raiz", type=Path, default=RAIZ_REPO)
    parser.add_argument("--forzar", action="store_true", help="vuelve a bajar entradas ya completas")
    parser.add_argument("--max-entradas", type=int, default=None, help="tope de entradas a procesar")
    parser.add_argument(
        "--solo-probar",
        action="store_true",
        help="lee robots.txt y la primera página del RSS y baja 2 entradas, sin escribir archivos",
    )
    parser.add_argument("--pausa", type=float, default=6.0, help=f"segundos entre pedidos (mín. {PAUSA_MINIMA:g})")
    args = parser.parse_args(argv)

    cliente = Cliente(args.pausa)
    errores: list[str] = []
    try:
        robots = cliente.cargar_robots()
    except ErrorTTA as exc:
        print(f"FALLÓ  robots.txt: {exc}", file=sys.stderr)
        return 1
    print(f"OK     robots.txt de tta.cl leído ({len(robots)} bytes); rutas del RSS y PDF permitidas", flush=True)

    entradas: list[dict] = []
    feed_completo = False
    for pagina in range(1, MAX_PAGINAS + 1):
        try:
            datos = cliente.get(URL_FEED.format(pagina=pagina), permitir_404=pagina > 1)
            if datos is None:
                feed_completo = True
                break
            nuevas = parsear_feed(datos)
        except ErrorTTA as exc:
            errores.append(f"RSS página {pagina}: {exc}")
            print(f"FALLÓ  RSS página {pagina}: {exc}", file=sys.stderr, flush=True)
            break
        print(f"OK     RSS página {pagina}: {len(nuevas)} entradas", flush=True)
        if not nuevas:
            feed_completo = True
            break
        entradas.extend(nuevas)
        if args.solo_probar:
            break
    if not entradas:
        errores.append("el canal RSS no entregó ninguna entrada (¿cambió el formato?)")

    ruta_meta = args.raiz / CARPETA / "metadata.json"
    anterior: dict[int, dict] = {}
    if ruta_meta.is_file():
        anterior = {e["post_id"]: e for e in json.loads(ruta_meta.read_text(encoding="utf-8"))}

    tope = 2 if args.solo_probar and args.max_entradas is None else args.max_entradas
    vistos: set[int] = set()
    metadata: list[dict] = []
    procesadas = pdf_ok = pdf_sin_texto = 0
    filas = ["| id | fecha | PDF | caracteres | título |", "|---|---|---|---|---|"]
    for entrada in entradas:
        if entrada["post_id"] in vistos:
            continue
        vistos.add(entrada["post_id"])
        relativo = f"{CARPETA}/texto/{entrada['id']}.md"
        ruta_texto = args.raiz / relativo
        previo = anterior.get(entrada["post_id"], {})
        registro = {k: v for k, v in entrada.items() if k not in ("resena", "pdfs")}
        registro["texto"] = relativo
        previos_pdf = [s["url"] for s in previo.get("sentencias", [])]
        if (
            not args.solo_probar
            and not args.forzar
            and previo.get("completo")
            and previos_pdf == entrada["pdfs"]
            and ruta_texto.is_file()
        ):
            metadata.append({**registro, "sentencias": previo["sentencias"], "completo": True})
            continue
        if tope is not None and procesadas >= tope:
            metadata.append(previo or {**registro, "texto": None, "sentencias": [], "completo": False})
            continue
        procesadas += 1
        sentencias: list[dict] = []
        for url_pdf in entrada["pdfs"]:
            sentencia: dict = {"url": url_pdf}
            if urllib.parse.urlsplit(url_pdf).netloc != urllib.parse.urlsplit(SITIO).netloc:
                sentencia["motivo"] = "enlace fuera de tta.cl: no se descarga"
                sentencias.append(sentencia)
                continue
            try:
                texto = extraer_texto(cliente.get(url_pdf) or b"", url_pdf)
            except (ErrorTTA, ErrorCircular) as exc:
                errores.append(f"{entrada['id']}: {exc}")
                print(f"FALLÓ  {entrada['id']}: {exc}", file=sys.stderr, flush=True)
                sentencia["motivo"] = "no se pudo descargar o leer"
                sentencia["error"] = True
                sentencias.append(sentencia)
                continue
            if len(texto) < MIN_CARACTERES_PDF:
                pdf_sin_texto += 1
                sentencia["motivo"] = f"PDF sin texto extraíble ({len(texto)} caracteres)"
            else:
                pdf_ok += 1
                sentencia["texto"] = texto
            sentencia["caracteres"] = len(texto)
            sentencias.append(sentencia)
        documento = componer_markdown(entrada, sentencias)
        completo = not any(s.get("error") for s in sentencias)
        meta_sentencias = [{k: v for k, v in s.items() if k != "texto"} for s in sentencias]
        metadata.append({**registro, "sentencias": meta_sentencias, "completo": completo})
        total_car = sum(s.get("caracteres", 0) for s in sentencias)
        filas.append(
            f"| {entrada['id']} | {entrada['fecha']} | {len(entrada['pdfs'])} | {total_car} | {entrada['titulo'][:70]} |"
        )
        print(
            f"OK     {entrada['id']} ({entrada['fecha']}): reseña {len(entrada['resena'])} car., "
            f"{len(entrada['pdfs'])} PDF, {total_car} car. de sentencias",
            flush=True,
        )
        if args.solo_probar:
            print("----- inicio del markdown generado -----\n" + documento[:1500] + "\n-----", flush=True)
        else:
            escribir_atomico(ruta_texto, documento)

    if not args.solo_probar and feed_completo and entradas:
        metadata.sort(key=lambda e: (e.get("fecha") or "", e["post_id"]), reverse=True)
        escribir_atomico(ruta_meta, json.dumps(metadata, ensure_ascii=False, indent=1) + "\n")
        ruta_indice = args.raiz / "index.json"
        indice = json.loads(ruta_indice.read_text(encoding="utf-8-sig"))
        bloque = {
            "total": len(metadata),
            "metadata": f"{CARPETA}/metadata.json",
            "textoPattern": f"{CARPETA}/texto/{{id}}.md",
            "fuente": URL_CATEGORIA,
            "nota": NOTA_VIGENCIA,
        }
        if indice.get("fallos_tta") != bloque:
            indice["fallos_tta"] = bloque
            escribir_atomico(ruta_indice, json.dumps(indice, ensure_ascii=False, indent=1) + "\n")
    elif not args.solo_probar:
        errores.append("el RSS no se leyó completo: no se actualizó tta/metadata.json")

    resumen = (
        f"Resumen: {len(vistos)} entradas en el RSS, {procesadas} procesadas, {pdf_ok} PDF con texto, "
        f"{pdf_sin_texto} PDF sin texto, {cliente.pedidos} pedidos HTTP, {len(errores)} errores."
    )
    print("\n" + resumen)
    _resumen_github([resumen, "", *filas])
    if errores:
        print("ERROR: fallaron:", file=sys.stderr)
        for error in errores:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
