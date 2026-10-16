#!/usr/bin/env python3
"""Descarga los textos consolidados vigentes de LeyChile y los convierte a Markdown.

Fuente: servicio web oficial de la Biblioteca del Congreso Nacional (BCN),
"XML completo de la versión actualizada de una norma":

    https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=<idNorma>

Esquema publicado por la BCN: http://www.leychile.cl/esquemas/EsquemaIntercambioNorma-v1-0.xsd
(espacio de nombres ``http://www.leychile.cl/esquemas``). Estructura usada aquí:

    <Norma normaId=".." fechaVersion="AAAA-MM-DD" derogado="..">
      <Identificador fechaPromulgacion=".." fechaPublicacion="..">
        <TiposNumeros><TipoNumero><Tipo/><Numero/></TipoNumero></TiposNumeros>
        <Organismos><Organismo/></Organismos>
      </Identificador>
      <Metadatos><TituloNorma/>...</Metadatos>
      <Encabezado><Texto/></Encabezado>
      <EstructurasFuncionales>
        <EstructuraFuncional tipoParte="Libro|Título|Capítulo|Párrafo|Artículo|..." derogado="..">
          <Texto/>
          <Metadatos><NombreParte presente="si|no"/><TituloParte presente="si|no"/></Metadatos>
          <EstructurasFuncionales>...</EstructurasFuncionales>   (recursivo)
        </EstructuraFuncional>
      </EstructurasFuncionales>
      <Promulgacion><Texto/></Promulgacion>
      <Anexos><Anexo><Metadatos><Titulo/></Metadatos><Texto/></Anexo></Anexos>
    </Norma>

Para cada código de ``index.json`` se genera ``codigos/<slug>.md`` con el front-matter que lee
``cc.knowledge.base_local`` (``idNorma``, ``titulo``, ``fuente`` con la URL oficial, fecha de
versión y la nota "verificar vigencia antes de citar"). Cada artículo queda como encabezado
``#### Artículo N`` para que el cargador de CC lo fragmente por artículo.

Todo o nada: primero se descargan y validan todas las normas pedidas; si alguna falla, el
script termina con error y no escribe ningún archivo. Las escrituras son atómicas
(archivo temporal + ``os.replace``).

Solo usa la biblioteca estándar de Python (3.10+).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

NS = "http://www.leychile.cl/esquemas"
NS_AEM = "http://valida.aem.gob.cl"
URL_XML = "https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma={id_norma}"
URL_OFICIAL = "https://www.bcn.cl/leychile/navegar?idNorma={id_norma}"
NOTA_VIGENCIA = (
    "Texto consolidado descargado del servicio oficial de LeyChile (BCN). "
    "Verificar vigencia antes de citar."
)
# CloudFront de LeyChile rechaza agentes de usuario genéricos de bibliotecas HTTP.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 kickoff-base-legal/1.0"
)
RAIZ_REPO = Path(__file__).resolve().parent.parent
ADVERTENCIA_FECHA_FUTURA = (
    "LeyChile informa fechaVersion {fecha}, posterior a la fecha de descarga (no es una fecha real "
    "de publicación). El texto puede incluir modificaciones con vigencia diferida o aún no vigentes: "
    "verificar en la fuente oficial qué disposiciones rigen antes de citar."
)

_TIPOS_ARTICULO = {"artículo", "articulo"}
_TIPOS_TRANSITORIA = {"disposición transitoria", "disposicion transitoria"}
_RE_ESPACIOS = re.compile(r"\s+")
_RE_DEL_ART = re.compile(r"\s*\(\s*DEL\s+ART\.?\s*\d+\s*\)\s*$", re.IGNORECASE)
# Prefijo del artículo dentro del cuerpo: «Artículo 1°.-», «Art. 2.», «ARTICULO 10 bis.-».
_RE_PREFIJO_ART = re.compile(
    r"""^\s*"?\s*(?:art[íi]culo|art\.)\s*[^\s.:\-–—]+(?:\s+(?:bis|ter|qu[áa]ter|[a-z]))?
        (?:\s+transitori[oa])?\s*[°º]?\s*(?:\.\s*-|\.|-|–|—|:)\s*""",
    re.IGNORECASE | re.VERBOSE,
)
# Líneas que empiezan un elemento de lista y no deben pegarse a la anterior al refluir.
_RE_INICIO_ITEM = re.compile(
    r"^(?:[a-zñ]\)|\d+[°º]?[.)\-]|[ivxlc]+[.)]|-|•|\(\d+\)|\([a-z]\))\s", re.IGNORECASE
)
# Anotaciones de margen del Diario Oficial (referencias a leyes modificatorias).
_RE_MARGEN = re.compile(
    r"(?:\bLEY\b|\bL\.\s*\d|D\.O\.|D\.L\.|D\.F\.L\.|D\.S\.|\bArt\.?\s*\d|\bN[º°]\s*\d"
    r"|\d{2}\.\d{2}\.\d{4})"
)
_RE_CORTE_MARGEN = re.compile(r"^(?P<texto>.*?\S)\s{6,}(?P<margen>\S.*)$")


class ErrorDescarga(Exception):
    """Una norma no se pudo descargar, interpretar o validar."""


@dataclass
class Norma:
    """Resultado de convertir el XML de una norma."""

    id_norma: int
    titulo_oficial: str
    tipo_numero: str
    fecha_version: str
    fecha_publicacion: str
    derogado: str
    markdown_cuerpo: str
    articulos: int
    avisos: list[str] = field(default_factory=list)


def _t(nombre: str) -> str:
    return f"{{{NS}}}{nombre}"


def _limpiar_control(texto: str) -> str:
    texto = texto.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in texto if c in "\n\t" or unicodedata.category(c) != "Cc")


def _una_linea(texto: str | None) -> str:
    return _RE_ESPACIOS.sub(" ", _limpiar_control(texto or "")).strip()


def _texto_de(elemento: ET.Element | None) -> str:
    """Contenido de un ``<Texto>``, omitiendo binarios embebidos (``aem:ArchivoBinario``)."""
    if elemento is None:
        return ""
    partes = [elemento.text or ""]
    for hijo in elemento:
        if hijo.tag.startswith(f"{{{NS_AEM}}}"):
            partes.append("\n\n[imagen o archivo adjunto omitido; ver texto oficial]\n\n")
        else:
            partes.append("".join(hijo.itertext()))
        partes.append(hijo.tail or "")
    return _limpiar_control("".join(partes))


def _quitar_margenes(linea: str) -> str | None:
    """Quita referencias de margen; ``None`` si la línea entera es anotación de margen."""
    sangria = len(linea) - len(linea.lstrip(" "))
    limpia = linea.strip()
    if not limpia:
        return ""
    if sangria >= 20 and len(limpia) < 60 and _RE_MARGEN.search(limpia):
        return None
    corte = _RE_CORTE_MARGEN.match(linea)
    if corte and _RE_MARGEN.search(corte.group("margen")) and len(corte.group("margen")) < 60:
        return corte.group("texto").strip()
    return limpia


def limpiar_cuerpo(texto: str) -> str:
    """Normaliza el texto de LeyChile: quita márgenes y refluye las líneas cortadas."""
    parrafos: list[str] = []
    actual: list[str] = []
    for cruda in _limpiar_control(texto).split("\n"):
        linea = _quitar_margenes(cruda.expandtabs(4).rstrip())
        if linea is None:
            continue
        if not linea:
            if actual:
                parrafos.append(" ".join(actual))
                actual = []
            continue
        if actual and _RE_INICIO_ITEM.match(linea):
            parrafos.append(" ".join(actual))
            actual = []
        actual.append(linea)
    if actual:
        parrafos.append(" ".join(actual))
    return "\n\n".join(_RE_ESPACIOS.sub(" ", p).strip() for p in parrafos if p.strip())


def _meta_parte(estructura: ET.Element, campo: str) -> str:
    meta = estructura.find(_t("Metadatos"))
    if meta is None:
        return ""
    hijo = meta.find(_t(campo))
    if hijo is None or hijo.get("presente") == "no":
        return ""
    return _RE_DEL_ART.sub("", _una_linea(hijo.text)).strip()


def _sin_prefijo_articulo(cuerpo: str) -> str:
    coincidencia = _RE_PREFIJO_ART.match(cuerpo)
    if coincidencia is None:
        return cuerpo
    resto = cuerpo[coincidencia.end() :].lstrip()
    return resto or cuerpo


def _etiqueta_articulo(nombre: str, transitorio: bool) -> str:
    """``1`` → ``Artículo 1``; ``1 TRANSITORIO`` → ``Artículo 1 transitorio``."""
    nombre = re.sub(r"^(?:art[íi]culo|art\.)\s*", "", nombre, flags=re.IGNORECASE).strip()
    nombre = nombre.rstrip(" .:-–—°º")
    nombre = re.sub(r"\btransitori([oa])\b", r"transitori\1", nombre, flags=re.IGNORECASE)
    if transitorio and "transitori" not in nombre.lower():
        nombre = f"{nombre} transitorio"
    return f"Artículo {nombre}"


def _es_derogado(estructura: ET.Element) -> bool:
    return estructura.get("derogado", "").strip().lower() == "derogado"


def _es_tipo_articulo(estructura: ET.Element) -> bool:
    return (estructura.get("tipoParte") or "").strip().lower() in _TIPOS_ARTICULO


def _contiene_articulos(estructura: ET.Element) -> bool:
    return any(
        _es_tipo_articulo(hija) for hija in estructura.iter(_t("EstructuraFuncional")) if hija is not estructura
    )


class _Conversor:
    """Recorre EstructurasFuncionales y emite Markdown (un encabezado ``####`` por artículo)."""

    def __init__(self) -> None:
        self.lineas: list[str] = []
        self.articulos = 0
        self.avisos: list[str] = []
        self.encabezados: list[str] = []

    def _bloque(self, texto: str) -> None:
        if texto.strip():
            self.lineas.extend([texto.strip(), ""])

    def estructuras(self, contenedor: ET.Element | None, profundidad: int) -> None:
        if contenedor is None:
            return
        for estructura in contenedor.findall(_t("EstructuraFuncional")):
            self.estructura(estructura, profundidad)

    def estructura(self, estructura: ET.Element, profundidad: int) -> None:
        tipo = (estructura.get("tipoParte") or "").strip()
        tipo_min = tipo.lower()
        nombre = _meta_parte(estructura, "NombreParte")
        titulo = _meta_parte(estructura, "TituloParte")
        cuerpo = limpiar_cuerpo(_texto_de(estructura.find(_t("Texto"))))
        transitorio = estructura.get("transitorio", "").strip().lower() == "transitorio"
        derogado = " (derogado)" if _es_derogado(estructura) else ""

        if tipo_min in _TIPOS_ARTICULO or (tipo_min in _TIPOS_TRANSITORIA and nombre):
            self.articulos += 1
            if nombre and tipo_min in _TIPOS_ARTICULO:
                encabezado = _etiqueta_articulo(nombre, transitorio)
                self.encabezados.append(encabezado.lower())
                self.lineas.extend([f"#### {encabezado}{derogado}", ""])
                self._bloque(_sin_prefijo_articulo(cuerpo))
            elif nombre:
                # Disposición transitoria con nombre propio («Primera», «Trigésima»):
                # se respeta la denominación oficial, sin convertirla en «Artículo».
                self.lineas.extend([f"#### Disposición transitoria {nombre}{derogado}", ""])
                self._bloque(cuerpo)
            else:
                self.avisos.append("artículo sin NombreParte: se conserva el texto tal cual")
                self._bloque(cuerpo)
        elif tipo_min == "doble articulado":
            self._bloque(cuerpo)
        else:
            # Agrupador (Libro, Título, Capítulo, Párrafo...): su <Texto> suele ser solo el
            # rótulo («TITULO PRELIMINAR»). Si es corto se usa como encabezado; si es largo se
            # conserva completo bajo un encabezado construido con TituloParte/tipoParte.
            nivel = "#" * min(profundidad + 1, 3)
            plano = _una_linea(cuerpo)
            if plano and len(plano) <= 200:
                encabezado, resto = plano, ""
            else:
                encabezado, resto = (titulo or tipo or "Sección"), cuerpo
            self.lineas.extend([f"{nivel} {encabezado}{derogado}", ""])
            self._bloque(resto)
        self.estructuras(estructura.find(_t("EstructurasFuncionales")), profundidad + 1)


def _articulos_exteriores(cuerpo: ET.Element) -> tuple[list[ET.Element], list[ET.Element]]:
    """Artículos que no están dentro de otro artículo, y cuáles de ellos son envoltorios."""
    exteriores: list[ET.Element] = []

    def recorrer(nodo: ET.Element) -> None:
        for hija in nodo.findall(f"{_t('EstructuraFuncional')}"):
            if _es_tipo_articulo(hija):
                exteriores.append(hija)
            else:
                contenedor = hija.find(_t("EstructurasFuncionales"))
                if contenedor is not None:
                    recorrer(contenedor)

    recorrer(cuerpo)
    return exteriores, [a for a in exteriores if _contiene_articulos(a)]


def _convertir_envoltorio(
    conversor: _Conversor, cuerpo: ET.Element, etiqueta_norma: str, exteriores: list[ET.Element],
    envoltorios: list[ET.Element],
) -> None:
    """Norma envoltorio (p. ej. el DFL 1 de 2000 que fija el texto del Código Civil).

    Un artículo del decreto contiene, anidado, el articulado completo del código. Para que
    ``kb://<slug>#art-1`` sea el artículo 1 del código y ningún fragmento se rotule como
    artículo del código sin serlo:

    - los artículos propios del decreto van primero, con encabezado «<norma>, artículo N» (que
      el cargador de CC no toma como artículo: quedan en el fragmento ``inicio``);
    - el articulado del envoltorio principal (el que contiene más artículos) va después,
      un ``#### Artículo N`` por artículo;
    - el contenido de los demás envoltorios (otras leyes refundidas en el mismo decreto) se
      omite y se deja constancia; está en la fuente oficial.
    """
    principal = max(envoltorios, key=lambda e: sum(1 for x in e.iter(_t("EstructuraFuncional")) if _es_tipo_articulo(x)))
    for articulo in exteriores:
        nombre = _meta_parte(articulo, "NombreParte") or "s/n"
        transitorio = articulo.get("transitorio", "").strip().lower() == "transitorio"
        etiqueta = _etiqueta_articulo(nombre, transitorio).replace("Artículo", "artículo", 1)
        derogado = " (derogado)" if _es_derogado(articulo) else ""
        conversor.lineas.extend([f"### {etiqueta_norma}, {etiqueta}{derogado}", ""])
        conversor._bloque(_sin_prefijo_articulo(limpiar_cuerpo(_texto_de(articulo.find(_t("Texto"))))))
        if articulo is not principal and articulo in envoltorios:
            omitidos = sum(1 for x in articulo.iter(_t("EstructuraFuncional")) if _es_tipo_articulo(x)) - 1
            conversor._bloque(
                f"_[Se omiten aquí los {omitidos} artículos del texto que fija este artículo: no forman "
                "parte de este archivo. Consultar la fuente oficial.]_"
            )
            conversor.avisos.append(f"envoltorio secundario omitido: {etiqueta} ({omitidos} artículos)")
    conversor.articulos += len(exteriores)
    conversor.estructuras(principal.find(_t("EstructurasFuncionales")), 2)


def parsear_xml(datos: bytes, id_esperado: int | None = None) -> Norma:
    """Convierte el XML ``obtxml?opt=7`` de LeyChile en una :class:`Norma`.

    Lanza :class:`ErrorDescarga` si el XML no es una norma de LeyChile válida o no corresponde
    al ``idNorma`` esperado.
    """
    if not datos or not datos.lstrip().startswith(b"<"):
        raise ErrorDescarga("la respuesta no es XML (vacía o HTML/texto)")
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as exc:
        raise ErrorDescarga(f"XML mal formado: {exc}") from exc
    if raiz.tag != _t("Norma"):
        raise ErrorDescarga(f"raíz inesperada {raiz.tag!r}; se esperaba {{{NS}}}Norma")
    crudo_id = (raiz.get("normaId") or "").strip()
    if not crudo_id.isdigit():
        raise ErrorDescarga(f"atributo normaId ausente o inválido: {crudo_id!r}")
    id_norma = int(crudo_id)
    if id_esperado is not None and id_norma != id_esperado:
        raise ErrorDescarga(f"se pidió idNorma {id_esperado} y LeyChile devolvió {id_norma}")

    fecha_version = (raiz.get("fechaVersion") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha_version):
        raise ErrorDescarga(f"fechaVersion ausente o inválida: {fecha_version!r}")

    identificador = raiz.find(_t("Identificador"))
    fecha_publicacion = ""
    tipos: list[str] = []
    if identificador is not None:
        fecha_publicacion = (identificador.get("fechaPublicacion") or "").strip()
        for tipo_numero in identificador.iter(_t("TipoNumero")):
            tipo = _una_linea(tipo_numero.findtext(_t("Tipo")))
            numero = _una_linea(tipo_numero.findtext(_t("Numero")))
            if tipo or numero:
                tipos.append(f"{tipo} {numero}".strip())
    titulo_oficial = _una_linea(raiz.findtext(f"{_t('Metadatos')}/{_t('TituloNorma')}"))
    if not titulo_oficial:
        raise ErrorDescarga("falta Metadatos/TituloNorma")

    conversor = _Conversor()
    encabezado = limpiar_cuerpo(_texto_de(raiz.find(f"{_t('Encabezado')}/{_t('Texto')}")))
    conversor._bloque(encabezado)
    cuerpo = raiz.find(_t("EstructurasFuncionales"))
    if cuerpo is None or cuerpo.find(_t("EstructuraFuncional")) is None:
        raise ErrorDescarga("el XML no trae EstructurasFuncionales con contenido")
    exteriores, envoltorios = _articulos_exteriores(cuerpo)
    hay_envoltorio = bool(envoltorios)
    if hay_envoltorio:
        _convertir_envoltorio(conversor, cuerpo, tipos[0] if tipos else "Norma", exteriores, envoltorios)
    else:
        conversor.estructuras(cuerpo, 1)

    promulgacion = limpiar_cuerpo(_texto_de(raiz.find(f"{_t('Promulgacion')}/{_t('Texto')}")))
    if promulgacion:
        conversor.lineas.extend(["## Promulgación", ""])
        conversor._bloque(promulgacion)
    anexos = raiz.find(_t("Anexos"))
    if anexos is not None:
        for indice, anexo in enumerate(anexos.findall(_t("Anexo")), start=1):
            titulo_anexo = _una_linea(anexo.findtext(f"{_t('Metadatos')}/{_t('Titulo')}"))
            conversor.lineas.extend([f"## Anexo: {titulo_anexo or indice}", ""])
            conversor._bloque(limpiar_cuerpo(_texto_de(anexo.find(_t("Texto")))))

    if conversor.articulos == 0:
        raise ErrorDescarga("no se encontró ningún artículo (tipoParte=Artículo) en el XML")
    if hay_envoltorio:
        conversor.avisos.append(
            f"norma envoltorio: los artículos propios de «{tipos[0] if tipos else 'la norma'}» van al inicio"
        )
    conteo = Counter(conversor.encabezados)
    repetidos = sorted(e for e, n in conteo.items() if n > 1)
    if repetidos:
        muestra = ", ".join(repetidos[:8]) + ("…" if len(repetidos) > 8 else "")
        conversor.avisos.append(f"{len(repetidos)} rótulos de artículo repetidos (anclas -2, -3 en CC): {muestra}")

    return Norma(
        id_norma=id_norma,
        titulo_oficial=titulo_oficial,
        tipo_numero="; ".join(tipos),
        fecha_version=fecha_version,
        fecha_publicacion=fecha_publicacion,
        derogado=(raiz.get("derogado") or "").strip(),
        markdown_cuerpo="\n".join(conversor.lineas).rstrip() + "\n",
        articulos=conversor.articulos,
        avisos=conversor.avisos,
    )


def es_fecha_futura(fecha: str, referencia: str) -> bool:
    """``True`` si ``fecha`` (AAAA-MM-DD) es posterior a ``referencia`` (p. ej. 2222-02-02)."""
    return fecha > referencia


def _yaml_str(valor: str) -> str:
    return json.dumps(valor, ensure_ascii=False)


def a_markdown(norma: Norma, nombre: str, fecha_descarga: str) -> str:
    """Archivo final: front-matter ``clave: valor`` (lo lee ``separar_front_matter``) + cuerpo."""
    front = [
        "---",
        f"idNorma: {norma.id_norma}",
        f"titulo: {_yaml_str(nombre)}",
        f"titulo_oficial: {_yaml_str(norma.titulo_oficial)}",
        f"tipo_numero: {_yaml_str(norma.tipo_numero)}",
        f"fuente: {URL_OFICIAL.format(id_norma=norma.id_norma)}",
        f"fuente_xml: {URL_XML.format(id_norma=norma.id_norma)}",
        f"fecha_version: {norma.fecha_version}",
        *([f"advertencia_version: {_yaml_str(ADVERTENCIA_FECHA_FUTURA.format(fecha=norma.fecha_version))}"]
          if es_fecha_futura(norma.fecha_version, fecha_descarga) else []),
        f"fecha_publicacion: {norma.fecha_publicacion}",
        f"fecha_descarga: {fecha_descarga}",
        f"estado: {_yaml_str(norma.derogado)}",
        f"articulos: {norma.articulos}",
        f"nota: {_yaml_str(NOTA_VIGENCIA)}",
        "---",
        "",
        f"# {nombre}",
        "",
        f"> **Verificar vigencia antes de citar.** Texto consolidado de LeyChile (BCN), versión "
        f"{norma.fecha_version}, idNorma {norma.id_norma}. Fuente oficial: "
        f"{URL_OFICIAL.format(id_norma=norma.id_norma)}",
        "",
        *([f"> **Advertencia:** {ADVERTENCIA_FECHA_FUTURA.format(fecha=norma.fecha_version)}", ""]
          if es_fecha_futura(norma.fecha_version, fecha_descarga) else []),
    ]
    return "\n".join(front) + "\n" + norma.markdown_cuerpo


def _espera_reintento(exc: Exception | None, intento: int) -> float:
    """Respeta ``Retry-After`` si viene; si no, espera exponencial (15, 30, 60, 120, 180 s)."""
    if isinstance(exc, urllib.error.HTTPError):
        crudo = (exc.headers or {}).get("Retry-After", "")
        if str(crudo).strip().isdigit():
            return min(900.0, float(crudo))
    return float(min(180, 15 * 2 ** (intento - 1)))


def descargar(id_norma: int, intentos: int = 8, timeout: float = 120.0) -> bytes:
    """GET al servicio oficial con reintentos ante errores de red, 5xx o 429 (límite de tasa)."""
    url = URL_XML.format(id_norma=id_norma)
    peticion = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml, text/xml, */*"}
    )
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
                datos = respuesta.read()
                if respuesta.status != 200:
                    raise ErrorDescarga(f"HTTP {respuesta.status} en {url}")
                return datos
        except urllib.error.HTTPError as exc:
            ultimo = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise ErrorDescarga(f"HTTP {exc.code} en {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            ultimo = exc
        if intento < intentos:
            espera = _espera_reintento(ultimo, intento)
            print(f"       reintento {intento}/{intentos - 1} de idNorma {id_norma} en {espera:.0f} s: {ultimo}",
                  flush=True)
            time.sleep(espera)
    raise ErrorDescarga(f"no se pudo descargar {url} tras {intentos} intentos: {ultimo}")


def escribir_atomico(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(contenido)
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise


def _cuerpo_sin_fecha(texto: str) -> str:
    """Contenido comparable: ignora la línea ``fecha_descarga`` para no generar commits vacíos."""
    return re.sub(r"^fecha_descarga: .*$", "", texto, count=1, flags=re.MULTILINE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raiz", type=Path, default=RAIZ_REPO, help="raíz de kickoff-base-legal")
    parser.add_argument("--solo", nargs="*", default=None, help="slugs a descargar (por defecto todos)")
    parser.add_argument(
        "--solo-probar",
        action="store_true",
        help="descarga y valida sin escribir archivos (para probar en CI)",
    )
    parser.add_argument("--pausa", type=float, default=15.0, help="segundos entre descargas (LeyChile limita la tasa)")
    parser.add_argument("--min-articulos", type=int, default=10, help="mínimo de artículos por norma")
    args = parser.parse_args(argv)

    indice_ruta = args.raiz / "index.json"
    indice = json.loads(indice_ruta.read_text(encoding="utf-8-sig"))
    codigos = indice.get("codigos", [])
    if args.solo:
        pedidos = set(args.solo)
        desconocidos = pedidos - {c["slug"] for c in codigos}
        if desconocidos:
            print(f"ERROR: slugs desconocidos: {', '.join(sorted(desconocidos))}", file=sys.stderr)
            return 2
        codigos = [c for c in codigos if c["slug"] in pedidos]

    fecha_descarga = dt.date.today().isoformat()
    resultados: list[tuple[dict, Norma, str]] = []
    errores: list[str] = []
    for posicion, codigo in enumerate(codigos):
        if posicion:
            time.sleep(args.pausa)
        slug, id_norma = codigo["slug"], int(codigo["idNorma"])
        try:
            norma = parsear_xml(descargar(id_norma), id_esperado=id_norma)
            if norma.articulos < args.min_articulos:
                raise ErrorDescarga(
                    f"solo {norma.articulos} artículos (< {args.min_articulos}); posible respuesta incompleta"
                )
        except ErrorDescarga as exc:
            errores.append(f"{slug} (idNorma {id_norma}): {exc}")
            print(f"FALLÓ  {slug:34} idNorma={id_norma}: {exc}", file=sys.stderr, flush=True)
            continue
        markdown = a_markdown(norma, codigo["nombre"], fecha_descarga)
        resultados.append((codigo, norma, markdown))
        print(
            f"OK     {slug:34} idNorma={id_norma} versión={norma.fecha_version} "
            f"artículos={norma.articulos} bytes={len(markdown.encode('utf-8'))} "
            f"título={norma.titulo_oficial[:70]!r}",
            flush=True,
        )
        for aviso in sorted(set(norma.avisos)):
            print(f"       aviso: {aviso}", flush=True)

    print(f"\nResumen: {len(resultados)} OK, {len(errores)} con error, de {len(codigos)} pedidas.")
    if errores:
        print("ERROR: no se escribió ningún archivo porque fallaron estas normas:", file=sys.stderr)
        for error in errores:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if args.solo_probar:
        print("Modo --solo-probar: no se escribió ningún archivo.")
        return 0

    cambiados = 0
    for codigo, norma, markdown in resultados:
        ruta = args.raiz / codigo["archivo"]
        anterior = ruta.read_text(encoding="utf-8") if ruta.is_file() else None
        if anterior is not None and _cuerpo_sin_fecha(anterior) == _cuerpo_sin_fecha(markdown):
            continue
        escribir_atomico(ruta, markdown)
        codigo["articulos"] = norma.articulos
        codigo["bytes"] = len(markdown.encode("utf-8"))
        codigo["fechaVersion"] = norma.fecha_version
        cambiados += 1
    if cambiados:
        indice["version"] = fecha_descarga
        escribir_atomico(indice_ruta, json.dumps(indice, ensure_ascii=False, indent=1) + "\n")
    print(f"Archivos actualizados: {cambiados}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
