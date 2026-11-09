"""Where links come from: citations in every gateway format, addresses in the text, and pages code has opened."""

import asyncio

import httpx
from project_generator_lab.core import web
from project_generator_lab.core.search import citations, links_in_text
from project_generator_lab.core.web import Page, fetch_page, visible


def test_citations_are_read_from_every_gateway_format():
    raw = {"citations": ["https://a.example/1", "ftp://x"], "search_results": [{"url": "https://b.example/2"}, "junk"],
           "choices": [{"message": {"citations": ["https://a.example/1"],
                                    "annotations": [{"type": "url_citation", "url_citation": {"url": "https://c.example/3."}},
                                                    {"url": "https://d.example/4"}]}}]}
    assert citations(raw) == ["https://a.example/1", "https://b.example/2", "https://c.example/3", "https://d.example/4"]
    assert citations({"choices": []}) == []


def test_addresses_are_taken_from_free_text():
    text = "Документация — https://docs.example.org/roles — основы.\nСм. также (https://habr.example/post/1), «https://x.example/a»."
    assert links_in_text(text) == ["https://docs.example.org/roles", "https://habr.example/post/1", "https://x.example/a"]


def test_visible_text_drops_scripts_and_tags():
    assert visible("<p>Роли&nbsp;в <b>команде</b></p><script>var x=1</script>", 50) == "Роли в команде"


def pages(handler) -> dict[str, Page | None]:
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return {url: await fetch_page(url, client, resolve=lambda u: "93.184.215.14") for url in URLS}
    return asyncio.run(run())


URLS = ["https://ok.example/", "https://gone.example/", "https://guarded.example/", "https://zip.example/", "https://down.example/"]


def test_dead_pages_and_non_documents_are_dropped_guarded_ones_kept():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "ok.example":
            return httpx.Response(200, html="<html><title>Роли</title><body>Текст про роли</body></html>")
        if host == "gone.example":
            return httpx.Response(404, html="нет")
        if host == "guarded.example":
            return httpx.Response(403, html="captcha")
        if host == "zip.example":
            return httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
        raise httpx.ConnectError("no route")
    found = pages(handler)
    ok, guarded = found["https://ok.example/"], found["https://guarded.example/"]
    assert ok is not None and ok.title == "Роли" and "Текст про роли" in ok.text
    assert guarded is not None and "403" in guarded.text
    assert found["https://gone.example/"] is None and found["https://zip.example/"] is None and found["https://down.example/"] is None


def test_only_public_web_addresses_are_opened():
    from project_generator_lab.core.web import public
    for url in ("http://127.0.0.1:8011/api/config", "http://localhost/", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/", "ftp://example.org/", "https://93.184.215.14:8443/", "file:///etc/passwd"):
        assert not public(url), url
    assert public("https://93.184.215.14/")


def test_a_redirect_to_an_internal_address_is_not_followed():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "ok.example":
            return httpx.Response(302, headers={"location": "http://inside.example/secret"})
        return httpx.Response(200, html="<title>Секрет</title>")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_page("https://ok.example/", client, resolve=lambda u: None if "inside" in u else "93.184.215.14")
    assert asyncio.run(run()) is None and calls == ["https://ok.example/"]


def test_on_the_server_the_connection_goes_to_the_checked_address_and_the_body_is_capped(monkeypatch):
    # Audit 30.09.2026: the name was checked, then resolved again by httpx to connect — a DNS answer that changes
    # between the two (rebinding) reached a loopback page. Now the request goes to the checked IP; the name travels
    # in Host and in TLS (SNI), so the certificate is still checked for it.
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers["host"], request.extensions.get("sni_hostname")))
        # A title after the size cap is never read: the page is taken up to MAX_BYTES only.
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<p>" + "a" * (web.MAX_BYTES + 10) + "</p><title>Поздний заголовок</title>")

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_page("https://docs.example.org/page", client, resolve=lambda u: "93.184.215.14", pin=True)

    page = asyncio.run(fetch())
    assert seen == [("93.184.215.14", "docs.example.org", "docs.example.org")]
    assert page is not None and page.url == "https://docs.example.org/page" and page.title == ""


def test_ipv6_forms_that_reach_ipv4_inside_are_not_public(monkeypatch):
    for inside in ("64:ff9b::7f00:1", "::127.0.0.1", "::ffff:127.0.0.1", "127.0.0.1", "169.254.169.254"):
        monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, value=inside: [(0, 0, 0, "", (value, 0))])
        assert web.address("https://rebind.example/") is None, inside
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a: [(0, 0, 0, "", ("2606:4700::6810:84e5", 0))])
    assert web.address("https://ok.example/") == "2606:4700::6810:84e5"
    assert web.pinned("https://ok.example:443/a?b=1", "2606:4700::6810:84e5")[0] == "https://[2606:4700::6810:84e5]:443/a?b=1"


def test_a_compressed_page_is_capped_after_decompression_and_an_idn_host_is_sent_in_ascii():
    # Independent review 30.09.2026: the cap counted decoded bytes but httpx inflated every chunk in full (298 KB of gzip
    # took 133 MB); a Cyrillic host made the Host header fail and the page was silently dropped.
    import gzip

    bomb = gzip.compress(b"<p>" + b"a" * (40 * 1024 * 1024) + b"</p>")
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append((request.headers["host"], request.extensions.get("sni_hostname")))
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"}, stream=httpx.ByteStream(bomb))

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_page("https://пример.рф/страница", client, resolve=lambda u: "93.184.215.14", pin=True)

    page = asyncio.run(fetch())
    assert page is not None and page.url == "https://пример.рф/страница" and page.text.startswith("a")
    assert hosts == [("xn--e1afmkfd.xn--p1ai", "xn--e1afmkfd.xn--p1ai")]

    async def capped():
        response = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(bomb))
        return await web.read_capped(response)

    assert len(asyncio.run(capped())) == web.MAX_BYTES


def fetch_with(handler, url="https://docs.example.org/page", pin=False):
    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_page(url, client, resolve=lambda u: "93.184.215.14", pin=pin)
    return asyncio.run(fetch())


def test_pages_are_read_as_before_through_the_streamed_reader():
    # Retention for the rewritten fetch_page (audit 30.09.2026): redirects, PDF, charsets, titles, dead pages.
    pdf = fetch_with(lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, stream=httpx.ByteStream(b"%PDF")),
                     url="https://docs.example.org/files/guide.pdf")
    assert pdf == Page("https://docs.example.org/files/guide.pdf", "guide.pdf", "PDF-документ")
    cp1251 = "<title>Документация</title><p>Текст страницы</p>".encode("cp1251")
    page = fetch_with(lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=windows-1251"}, stream=httpx.ByteStream(cp1251)))
    assert page is not None and (page.title, page.text) == ("Документация", "Документация Текст страницы")
    long_title = ("<title>" + "Т" * 300 + "</title>").encode()
    page = fetch_with(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, stream=httpx.ByteStream(long_title)))
    assert page is not None and len(page.title) == 200
    # A relative redirect is followed from the original address; four hops at most.
    hops = []

    def moving(request: httpx.Request) -> httpx.Response:
        hops.append(request.url.path)
        if request.url.path == "/end":
            return httpx.Response(200, headers={"content-type": "text/plain"}, stream=httpx.ByteStream(b"final"))
        return httpx.Response(302, headers={"location": "/end"})

    page = fetch_with(moving)
    assert page is not None and page.url == "https://docs.example.org/end" and hops == ["/page", "/end"]
    assert fetch_with(lambda r: httpx.Response(302, headers={"location": "/again"})) is None
    assert fetch_with(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, stream=httpx.ByteStream(b"x"))) is None
    guarded = fetch_with(lambda r: httpx.Response(403, headers={"content-type": "text/html"}, stream=httpx.ByteStream(b"no")))
    assert guarded == Page("https://docs.example.org/page", "", "Сайт не отдал текст проверке (код 403); страница существует.")


def test_the_capped_reader_handles_each_encoding_and_the_exact_limit():
    import zlib

    async def read(body: bytes, encoding: str | None) -> bytes:
        headers = {"content-encoding": encoding} if encoding else {}
        return await web.read_capped(httpx.Response(200, headers=headers, stream=httpx.ByteStream(body)))

    plain = b"x" * (web.MAX_BYTES + 5)
    assert asyncio.run(read(plain, None)) == b"x" * web.MAX_BYTES
    assert asyncio.run(read(b"short", None)) == b"short"
    deflated = zlib.compress(b"deflated text")
    assert asyncio.run(read(deflated, "deflate")) == b"deflated text"
    import gzip
    assert asyncio.run(read(gzip.compress(b"gz text"), "x-gzip")) == b"gz text"
    # Brotli and the like: the page stays alive, its text is not read.
    assert asyncio.run(read(b"\x1b\x00", "br")) == b""


def test_address_ports_schemes_and_the_pinned_request(monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a: [(0, 0, 0, "", ("93.184.215.14", 0))])
    assert web.address("http://ok.example/") == "93.184.215.14" and web.address("https://ok.example:443/") == "93.184.215.14"
    assert web.address("http://ok.example:80/") == "93.184.215.14"
    assert web.address("https://ok.example:8443/") is None and web.address("ftp://ok.example/") is None
    # Plain http has no TLS name; an explicit port stays in Host.
    assert web.pinned("http://ok.example/a", "93.184.215.14") == ("http://93.184.215.14/a", {"Host": "ok.example"}, {})
    assert web.pinned("https://ok.example:443/a", "93.184.215.14")[1] == {"Host": "ok.example:443"}


def test_on_the_server_pinning_is_the_default_and_the_text_is_cut_to_the_limit(monkeypatch):
    # production.py sets SERVER; open_pages calls fetch_page without «pin» — the default must follow SERVER.
    monkeypatch.setattr(web, "SERVER", True)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, headers={"content-type": "text/plain"}, stream=httpx.ByteStream(b"w " * 2000))

    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_page("https://docs.example.org/p", client, resolve=lambda u: "93.184.215.14")

    page = asyncio.run(fetch())
    assert seen == ["93.184.215.14"] and page is not None and len(page.text) == 700


def test_a_compressed_page_arriving_in_pieces_is_put_together():
    import gzip

    class Pieces(httpx.AsyncByteStream):
        def __init__(self, data: bytes) -> None:
            self.data = data

        async def __aiter__(self):
            for i in range(0, len(self.data), 7):
                yield self.data[i:i + 7]

    async def read() -> bytes:
        body = gzip.compress("Страница целиком".encode())
        return await web.read_capped(httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Pieces(body)))

    assert asyncio.run(read()).decode() == "Страница целиком"
