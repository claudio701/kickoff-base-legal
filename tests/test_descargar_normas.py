"""Pruebas del conversor LeyChile → Markdown.

Usan ``tests/muestras/norma_sintetica.xml``: una MUESTRA SINTÉTICA construida a mano con la
estructura documentada de ``obtxml?opt=7``. No es una norma real (idNorma 999999 ficticio).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "herramientas"))

import descargar_normas as dn  # noqa: E402

MUESTRA = (RAIZ / "tests" / "muestras" / "norma_sintetica.xml").read_bytes()
ID_MUESTRA = 999999


@pytest.fixture()
def norma() -> dn.Norma:
    return dn.parsear_xml(MUESTRA, id_esperado=ID_MUESTRA)


def test_metadatos(norma: dn.Norma) -> None:
    assert norma.id_norma == ID_MUESTRA
    assert norma.titulo_oficial == "LEY SINTETICA DE PRUEBA"
    assert norma.tipo_numero == "Ley 99999"
    assert norma.fecha_version == "2026-01-15"
    assert norma.fecha_publicacion == "2020-01-10"
    assert norma.articulos == 4


def test_articulos_como_encabezados(norma: dn.Norma) -> None:
    md = norma.markdown_cuerpo
    assert "## TITULO I Disposiciones generales" in md
    assert "#### Artículo 1\n\nEsta ley sintética regula el plazo de prueba de diez días hábiles." in md
    assert "#### Artículo 2 bis\n\nLa prescripción de prueba" in md  # sin «(DEL ART. 1)»
    assert "#### Artículo 3 (derogado)" in md
    assert "#### Artículo primero transitorio\n\nEsta ley sintética rige" in md
    # El prefijo «Artículo 1°.-» del cuerpo no se duplica.
    assert "Artículo 1°.-" not in md


def test_margenes_listas_y_binarios(norma: dn.Norma) -> None:
    md = norma.markdown_cuerpo
    assert "LEY 99998" not in md and "D.O. 01.02.2021" not in md
    assert "\n\na) los casos de prueba, y\n\nb) las muestras." in md
    assert "QUJD" not in md
    assert "[imagen o archivo adjunto omitido; ver texto oficial]" in md
    assert "Texto posterior a la figura." in md
    assert "## Promulgación" in md


def test_front_matter() -> None:
    md = dn.a_markdown(dn.parsear_xml(MUESTRA), "Ley de prueba", "2026-10-15")
    assert md.startswith("---\nidNorma: 999999\n")
    assert "fuente: https://www.bcn.cl/leychile/navegar?idNorma=999999\n" in md
    assert "fuente_xml: https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=999999\n" in md
    assert "fecha_version: 2026-01-15\n" in md
    assert "verificar vigencia antes de citar" in md.lower()


def test_advertencia_fecha_version_futura() -> None:
    sintetica = MUESTRA.replace(b'fechaVersion="2026-01-15" SchemaVersion', b'fechaVersion="2222-02-02" SchemaVersion')
    md = dn.a_markdown(dn.parsear_xml(sintetica), "Ley de prueba", "2026-10-15")
    assert "advertencia_version: " in md
    assert "> **Advertencia:** LeyChile informa fechaVersion 2222-02-02" in md
    normal = dn.a_markdown(dn.parsear_xml(MUESTRA), "Ley de prueba", "2026-10-15")
    assert "advertencia_version" not in normal and "Advertencia:" not in normal


@pytest.mark.parametrize(
    ("datos", "mensaje"),
    [
        (b"", "no es XML"),
        (b"<html><body>Request blocked</body></html>", "raíz inesperada"),
        (b"<Norma", "mal formado"),
    ],
)
def test_respuestas_invalidas(datos: bytes, mensaje: str) -> None:
    with pytest.raises(dn.ErrorDescarga, match=mensaje):
        dn.parsear_xml(datos)


def test_id_distinto() -> None:
    with pytest.raises(dn.ErrorDescarga, match="devolvió 999999"):
        dn.parsear_xml(MUESTRA, id_esperado=1)


def _base_temporal(tmp_path: Path) -> Path:
    indice = {
        "version": "2000-01-01",
        "codigos": [
            {"slug": "ley-a", "nombre": "Ley A", "idNorma": ID_MUESTRA, "archivo": "codigos/ley-a.md"},
            {"slug": "ley-b", "nombre": "Ley B", "idNorma": 123, "archivo": "codigos/ley-b.md"},
        ],
        "documentos": [],
    }
    (tmp_path / "index.json").write_text(json.dumps(indice), encoding="utf-8")
    return tmp_path


def test_todo_o_nada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raiz = _base_temporal(tmp_path)
    monkeypatch.setattr(dn, "descargar", lambda id_norma, **_: MUESTRA)  # ley-b: id distinto
    codigo = dn.main(["--raiz", str(raiz), "--pausa", "0", "--min-articulos", "1"])
    assert codigo == 1
    assert not (raiz / "codigos").exists()
    assert json.loads((raiz / "index.json").read_text())["version"] == "2000-01-01"


def test_solo_probar_no_escribe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raiz = _base_temporal(tmp_path)
    monkeypatch.setattr(dn, "descargar", lambda id_norma, **_: MUESTRA)
    argumentos = ["--raiz", str(raiz), "--pausa", "0", "--min-articulos", "1", "--solo", "ley-a"]
    assert dn.main([*argumentos, "--solo-probar"]) == 0
    assert not (raiz / "codigos").exists()
    assert dn.main(argumentos) == 0
    assert (raiz / "codigos" / "ley-a.md").is_file()
    indice = json.loads((raiz / "index.json").read_text())
    assert indice["codigos"][0]["articulos"] == 4
    assert indice["codigos"][0]["fechaVersion"] == "2026-01-15"


def test_lo_lee_el_cargador_de_cc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La salida la fragmenta por artículo ``cc.knowledge.base_local`` (si CC está instalado)."""
    base_local = pytest.importorskip("cc.knowledge.base_local")
    raiz = _base_temporal(tmp_path)
    monkeypatch.setattr(dn, "descargar", lambda id_norma, **_: MUESTRA)
    assert dn.main(["--raiz", str(raiz), "--pausa", "0", "--min-articulos", "1", "--solo", "ley-a"]) == 0

    base = base_local.BaseLegalLocal(raiz)
    assert base.cargar() > 0
    assert base.faltantes == ("codigos/ley-b.md",)
    refs = base.refs()
    for ancla in ("art-1", "art-2-bis", "art-3", "art-primero-transitorio"):
        assert f"kb://ley-a#{ancla}" in refs, sorted(refs)
    fragmento = base.obtener("kb://ley-a#art-1")
    assert fragmento is not None
    assert fragmento.tipo == "norma"
    assert fragmento.titulo == "Ley A, artículo 1"
    assert fragmento.fuente_oficial == "https://www.bcn.cl/leychile/navegar?idNorma=999999"
    assert "diez días hábiles" in fragmento.texto
    # Solo el artículo 1 (el 2 bis lo menciona en su texto, pero no es su fragmento).
    assert base.buscar("plazo diez días hábiles", limite=1)[0].ref == "kb://ley-a#art-1"


# MUESTRA SINTÉTICA de norma «envoltorio»: un decreto cuyo artículo 2 contiene el articulado
# completo de un código (como el DFL que fija el texto del Código Civil). Contenido ficticio.
ENVOLTORIO = """<?xml version="1.0" encoding="UTF-8"?>
<Norma xmlns="http://www.leychile.cl/esquemas" normaId="888888" fechaVersion="2025-05-05" derogado="no derogado">
  <Identificador fechaPublicacion="2000-01-01"><TiposNumeros><TipoNumero><Tipo>Decreto con Fuerza de Ley</Tipo>
  <Numero>9</Numero></TipoNumero></TiposNumeros></Identificador>
  <Metadatos><TituloNorma>FIJA TEXTO SINTETICO</TituloNorma></Metadatos>
  <EstructurasFuncionales>
    <EstructuraFuncional tipoParte="Artículo"><Texto>Artículo 1.- Deja sin efecto un decreto ficticio.</Texto>
      <Metadatos><NombreParte presente="si">1</NombreParte></Metadatos></EstructuraFuncional>
    <EstructuraFuncional tipoParte="Artículo"><Texto>Artículo 2.- Fíjase el siguiente texto sintético.</Texto>
      <Metadatos><NombreParte presente="si">2</NombreParte></Metadatos>
      <EstructurasFuncionales>
        <EstructuraFuncional tipoParte="Título"><Texto>TITULO PRELIMINAR</Texto>
          <EstructurasFuncionales>
            <EstructuraFuncional tipoParte="Artículo"><Texto>Art. 1. Primer artículo del código sintético.</Texto>
              <Metadatos><NombreParte presente="si">1</NombreParte></Metadatos></EstructuraFuncional>
            <EstructuraFuncional tipoParte="Artículo"><Texto>Art. 2. Segundo artículo del código sintético.</Texto>
              <Metadatos><NombreParte presente="si">2</NombreParte></Metadatos></EstructuraFuncional>
          </EstructurasFuncionales>
        </EstructuraFuncional>
      </EstructurasFuncionales>
    </EstructuraFuncional>
    <EstructuraFuncional tipoParte="Artículo"><Texto>Artículo 3.- Fíjase el texto de otra ley ficticia.</Texto>
      <Metadatos><NombreParte presente="si">3</NombreParte></Metadatos>
      <EstructurasFuncionales>
        <EstructuraFuncional tipoParte="Artículo"><Texto>Artículo 1.- Artículo de OTRA ley ficticia.</Texto>
          <Metadatos><NombreParte presente="si">1</NombreParte></Metadatos></EstructuraFuncional>
      </EstructurasFuncionales>
    </EstructuraFuncional>
    <EstructuraFuncional tipoParte="Artículo"><Texto>Artículo 4.- Disposición final ficticia del decreto.</Texto>
      <Metadatos><NombreParte presente="si">4</NombreParte></Metadatos></EstructuraFuncional>
  </EstructurasFuncionales>
</Norma>""".encode()


def test_norma_envoltorio() -> None:
    norma = dn.parsear_xml(ENVOLTORIO, id_esperado=888888)
    md = norma.markdown_cuerpo
    assert "### Decreto con Fuerza de Ley 9, artículo 1\n\nDeja sin efecto" in md
    assert "### Decreto con Fuerza de Ley 9, artículo 2\n\nFíjase" in md
    assert "#### Artículo 1\n\nPrimer artículo del código sintético." in md
    assert md.count("#### Artículo ") == 2
    assert "OTRA ley ficticia" not in md
    assert "Se omiten aquí los 1 artículos" in md
    # Los artículos propios del decreto (incluido el 4, posterior) quedan antes del código.
    assert md.index("artículo 4\n\nDisposición final") < md.index("#### Artículo 1")
    assert any("envoltorio secundario omitido" in a for a in norma.avisos)
    assert any("envoltorio" in a for a in norma.avisos)


def test_envoltorio_en_cargador_de_cc(tmp_path: Path) -> None:
    base_local = pytest.importorskip("cc.knowledge.base_local")
    indice = {"codigos": [{"slug": "cod", "nombre": "Código X", "idNorma": 888888, "archivo": "codigos/cod.md"}]}
    (tmp_path / "index.json").write_text(json.dumps(indice), encoding="utf-8")
    (tmp_path / "codigos").mkdir()
    md = dn.a_markdown(dn.parsear_xml(ENVOLTORIO), "Código X", "2026-10-15")
    (tmp_path / "codigos" / "cod.md").write_text(md, encoding="utf-8")
    base = base_local.BaseLegalLocal(tmp_path)
    base.cargar()
    assert {"kb://cod#art-1", "kb://cod#art-2"} <= base.refs()
    assert "kb://cod#art-1-2" not in base.refs()
    assert "Primer artículo del código sintético" in base.obtener("kb://cod#art-1").texto
