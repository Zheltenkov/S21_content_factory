"""
Опциональный контекст didactics: локальная папка content_gen/didactics/ в .gitignore
и на проде отсутствует — импорт не должен валить приложение.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("content_gen.utils.didactics_loader")


def _strict_didactics_enabled() -> bool:
    raw = os.getenv("DIDACTICS_STRICT", "").strip().lower()
    return raw in {"1", "true", "yes", "on", "strict"} or bool(os.getenv("PYTEST_CURRENT_TEST"))


def compose_didactics_context(config_name: str) -> tuple[str, dict[str, Any]]:
    try:
        from content_gen.didactics.composer import compose_didactics_context as _compose

        return _compose(config_name)
    except ImportError:
        return "", {}
    except Exception as exc:
        trace = {
            "didactics_agent": config_name,
            "didactics_error": str(exc),
            "didactics_context_missing": True,
        }
        if _strict_didactics_enabled():
            raise
        logger.warning("Didactics context unavailable for %s: %s", config_name, exc)
        return "", trace
