"""Shared application errors for generation services."""

from __future__ import annotations

from typing import Any


class GenerationServiceError(Exception):
    """HTTP-adapter friendly error raised by generation application services."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
