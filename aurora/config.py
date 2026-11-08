"""Caminhos e variaveis de ambiente do assistente."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DADOS = RAIZ / "dados"
ESTADO = Path(os.environ.get("AURORA_ESTADO", RAIZ / "estado"))
BANCO_CONDOMINIO = ESTADO / "condominio.db"
BANCO_SESSOES = ESTADO / "sessoes.db"

APP_NAME = "residencial_aurora"

MODELO_PRINCIPAL = os.environ.get("AURORA_MODELO_PRINCIPAL") or "gemini-3.8-flash"
MODELO_ESPECIALISTAS = os.environ.get("AURORA_MODELO_ESPECIALISTAS") or "gemini-3.8-flash"
MODELO_REGULAMENTO = os.environ.get("AURORA_MODELO_REGULAMENTO") or "gemini-3.5-flash-lite"
