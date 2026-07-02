"""ASGI adapter for the existing Spravochnik WSGI viewer."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from starlette.middleware.wsgi import WSGIMiddleware
from starlette.types import Message, Receive, Scope, Send

from content_factory.api.integrations.spravochnik_curriculum_sync import sync_spravochnik_curriculum_plans

logger = logging.getLogger("content_factory.api.integrations.spravochnik_mount")


class PrefixRewriteASGI:
    """Rewrite legacy absolute links and redirects to a mounted prefix."""

    def __init__(self, app, prefix: str) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        status_code: int | None = None
        headers: list[tuple[bytes, bytes]] = []
        body_parts: list[bytes] = []

        async def buffer_send(message: Message) -> None:
            nonlocal status_code, headers
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = b"".join(body_parts)
            headers, body = self._rewrite_response(headers, body)
            if self._should_sync_curriculum(scope, status_code):
                await self._sync_curriculum_after_mutation()
            await send({"type": "http.response.start", "status": status_code or 200, "headers": headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, buffer_send)

    def _should_sync_curriculum(self, scope: Scope, status_code: int | None) -> bool:
        """Detect successful UP mutations routed through the mounted legacy UI."""

        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        if status_code is None or not (200 <= status_code < 400):
            return False
        path = str(scope.get("path") or "")
        root_path = str(scope.get("root_path") or "")
        full_path = f"{root_path}{path}"
        return path.startswith("/up") or full_path.startswith(f"{self.prefix}/up")

    async def _sync_curriculum_after_mutation(self) -> None:
        """Mirror changed UP data without making the legacy UI depend on the mirror."""

        try:
            await asyncio.to_thread(sync_spravochnik_curriculum_plans)
        except Exception:
            logger.exception("Failed to mirror Spravochnik curriculum plans after UP mutation")

    def _rewrite_response(self, headers: list[tuple[bytes, bytes]], body: bytes) -> tuple[list[tuple[bytes, bytes]], bytes]:
        rewritten_headers: list[tuple[bytes, bytes]] = []
        content_type = ""
        for key, value in headers:
            lower_key = key.lower()
            if lower_key == b"location":
                value = self._rewrite_location(value)
            elif lower_key == b"content-type":
                content_type = value.decode("latin-1", errors="ignore")
            elif lower_key == b"content-length":
                continue
            rewritten_headers.append((key, value))
        body = self._rewrite_body(content_type, body)
        rewritten_headers.append((b"content-length", str(len(body)).encode("ascii")))
        return rewritten_headers, body

    def _rewrite_location(self, value: bytes) -> bytes:
        location = value.decode("latin-1", errors="ignore")
        if location.startswith("/") and not location.startswith(f"{self.prefix}/"):
            return f"{self.prefix}{location}".encode("latin-1")
        return value

    def _rewrite_body(self, content_type: str, body: bytes) -> bytes:
        if not any(marker in content_type for marker in ("text/html", "text/css", "javascript")):
            return body
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return body
        replacements = {
            'href="/': f'href="{self.prefix}/',
            'src="/': f'src="{self.prefix}/',
            'action="/': f'action="{self.prefix}/',
            'fetch("/': f'fetch("{self.prefix}/',
            "fetch('/": f"fetch('{self.prefix}/",
            'url("/': f'url("{self.prefix}/',
            "url('/": f"url('{self.prefix}/",
            'window.location.href = "/': f'window.location.href = "{self.prefix}/',
            "window.location.href = '/": f"window.location.href = '{self.prefix}/",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.encode("utf-8")


def build_spravochnik_app(prefix: str = "/app/spravochnik") -> PrefixRewriteASGI:
    """Build the mounted Spravochnik viewer using its current WSGI app."""

    from content_factory.catalog.viewer.app import DEFAULT_DB, DEFAULT_SUMMARY, create_app

    db_path = Path(os.getenv("SPRAVOCHNIK_SQLITE_PATH", str(DEFAULT_DB)))
    summary_path = Path(os.getenv("SPRAVOCHNIK_SUMMARY_PATH", str(DEFAULT_SUMMARY)))
    wsgi_app = create_app(db_path, summary_path)
    return PrefixRewriteASGI(WSGIMiddleware(wsgi_app), prefix=prefix)
