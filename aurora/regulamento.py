"""Acesso ao regulamento por capitulo.

Garantia 4: o texto nunca e carregado inteiro. O especialista recebe so o
sumario (titulos) nas instrucoes e le um capitulo por vez pela tool; e ele roda
como AgentTool, num Runner proprio, entao nem esse capitulo entra nos eventos
da sessao do morador.
"""

from __future__ import annotations

import re

from .config import DADOS

_TITULO = re.compile(r"^## Capítulo (?P<numero>[IVXLC]+): (?P<titulo>.+)$", re.M)


def _carregar() -> dict[str, tuple[str, str]]:
    texto = (DADOS / "regulamento.md").read_text(encoding="utf-8")
    marcas = list(_TITULO.finditer(texto))
    capitulos: dict[str, tuple[str, str]] = {}
    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        capitulos[m.group("numero")] = (m.group("titulo").strip(), texto[m.start():fim].strip())
    return capitulos


CAPITULOS = _carregar()


def sumario() -> str:
    return "\n".join(f"- Capítulo {n}: {titulo}" for n, (titulo, _) in CAPITULOS.items())


def capitulo(numero: str) -> str | None:
    entrada = CAPITULOS.get(numero.strip().upper().removeprefix("CAPÍTULO ").strip())
    return entrada[1] if entrada else None
