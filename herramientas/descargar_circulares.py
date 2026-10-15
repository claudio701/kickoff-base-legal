#!/usr/bin/env python3
"""Descarga metadata y texto de las circulares del SII (2013 en adelante) desde sii.cl.

Fuentes oficiales (formato verificado en una copia guardada del índice 2026 del SII):

- Índice anual: ``https://www.sii.cl/normativa_legislacion/circulares/{año}/indcir{año}.htm``.
  Cada circular aparece como::

      <h5 ...><a href="circu38.pdf" ...>Circular N° 38 del 11 de Septiembre del 2026</a></h5>
      <p ...>Materia de la circular.</p>
      <span ...><i>Fuente: Subdirección Normativa</i></span>

  (las dejadas sin efecto llevan ``class="sin_efecto"`` en el enlace).
- Documento: ``https://www.sii.cl/normativa_legislacion/circulares/{año}/circu{n}.pdf``
  (relativo al índice).

Salida (rutas de ``index.json``):

- ``circulares/metadata/{año}.json``: lista con número, fecha, título, materia, fuente interna,
  URL oficial, si quedó sin efecto y la ruta del texto (o ``null`` si el PDF no tiene texto
  extraíble, p. ej. escaneado).
- ``circulares/texto/{año}/circu{n}.txt``: texto extraído del PDF con una cabecera que indica la
  fuente oficial y la nota "verificar vigencia antes de citar".

Es incremental: no vuelve a bajar el PDF de una circular cuyo texto ya existe (``--forzar``
lo obliga). Cada archivo se escribe de forma atómica; la metadata de un año solo se escribe
si su índice se pudo leer completo. Si algo falla, termina con código 1 y lo detalla.

Requiere ``pypdf`` (versión fijada en ``herramientas/requirements.txt``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from descargar_normas import USER_AGENT, _espera_reintento, escribir_atomico  # noqa: E402

URL_INDICE = "https://www.sii.cl/normativa_legislacion/circulares/{ano}/indcir{ano}.htm"
PRIMER_ANO = 2013
RAIZ_REPO = Path(__file__).resolve().parent.parent
NOTA_VIGENCIA = "Texto extraído del PDF oficial del SII. Verificar vigencia antes de citar."
MIN_CARACTERES_TEXTO = 200

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_RE_ENLACE = re.compile(
    r"<a\b(?P<attrs>[^>]*\bhref=[\"'](?P<href>[^\"']*circu(?P<num>\d+)\.(?P<ext>pdf|html?))[\"'][^>]*)>"
    r"(?P<texto>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_RE_FECHA = re.compile(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de|del)\s+(\d{4})", re.IGNORECASE)
_RE_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_RE_FUENTE = re.compile(r"Fuente:\s*(.*?)\s*</", re.IGNORECASE | re.DOTALL)
_RE_ETIQUETA = re.compile(r"<[^>]+>")


class ErrorCircular(Exception):
    """No se pudo bajar o interpretar un índice o una circular."""


def _plano(fragmento: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_RE_ETIQUETA.sub(" ", fragmento))).strip()


def _decodificar(datos: bytes) -> str:
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode("cp1252", errors="replace")


def parsear_indice(contenido: str, ano: int, url_indice: str) -> list[dict]:
    """Extrae las circulares de un índice anual del SII (más reciente primero)."""
    enlaces = list(_RE_ENLACE.finditer(contenido))
    circulares: list[dict] = []
    vistos: set[int] = set()
    for posicion, enlace in enumerate(enlaces):
        numero = int(enlace.group("num"))
        if numero in vistos:
            continue
        vistos.add(numero)
        titulo = _plano(enlace.group("texto"))
        fin = enlaces[posicion + 1].start() if posicion + 1 < len(enlaces) else len(contenido)
        tramo = contenido[enlace.end() : fin]
        materia = _plano(m.group(1)) if (m := _RE_P.search(tramo)) else ""
        fuente = _plano(m.group(1)) if (m := _RE_FUENTE.search(tramo)) else ""
        fecha = None
        if (f := _RE_FECHA.search(titulo)) and f.group(2).lower() in MESES:
            try:
                fecha = dt.date(int(f.group(3)), MESES[f.group(2).lower()], int(f.group(1))).isoformat()
            except ValueError:
                fecha = None
        circulares.append(
            {
                "numero": numero,
                "ano": ano,
                "fecha": fecha,
                "titulo": titulo,
                "materia": materia,
                "fuente_interna": fuente,
                "sin_efecto": "sin_efecto" in enlace.group("attrs").lower(),
                "url": urllib.parse.urljoin(url_indice, html.unescape(enlace.group("href"))),
                "texto": None,
            }
        )
    return circulares


def _get(url: str, intentos: int = 4, timeout: float = 90.0) -> bytes:
    peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
                return respuesta.read()
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise ErrorCircular(f"HTTP {exc.code} en {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ultimo = exc
        if intento < intentos:
            time.sleep(_espera_reintento(ultimo, intento))
    raise ErrorCircular(f"no se pudo descargar {url} tras {intentos} intentos: {ultimo}")


def extraer_texto(datos: bytes, url: str) -> str:
    """Texto de un PDF (con pypdf) o de un HTML antiguo."""
    if url.lower().endswith((".htm", ".html")):
        cuerpo = re.sub(r"<(script|style)\b.*?</\1>", " ", _decodificar(datos), flags=re.I | re.S)
        cuerpo = re.sub(r"<br\s*/?>|</p>|</div>|</h\d>|</tr>", "\n", cuerpo, flags=re.I)
        lineas = [_plano(linea) for linea in cuerpo.splitlines()]
        return "\n".join(linea for linea in lineas if linea)
    if not datos.startswith(b"%PDF"):
        raise ErrorCircular(f"la respuesta no es un PDF: {url}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ErrorCircular("falta pypdf: pip install -r herramientas/requirements.txt") from exc
    try:
        lector = PdfReader(io.BytesIO(datos))
        paginas = [(pagina.extract_text() or "").strip() for pagina in lector.pages]
    except Exception as exc:  # pypdf lanza varios tipos según el PDF
        raise ErrorCircular(f"no se pudo leer el PDF {url}: {exc}") from exc
    texto = "\n\n".join(p for p in paginas if p)
    return re.sub(r"[ \t]+\n", "\n", texto).strip()


def _cabecera(circular: dict) -> str:
    return (
        f"Circular SII N° {circular['numero']} de {circular['ano']}"
        f"{' (' + circular['fecha'] + ')' if circular['fecha'] else ''}\n"
        f"Materia: {circular['materia']}\n"
        f"Fuente oficial: {circular['url']}\n"
        f"Nota: {NOTA_VIGENCIA}"
        f"{chr(10) + 'Estado en el índice SII: dejada sin efecto' if circular['sin_efecto'] else ''}\n"
        f"{'=' * 72}\n\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raiz", type=Path, default=RAIZ_REPO)
    parser.add_argument("--anos", nargs="*", type=int, default=None, help="por defecto 2013..año actual")
    parser.add_argument("--forzar", action="store_true", help="vuelve a bajar textos ya existentes")
    parser.add_argument("--max-textos", type=int, default=None, help="tope de PDFs a bajar")
    parser.add_argument(
        "--solo-probar",
        action="store_true",
        help="lee todos los índices y prueba un PDF por año, sin escribir archivos",
    )
    parser.add_argument("--pausa", type=float, default=2.0, help="segundos entre descargas")
    args = parser.parse_args(argv)

    anos = args.anos or list(range(PRIMER_ANO, dt.date.today().year + 1))
    errores: list[str] = []
    total = 0
    bajados = 0
    for ano in anos:
        url_indice = URL_INDICE.format(ano=ano)
        try:
            circulares = parsear_indice(_decodificar(_get(url_indice)), ano, url_indice)
        except ErrorCircular as exc:
            errores.append(f"índice {ano}: {exc}")
            print(f"FALLÓ  índice {ano}: {exc}", file=sys.stderr, flush=True)
            continue
        if not circulares:
            errores.append(f"índice {ano}: no se encontró ninguna circular (¿cambió el formato?)")
            print(f"FALLÓ  índice {ano}: 0 circulares", file=sys.stderr, flush=True)
            continue
        sin_fecha = sum(1 for c in circulares if c["fecha"] is None)
        print(f"OK     índice {ano}: {len(circulares)} circulares ({sin_fecha} sin fecha legible)", flush=True)
        total += len(circulares)

        anterior: dict[int, dict] = {}
        ruta_meta = args.raiz / "circulares" / "metadata" / f"{ano}.json"
        if ruta_meta.is_file():
            anterior = {c["numero"]: c for c in json.loads(ruta_meta.read_text(encoding="utf-8"))}

        pendientes = circulares[:1] if args.solo_probar else circulares
        for circular in pendientes:
            relativo = f"circulares/texto/{ano}/circu{circular['numero']}.txt"
            ruta_texto = args.raiz / relativo
            previo = anterior.get(circular["numero"], {})
            if not args.solo_probar and not args.forzar and previo.get("url") == circular["url"]:
                if ruta_texto.is_file() or previo.get("sin_texto"):
                    circular["texto"] = relativo if ruta_texto.is_file() else None
                    circular["sin_texto"] = bool(previo.get("sin_texto"))
                    continue
            if args.max_textos is not None and bajados >= args.max_textos:
                circular["texto"] = relativo if ruta_texto.is_file() else None
                continue
            time.sleep(args.pausa)
            try:
                texto = extraer_texto(_get(circular["url"]), circular["url"])
            except ErrorCircular as exc:
                errores.append(f"circular {circular['numero']}/{ano}: {exc}")
                print(f"FALLÓ  circular {circular['numero']}/{ano}: {exc}", file=sys.stderr, flush=True)
                circular["texto"] = relativo if ruta_texto.is_file() else None
                continue
            bajados += 1
            if len(texto) < MIN_CARACTERES_TEXTO:
                circular["sin_texto"] = True
                print(f"AVISO  circular {circular['numero']}/{ano}: PDF sin texto extraíble ({len(texto)} car.)")
                continue
            circular["sin_texto"] = False
            circular["texto"] = relativo
            if args.solo_probar:
                print(f"OK     circular {circular['numero']}/{ano}: {len(texto)} caracteres; inicio: {texto[:80]!r}")
            else:
                escribir_atomico(ruta_texto, _cabecera(circular) + texto + "\n")
        if not args.solo_probar:
            escribir_atomico(ruta_meta, json.dumps(circulares, ensure_ascii=False, indent=1) + "\n")

    if not args.solo_probar and total and not any(e.startswith("índice") for e in errores):
        ruta_indice = args.raiz / "index.json"
        indice = json.loads(ruta_indice.read_text(encoding="utf-8-sig"))
        bloque = indice.setdefault("circulares", {})
        if bloque.get("total") != total or bloque.get("anos") != anos:
            bloque["total"] = total
            bloque["anos"] = anos
            escribir_atomico(ruta_indice, json.dumps(indice, ensure_ascii=False, indent=1) + "\n")

    print(f"\nResumen: {total} circulares en índices, {bajados} PDFs bajados, {len(errores)} errores.")
    if errores:
        print("ERROR: fallaron:", file=sys.stderr)
        for error in errores:
            print(f"  - {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
