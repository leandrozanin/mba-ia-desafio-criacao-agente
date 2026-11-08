"""Sobe a API em http://localhost:8000:  uv run python -m aurora"""

import os

import uvicorn

from .api import criar_api

if __name__ == "__main__":
    uvicorn.run(criar_api(), host=os.environ.get("AURORA_HOST", "0.0.0.0"), port=int(os.environ.get("AURORA_PORT", "8000")))
