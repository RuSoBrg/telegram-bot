"""Entrada modular para o bot Telegram.

O módulo legado ``bot.py`` continua disponível para compatibilidade.
"""

from bot import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
