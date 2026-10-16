"""Pruebas del lector del RSS de "Fallos Relevantes" de los TTA.

Usan ``tests/muestras/feed_tta_sintetico.xml``: una MUESTRA SINTÉTICA con la misma estructura
del RSS 2.0 de WordPress de tta.cl (item, guid ?p=, content:encoded con enlaces a PDF).
Carátulas, fechas, ids y textos son ficticios. No se hace ningún pedido de red.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "herramientas"))

import descargar_fallos_tta as tta  # noqa: E402

MUESTRA = (RAIZ / "tests" / "muestras" / "feed_tta_sintetico.xml").read_bytes()
TEXTO_PDF = "Texto sintético de sentencia. " * 20


@pytest.fixture()
def entradas() -> list[dict]:
    return tta.parsear_feed(MUESTRA)


def test_parsear_feed(entradas: list[dict]) -> None:
    assert [e["id"] for e in entradas] == ["tta-9001", "tta-9000"]
    nueva, antigua = entradas
    assert nueva["post_id"] == 9001
    assert nueva["fecha"] == "2030-09-29"
    assert nueva["titulo"].startswith("TTA de Prueba: Sociedad Ficticia SpA")
    assert nueva["materia"] == "Se acoge el reclamo sintético de prueba."
    assert nueva["etiquetas"] == ["iva"]
    assert nueva["url"] == "https://www.tta.cl/sociedad-ficticia-spa-con-sii/"
    # PDF sin duplicar y en orden; el externo se conserva para registrarlo, no para bajarlo.
    assert nueva["pdfs"] == [
        "https://www.tta.cl/wp-content/uploads/2030/09/SENT-PRUEBA.pdf",
        "https://otro.example/externo.pdf",
    ]
    assert "se publicó primero" not in nueva["resena"]
    assert ".ttafr" not in nueva["resena"]
    assert "“SOCIEDAD FICTICIA SPA CON SII”." in nueva["resena"].splitlines()
    assert antigua["pdfs"] == [] and antigua["fecha"] == "2021-04-30"


def test_feed_invalido() -> None:
    with pytest.raises(tta.ErrorTTA):
        tta.parsear_feed(b"<html>bloqueado</html>")
    with pytest.raises(tta.ErrorTTA):
        tta.parsear_feed(b"<html><body>no es rss</body></html>")


def test_markdown(entradas: list[dict]) -> None:
    md = tta.componer_markdown(
        entradas[0],
        [{"url": entradas[0]["pdfs"][0], "texto": "# título del PDF\nCuerpo."}, {"url": entradas[0]["pdfs"][1], "motivo": "enlace fuera de tta.cl"}],
    )
    assert md.startswith("---\nid: tta-9001\n")
    assert "fuente: https://www.tta.cl/sociedad-ficticia-spa-con-sii/\n" in md
    assert "verificar vigencia antes de citar" in md.lower()
    assert "## Documento: SENT-PRUEBA.pdf" in md
    assert "\\# título del PDF" in md and "\\# línea que empieza" in md
    assert "(sin texto extraíble: enlace fuera de tta.cl)" in md


class ClienteFalso:
    """Sustituye la red: sirve el RSS sintético en la página 1, 404 en la 2 y un 'PDF'."""

    def __init__(self, pausa: float) -> None:
        self.pedidos: list[str] = []

    def cargar_robots(self) -> str:
        return "User-agent: *\nDisallow:\n"

    def get(self, url: str, *, permitir_404: bool = False) -> bytes | None:
        self.pedidos.append(url)
        if "paged=1" in url:
            return MUESTRA
        if "paged=" in url:
            return None
        return b"%PDF-falso"


def _base(tmp_path: Path) -> Path:
    (tmp_path / "index.json").write_text(json.dumps({"codigos": [], "documentos": []}), encoding="utf-8")
    return tmp_path


def test_main_incremental(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clientes: list[ClienteFalso] = []

    def nuevo(pausa: float) -> ClienteFalso:
        clientes.append(ClienteFalso(pausa))
        return clientes[-1]

    monkeypatch.setattr(tta, "Cliente", nuevo)
    monkeypatch.setattr(tta, "extraer_texto", lambda datos, url: TEXTO_PDF)
    raiz = _base(tmp_path)
    assert tta.main(["--raiz", str(raiz)]) == 0
    meta = json.loads((raiz / "tta" / "metadata.json").read_text(encoding="utf-8"))
    assert [m["id"] for m in meta] == ["tta-9001", "tta-9000"]
    assert meta[0]["completo"] is True and meta[0]["texto"] == "tta/texto/tta-9001.md"
    assert meta[0]["sentencias"][1]["motivo"].startswith("enlace fuera de tta.cl")
    assert "texto" not in meta[0]["sentencias"][0]
    texto = (raiz / "tta" / "texto" / "tta-9001.md").read_text(encoding="utf-8")
    assert TEXTO_PDF.strip() in texto
    indice = json.loads((raiz / "index.json").read_text(encoding="utf-8"))
    assert indice["fallos_tta"]["total"] == 2
    assert indice["codigos"] == [] and indice["documentos"] == []
    pdfs_primera = [u for u in clientes[0].pedidos if u.endswith(".pdf")]
    assert pdfs_primera == ["https://www.tta.cl/wp-content/uploads/2030/09/SENT-PRUEBA.pdf"]

    # Segunda corrida: nada nuevo, no se vuelve a bajar ningún PDF.
    assert tta.main(["--raiz", str(raiz)]) == 0
    assert not [u for u in clientes[1].pedidos if u.endswith(".pdf")]


def test_solo_probar_no_escribe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tta, "Cliente", ClienteFalso)
    monkeypatch.setattr(tta, "extraer_texto", lambda datos, url: TEXTO_PDF)
    raiz = _base(tmp_path)
    assert tta.main(["--raiz", str(raiz), "--solo-probar"]) == 0
    assert not (raiz / "tta").exists()
    assert "fallos_tta" not in json.loads((raiz / "index.json").read_text(encoding="utf-8"))


def test_robots_prohibe() -> None:
    cliente = tta.Cliente(pausa=5)
    import urllib.robotparser

    cliente._robots = urllib.robotparser.RobotFileParser()
    cliente._robots.parse(["User-agent: *", "Disallow: /"])
    with pytest.raises(tta.ErrorTTA, match="robots.txt"):
        cliente.get("https://www.tta.cl/category/fallos-relevantes/feed/")


def test_pausa_minima() -> None:
    assert tta.Cliente(pausa=1).pausa == tta.PAUSA_MINIMA


def test_url_ascii_codifica_caracteres_no_ascii() -> None:
    # Caso real (run 38853396440): un PDF de tta.cl con "N°" en el nombre botaba urllib.
    url = "https://www.tta.cl/wp-content/uploads/2021/05/Sentencia-RUC-N°-20-9-0000123-4.pdf"
    codificada = tta.url_ascii(url)
    assert codificada.isascii()
    assert codificada == "https://www.tta.cl/wp-content/uploads/2021/05/Sentencia-RUC-N%C2%B0-20-9-0000123-4.pdf"


def test_url_ascii_no_codifica_dos_veces() -> None:
    url = "https://www.tta.cl/category/fallos-relevantes/feed/?paged=2&x=%C2%B0"
    assert tta.url_ascii(url) == url
    assert tta.url_ascii("https://www.tta.cl/año/fallo.pdf") == "https://www.tta.cl/a%C3%B1o/fallo.pdf"
