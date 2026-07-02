"""Совместимая команда для сборки листа адъюдикации.

Основная реализация живёт в `content_audit.adjudication`, этот файл оставлен,
чтобы команда из рабочего протокола продолжила работать.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from content_audit.adjudication_cli import main


if __name__ == "__main__":
    raise SystemExit(main(["sample", *sys.argv[1:]]))
