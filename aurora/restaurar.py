"""Restaura reservas e visitantes ao estado de `dados/` e apaga as sessões.

    uv run python -m aurora.restaurar
"""

from . import banco
from .config import BANCO_SESSOES


def main() -> None:
    banco.restaurar()
    for sufixo in ("", "-wal", "-shm", "-journal"):
        arquivo = BANCO_SESSOES.with_name(BANCO_SESSOES.name + sufixo)
        if arquivo.exists():
            arquivo.unlink()
    print("dados restaurados: reservas e visitantes iguais a dados/; sessões apagadas.")


if __name__ == "__main__":
    main()
