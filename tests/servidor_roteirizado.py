"""Sobe a API com o modelo roteirizado (sem Gemini), na mesma porta 8000.

    uv run python -m tests.servidor_roteirizado
"""

import uvicorn

from aurora.agentes import criar_app
from aurora.api import criar_api
from tests.modelo_roteirizado import ModeloRoteirizado

if __name__ == "__main__":
    modelo = ModeloRoteirizado()
    uvicorn.run(criar_api(criar_app(modelo, modelo, modelo)), host="127.0.0.1", port=8000)
