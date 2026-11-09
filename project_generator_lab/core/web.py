"""Opening a link before it reaches a learner: a dead page, an error or a non-document is dropped by code.

A page counts as alive when it answers below 400 with HTML, PDF or text, or when bot protection refuses the check. Its title and the first lines of
visible text go to the writer model, which picks relevant sources among live pages and never types an address.
"""

import asyncio
import html
import ipaddress
import re
import socket
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>", re.I | re.S)
SPACES = re.compile(r"\s+")
DOCUMENTS = ("text/html", "application/pdf", "text/plain", "application/xhtml")
# Bot protection answers these for a live page; 404, 410 and server errors mean the page is gone.
GUARDED = {401, 403, 429}


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str


# Tags that mark a few words inside a sentence: taken out without a space, the sentence reads as the page shows it.
INLINE = re.compile(r"</?(?:a|abbr|b|bdi|cite|code|del|dfn|em|i|ins|kbd|mark|q|s|samp|small|span|strong|sub|sup|tt|u|var)\b[^>]*>", re.I)


def visible(markup: str, limit: int, prose: bool = False) -> str:
    """The text of a page as a reader sees it. prose — for a page to be quoted from: a space put in place of every
    tag turned «un<em>safe</em>» into «un safe» and «tip<sup>1</sup>.» into «tip 1 .», and a quotation with such
    seams passed for the page's own words (independent review 06.10.2026)."""
    if prose:
        markup = INLINE.sub("", TAGS.sub(lambda tag: tag.group(0) if INLINE.fullmatch(tag.group(0)) else " ", markup))
        return SPACES.sub(" ", html.unescape(markup)).strip()[:limit]
    return SPACES.sub(" ", html.unescape(TAGS.sub(" ", markup))).strip()[:limit]


# On the production server (set by production.py): connect to the address that was checked, ignore proxy variables.
SERVER = False
# A page is read up to this size: only its title and first lines are used; a 260 MB «page» took 1.3 GB (audit 30.09.2026).
MAX_BYTES = 512 * 1024


def address(url: str) -> str | None:
    """The address to connect to when the link is an ordinary web address, else None. On the production server a link
    from a search answer must not reach the server itself, the internal network or a cloud metadata address: every
    address the name resolves to is checked, and the connection then goes to one of those checked addresses."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname or port not in {None, 80, 443}:
        return None
    try:
        addresses = [str(info[4][0]).split("%")[0] for info in socket.getaddrinfo(parts.hostname, None)]
    except (OSError, UnicodeError):
        return None
    if not addresses or not all(ipaddress.ip_address(a).is_global and not special(a) for a in addresses):
        return None
    return addresses[0]


def special(value: str) -> bool:
    """IPv6 forms that Python counts as global but that reach IPv4 inside: NAT64 and the old IPv4-compatible form."""
    ip = ipaddress.ip_address(value)
    return ip.version == 6 and (ip in ipaddress.ip_network("64:ff9b::/96") or (int(ip) >> 32) == 0)


def public(url: str) -> bool:
    return address(url) is not None


def pinned(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, object]]:
    """The request to the checked address: the name goes in Host and in TLS (SNI and certificate check), so a second DNS
    answer (rebinding) cannot send the connection elsewhere. A Cyrillic name (.рф) travels in its ASCII form."""
    parts = urlsplit(url)
    host = (parts.hostname or "").encode("idna").decode("ascii")
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{parts.port}" if parts.port else "")
    extensions: dict[str, object] = {"sni_hostname": host} if parts.scheme == "https" else {}
    return urlunsplit(parts._replace(netloc=netloc)), {"Host": host + (f":{parts.port}" if parts.port else "")}, extensions


async def read_capped(response: httpx.Response) -> bytes:
    """At most MAX_BYTES of the page, counted after decompression: a small gzip body can inflate a hundredfold, and
    httpx would inflate every chunk in full (independent review 30.09.2026: 298 KB of gzip took 133 MB)."""
    encoding = response.headers.get("content-encoding", "identity").lower().strip()
    if encoding not in {"identity", "gzip", "deflate", "x-gzip"}:
        return b""  # brotli and others: the page stays alive, its text is not read
    inflate = zlib.decompressobj(wbits=47) if encoding != "identity" else None
    body = b""
    try:
        async for chunk in response.aiter_raw():
            body += inflate.decompress(chunk, MAX_BYTES - len(body)) if inflate else chunk
            if len(body) >= MAX_BYTES:
                break
    except httpx.StreamConsumed:
        body = response.content  # a response already held in memory (tests, transports that pre-read the body)
    return body[:MAX_BYTES]


async def fetch_page(url: str, client: httpx.AsyncClient, limit: int = 700,
                     resolve: Callable[[str], str | None] = address, pin: bool | None = None, prose: bool = False) -> Page | None:
    pin = SERVER if pin is None else pin
    try:
        # Redirects are followed by hand, so every hop is checked, not only the first address.
        for _ in range(4):
            ip = await asyncio.to_thread(resolve, url)
            if ip is None:
                return None
            target, headers, extensions = pinned(url, ip) if pin and ip else (url, {}, {})
            async with client.stream("GET", target, headers=headers, extensions=extensions, follow_redirects=False) as response:
                location = response.headers.get("location")
                if response.is_redirect and location:
                    url = urljoin(url, location)
                    continue
                status, kind = response.status_code, response.headers.get("content-type", "").lower()
                body = await read_capped(response) if status < 400 and kind.startswith(("text/", "application/xhtml")) else b""
                encoding = response.encoding or "utf-8"
            break
        else:
            return None
    except (httpx.HTTPError, ValueError):
        return None
    if status in GUARDED:
        # The site exists but keeps robots out: the page is kept, the writer model sees that its text was not read.
        return Page(url, "", f"Сайт не отдал текст проверке (код {status}); страница существует.")
    if status >= 400 or not kind.startswith(DOCUMENTS):
        return None
    if "pdf" in kind:
        return Page(url, url.rsplit("/", 1)[-1], "PDF-документ")
    text = body.decode(encoding, errors="replace")
    title = TITLE.search(text)
    return Page(url, visible(title.group(1), 200) if title else "", visible(text, limit, prose))


async def open_pages(urls: list[str], limit: int = 24, text_limit: int = 700, prose: bool = False) -> list[Page]:
    """Live pages among the urls, in their order. On a local computer the system proxy settings apply, as for any
    browser there; on the production server they do not: the connection must go to the address that was checked."""
    headers = {"User-Agent": "Mozilla/5.0 (project-lab link check)", "Accept-Language": "ru,en;q=0.8", "Accept-Encoding": "gzip, identity"}
    # On the server no connection is reused: the pool keys connections by address only, so a second site on the same
    # CDN address would ride on the first site's TLS session without its own certificate check.
    limits = httpx.Limits(max_keepalive_connections=0) if SERVER else httpx.Limits()
    async with httpx.AsyncClient(timeout=12, headers=headers, trust_env=not SERVER, limits=limits) as client:
        # A page that trickles bytes would hold the search for minutes: each page gets 20 seconds in all.
        pages = await asyncio.gather(*(asyncio.wait_for(fetch_page(url, client, text_limit, prose=prose), 20) for url in urls[:limit]), return_exceptions=True)
    return [page for page in pages if isinstance(page, Page)]
