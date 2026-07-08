"""URL policy and network helpers for audit checks."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

TRUSTED_REDIRECT_HOST_GROUPS = (
    frozenset({"opros.so", "oprosso.ru", "oprosso.net", "new.oprosso.net"}),
)


def _check_url(url: str, timeout_seconds: float) -> tuple[int, str | None, str | None]:
    """Check an external URL via HEAD with manual redirect handling."""

    current_url = url
    headers = {"User-Agent": "ContentAudit/0.1 (+https://github.com/Zheltenkov/Auditor)"}
    try:
        for _redirect_index in range(5):
            policy_error = _url_policy_error(current_url)
            if policy_error is not None:
                return 0, current_url, policy_error
            response = requests.head(current_url, allow_redirects=False, timeout=timeout_seconds, headers=headers)
            if response.status_code in {405, 403}:
                response = requests.get(current_url, allow_redirects=False, timeout=timeout_seconds, stream=True, headers=headers)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    return response.status_code, current_url, None
                current_url = urljoin(current_url, location)
                continue
            return response.status_code, current_url, None
        return 0, current_url, "Слишком длинная цепочка перенаправлений."
    except requests.RequestException as exc:
        return 0, current_url, str(exc)


def _is_transient_http_status(status_code: int) -> bool:
    """Separate temporary unavailability from durable broken links."""

    return status_code in {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def _is_redirect_chain_error(error: str) -> bool:
    """Treat redirect-chain failures as stronger link-rot signals."""

    return "перенаправ" in error.lower() or "redirect" in error.lower()


def _redirect_smells_like_rot(original_url: str, final_url: str | None) -> bool:
    """Detect redirects to another domain or to a generic landing page."""

    if not final_url:
        return False
    original = urlparse(original_url)
    final = urlparse(final_url)
    original_host = (original.hostname or "").lower().removeprefix("www.")
    final_host = (final.hostname or "").lower().removeprefix("www.")
    if _same_trusted_redirect_family(original_host, final_host) and (final.path or "/") not in {"", "/"}:
        return False
    if original_host and final_host and original_host != final_host:
        return True
    original_path = original.path or "/"
    final_path = final.path or "/"
    return original_path not in {"", "/"} and final_path in {"", "/"}


def _same_trusted_redirect_family(original_host: str, final_host: str) -> bool:
    """Allow known short-link/main-domain pairs."""

    return any(original_host in group and final_host in group for group in TRUSTED_REDIRECT_HOST_GROUPS)


def _url_policy_error(url: str) -> str | None:
    """Reject unsupported schemes, local addresses and internal IPs."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"Неподдерживаемая схема ссылки: {parsed.scheme or 'не указана'}."
    if parsed.username or parsed.password:
        return "Ссылки с учётными данными в адресе не проверяются автоматически."
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return "Не удалось определить домен ссылки."
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
        return "Локальные адреса не проверяются автоматически."
    try:
        ip_address = ipaddress.ip_address(hostname)
    except ValueError:
        ip_address = None
    if ip_address and (ip_address.is_private or ip_address.is_loopback or ip_address.is_link_local or ip_address.is_reserved):
        return "Внутренние IP-адреса не проверяются автоматически."
    return None


def _is_inside(path: Path, root: Path) -> bool:
    """Protect local links from escaping the audited project root."""

    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False
