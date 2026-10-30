"""Entrada modular para a interface Streamlit."""

from pathlib import Path
import runpy


def run() -> None:
    runpy.run_path(str(Path(__file__).resolve().parent.parent / "app.py"), run_name="__main__")


if __name__ == "__main__":
    run()
