"""Pruebas del parser del índice anual de circulares del SII.

El HTML de abajo es una MUESTRA SINTÉTICA reducida, con el mismo marcado que el índice
``indcir{año}.htm`` del SII (h5 > a[href=circuN.pdf], p con la materia, span con la fuente).
Números, fechas y materias son ficticios.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "herramientas"))

import descargar_circulares as dc  # noqa: E402

URL = "https://www.sii.cl/normativa_legislacion/circulares/2030/indcir2030.htm"
MUESTRA = """
<h2 class="title">Circulares 2030</h2>
<h5 style="margin-bottom:0px;"><a href="circu2.pdf" target="_blank">Circular N° 2 del 11 de Septiembre del 2030</a></h5>
<p style="margin-top:0px;margin-bottom:0px;">Materia sint&eacute;tica de prueba dos.</p>
<span style="font-size:12px;margin-bottom:10px;"><i>Fuente: Subdirección de Prueba</i></span>
<h5 style="margin-bottom:0px;"><a href="circu1.pdf" target="_blank" class="sin_efecto">Circular N° 1 del 05 de Enero del 2030</a></h5>
<p style="margin-top:0px;margin-bottom:0px;">Materia uno.</p>
<span style="font-size:12px;margin-bottom:10px;"><i>Fuente: Subdirección Normativa</i></span>
"""


def test_parsear_indice() -> None:
    circulares = dc.parsear_indice(MUESTRA, 2030, URL)
    assert [c["numero"] for c in circulares] == [2, 1]
    dos, uno = circulares
    assert dos["fecha"] == "2030-09-11"
    assert dos["materia"] == "Materia sintética de prueba dos."
    assert dos["fuente_interna"] == "Subdirección de Prueba"
    assert dos["url"] == "https://www.sii.cl/normativa_legislacion/circulares/2030/circu2.pdf"
    assert dos["sin_efecto"] is False
    assert uno["sin_efecto"] is True
    assert uno["fecha"] == "2030-01-05"


def test_html_antiguo_a_texto() -> None:
    texto = dc.extraer_texto(b"<html><p>Uno &amp; dos</p><br>tres</html>", "https://x/circu1.htm")
    assert texto.splitlines() == ["Uno & dos", "tres"]


def test_respuesta_no_pdf() -> None:
    try:
        dc.extraer_texto(b"<html>bloqueado</html>", "https://x/circu1.pdf")
    except dc.ErrorCircular as exc:
        assert "no es un PDF" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("debió fallar")
