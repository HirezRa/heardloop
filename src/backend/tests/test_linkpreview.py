"""Open Graph fetch for link cards: parsing, redirects, limits and the destination guard. No database."""

import asyncio

import httpx
import pytest

from heardloop import linkpreview

HTML = """<!doctype html><html><head>
<meta charset="utf-8"><title>Fallback title</title>
<meta property="og:title" content="  Example   article ">
<meta property="og:description" content="A short description.">
<meta property="og:image" content="/img/cover.png">
<meta property="og:site_name" content="Example">
</head><body><meta property="og:title" content="ignored in body"></body></html>"""


def _run(handler, url: str) -> linkpreview.LinkPreview:  # type: ignore[no-untyped-def]
    async def go() -> linkpreview.LinkPreview:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await linkpreview.fetch_preview(http, url)

    return asyncio.run(go())


def test_parse_open_graph() -> None:
    p = linkpreview.parse_html(HTML, "https://example.com/post/1")
    assert p.title == "Example article" and p.description == "A short description."
    assert p.image_url == "https://example.com/img/cover.png" and p.site_name == "Example"


def test_title_fallback_and_limits() -> None:
    html = "<html><head><title>" + "x" * 500 + "</title></head><body></body></html>"
    p = linkpreview.parse_html(html, "https://example.com/")
    assert p.title == "x" * linkpreview.TITLE_MAX and p.description is None and p.image_url is None


def test_fetch_follows_redirect_and_rechecks_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[str] = []

    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        checked.append(url)
        return None

    monkeypatch.setattr(linkpreview, "check_destination", guard)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=HTML)

    p = _run(handler, "https://example.com/old")
    assert p.url == "https://example.com/new" and p.title == "Example article"
    assert checked == ["https://example.com/old", "https://example.com/new"]


def test_blocked_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        return "blocked_destination"

    monkeypatch.setattr(linkpreview, "check_destination", guard)
    with pytest.raises(linkpreview.LinkPreviewError, match="blocked_destination"):
        _run(lambda r: httpx.Response(200, text=HTML), "http://intranet.example.com/")


@pytest.mark.parametrize("url", ["ftp://example.com/x", "https://user:pw@example.com/", "example.com", ""])
def test_invalid_urls(url: str) -> None:
    with pytest.raises(linkpreview.LinkPreviewError, match="invalid_url"):
        linkpreview.validate_url(url)


def test_non_html_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        return None

    monkeypatch.setattr(linkpreview, "check_destination", guard)
    with pytest.raises(linkpreview.LinkPreviewError, match="not_html"):
        _run(
            lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
            "https://example.com/a.pdf",
        )
    with pytest.raises(linkpreview.LinkPreviewError, match="http_404"):
        _run(lambda r: httpx.Response(404), "https://example.com/missing")


def test_body_size_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        return None

    monkeypatch.setattr(linkpreview, "check_destination", guard)
    big = HTML + ("<p>filler</p>" * 200_000)  # several MB; the parser only needs <head>
    p = _run(
        lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text=big), "https://example.com/"
    )
    assert p.title == "Example article"


def test_redirect_loop_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        return None

    monkeypatch.setattr(linkpreview, "check_destination", guard)
    with pytest.raises(linkpreview.LinkPreviewError, match="too_many_redirects"):
        _run(lambda r: httpx.Response(302, headers={"location": "/again"}), "https://example.com/loop")


def test_decode_html_charsets() -> None:
    # Hebrew via escapes so this file stays English-only for check_english.
    he = "\u05e9\u05dc\u05d5\u05dd"
    body = f"<html><head><meta charset='windows-1255'><title>{he}</title></head></html>".encode("cp1255")
    assert he in linkpreview.decode_html(body, "text/html")
    assert he in linkpreview.decode_html(f"<title>{he}</title>".encode(), "text/html; charset=utf-8")
    assert linkpreview.decode_html(b"<title>x</title>", "text/html; charset=nonsense") == "<title>x</title>"


def test_redirect_without_location_and_bad_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def guard(url: str, *, allow_private: bool = True) -> str | None:
        return None

    monkeypatch.setattr(linkpreview, "check_destination", guard)
    with pytest.raises(linkpreview.LinkPreviewError, match="bad_redirect"):
        _run(lambda r: httpx.Response(302), "https://example.com/x")
    # httpx may reject the broken Location itself; either way it is a clean 4xx, never a crash.
    with pytest.raises(linkpreview.LinkPreviewError):
        _run(lambda r: httpx.Response(302, headers={"location": "http://[::1"}), "https://example.com/y")
    with pytest.raises(linkpreview.LinkPreviewError, match="invalid_url"):
        linkpreview.validate_url("http://[::1")


def test_malformed_html_does_not_crash() -> None:
    p = linkpreview.parse_html(
        "<html><head><title>ok</title><meta <<<>>> property='x' </head><body", "https://example.com/"
    )
    assert p.title == "ok"
