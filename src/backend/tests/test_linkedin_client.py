import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from heardloop.config import Settings
from heardloop.linkedin.client import DEFAULT_TOKEN_SECONDS, LinkedInClient, LinkedInError, escape_commentary
from heardloop.linkedin.state import OAuthState, sign_state, verify_state
from heardloop.publishing.base import LinkAttachment

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
KEY = b"k" * 32


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATABASE_URL="postgresql+asyncpg://u@h/db",
        HEARDLOOP_TOKEN_KEY="A" * 43 + "=",
        LINKEDIN_CLIENT_ID="client-id-example",
        LINKEDIN_CLIENT_SECRET="client-secret-example",  # noqa: S106  # fake
        LINKEDIN_REDIRECT_URI="http://localhost:8380/api/v1/linkedin/callback",
    )


def _client(handler) -> LinkedInClient:  # type: ignore[no-untyped-def]
    return LinkedInClient(_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_settings_defaults_and_placeholders() -> None:
    s = Settings(  # type: ignore[call-arg]
        DATABASE_URL="x",
        HEARDLOOP_TOKEN_KEY="y",  # noqa: S106  # fake
        LINKEDIN_CLIENT_ID="<client-id>",
        LINKEDIN_API_VERSION="",
    )
    assert s.linkedin_client_id == "" and not s.linkedin_configured
    assert s.linkedin_api_version == "202608"
    with pytest.raises(ValueError):
        Settings(  # type: ignore[call-arg]
            DATABASE_URL="x",
            HEARDLOOP_TOKEN_KEY="y",  # noqa: S106  # fake
            LINKEDIN_API_VERSION="2026-08",
        )


def test_authorization_url() -> None:
    url = _client(lambda r: httpx.Response(500)).authorization_url("STATE")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert q["scope"] == ["openid profile w_member_social"]  # no "email": never stored or used
    assert q["state"] == ["STATE"] and q["response_type"] == ["code"]
    assert "client_secret" not in url


async def test_revoke_token_is_best_effort() -> None:
    seen: list[dict[str, list[str]]] = []

    def ok(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/oauth/v2/revoke"
        seen.append(parse_qs(req.content.decode()))
        return httpx.Response(200)

    assert await _client(ok).revoke_token("tok") is True
    assert seen[0]["token"] == ["tok"] and seen[0]["client_id"] == ["client-id-example"]
    assert set(seen[0]) == {"client_id", "client_secret", "token"}
    assert await _client(lambda r: httpx.Response(400)).revoke_token("tok") is False

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await _client(boom).revoke_token("tok") is False  # never raises


async def test_exchange_and_userinfo() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/accessToken"):
            form = parse_qs(req.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
            return httpx.Response(
                200, json={"access_token": "tok", "expires_in": 5184000, "scope": "openid,w_member_social"}
            )
        assert req.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"sub": "abc123", "name": "Example Person"})

    c = _client(handler)
    token = await c.exchange_code("the-code")
    assert token.expires_in == 5184000 and "w_member_social" in token.scopes
    assert "tok" not in repr(token)
    member = await c.userinfo(token.access_token)
    assert member.urn == "urn:li:person:abc123" and member.name == "Example Person"


async def test_exchange_failure_has_no_secrets() -> None:
    c = _client(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(LinkedInError) as info:
        await c.exchange_code("bad")
    assert info.value.code == "token_exchange_failed:http_400:invalid_grant"
    assert "secret" not in info.value.code


@pytest.mark.parametrize(
    ("status", "headers", "success", "transient", "code"),
    [
        (201, {"x-restli-id": "urn:li:share:1"}, True, False, None),
        (201, {}, False, False, "no_post_id"),
        (429, {}, False, True, "http_429"),
        (201, {"x-restli-id": "not-a-urn"}, False, False, "no_post_id"),
        (503, {}, False, True, "http_503"),
        (500, {}, False, False, "unknown_outcome"),
        (504, {}, False, False, "unknown_outcome"),
        (401, {}, False, False, "token_invalid"),
        (403, {}, False, False, "not_permitted"),
        (426, {}, False, False, "api_version_retired"),
        (422, {}, False, False, "http_422"),
    ],
)
async def test_create_post_result_mapping(
    status: int, headers: dict[str, str], success: bool, transient: bool, code: str | None
) -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx.Response(status, headers=headers, json={})

    result = await _client(handler).create_text_post("tok", "urn:li:person:abc", "Hi")
    assert (result.success, result.transient) == (success, transient)
    if code:
        assert result.error_code is not None and result.error_code.startswith(code)
    h = seen["headers"]
    assert isinstance(h, dict)
    assert h["linkedin-version"] == "202608" and h["x-restli-protocol-version"] == "2.0.0"
    body = seen["body"]
    assert isinstance(body, dict)
    assert (
        body["author"] == "urn:li:person:abc"
        and body["commentary"] == "Hi"
        and body["visibility"] == "PUBLIC"
    )


async def test_create_post_timeout_is_not_retried() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    result = await _client(handler).create_text_post("tok", "urn:li:person:abc", "Hi")
    assert not result.success and not result.transient and result.error_code == "timeout_unknown_outcome"


def test_state_roundtrip_and_rejections() -> None:
    st = OAuthState(uuid.uuid4(), uuid.uuid4(), "nonce-1")
    token = sign_state(KEY, st, NOW)
    assert verify_state(KEY, token, NOW, cookie_nonce="nonce-1") == st
    assert verify_state(KEY, token, NOW, cookie_nonce="other") is None
    assert verify_state(KEY, token, NOW, cookie_nonce=None) is None
    assert verify_state(b"x" * 32, token, NOW, cookie_nonce="nonce-1") is None
    assert verify_state(KEY, token, NOW + timedelta(minutes=11), cookie_nonce="nonce-1") is None
    assert verify_state(KEY, token[:-2] + "AA", NOW, cookie_nonce="nonce-1") is None
    assert verify_state(KEY, "garbage", NOW, cookie_nonce="nonce-1") is None


@pytest.mark.parametrize(
    ("exc", "transient", "code"),
    [
        (httpx.ConnectError, True, "network_error"),
        (httpx.ReadError, False, "unknown_outcome"),
        (httpx.RemoteProtocolError, False, "unknown_outcome"),
    ],
)
async def test_network_errors_retry_only_when_nothing_was_sent(
    exc: type[httpx.TransportError], transient: bool, code: str
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise exc("boom", request=req)

    result = await _client(handler).create_text_post("tok", "urn:li:person:abc", "Hi")
    assert result.transient is transient and result.error_code == code


def test_escape_commentary() -> None:
    raw = "Hi @bob (x) [y] a_b *c* ~d~ <e> {f} |g| \\ #tag"
    expected = r"Hi \@bob \(x\) \[y\] a\_b \*c\* \~d\~ \<e\> \{f\} \|g\| \\ #tag"
    assert escape_commentary(raw) == expected


async def test_exchange_handles_bad_json_and_missing_expiry() -> None:
    bad = _client(lambda r: httpx.Response(200, content=b"not json"))
    with pytest.raises(LinkedInError):
        await bad.exchange_code("c")
    no_exp = _client(lambda r: httpx.Response(200, json={"access_token": "t", "scope": "w_member_social"}))
    assert (await no_exp.exchange_code("c")).expires_in == DEFAULT_TOKEN_SECONDS


def test_redirect_uri_validation() -> None:
    base = {"DATABASE_URL": "x", "HEARDLOOP_TOKEN_KEY": "y"}
    ok = Settings(**base, LINKEDIN_REDIRECT_URI="http://localhost:8380/api/v1/linkedin/callback")  # type: ignore[arg-type]
    assert ok.linkedin_redirect_uri.endswith("/callback")
    for bad in ("http://example.com/api/v1/linkedin/callback", "https://example.com/other"):
        with pytest.raises(ValueError):
            Settings(**base, LINKEDIN_REDIRECT_URI=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("image_urn", [None, "urn:li:image:C4D"])
async def test_create_post_with_link_card(image_urn: str | None) -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"}, json={})

    link = LinkAttachment(url="https://example.com/post", title="A title", description="Some words")
    result = await _client(handler).create_text_post("tok", "urn:li:person:abc", "Read this", image_urn, link)
    assert result.success
    body = seen["body"]
    assert isinstance(body, dict)
    article = body["content"]["article"]
    assert article["source"] == "https://example.com/post" and article["title"] == "A title"
    assert article["description"] == "Some words"
    assert article.get("thumbnail") == image_urn and "media" not in body["content"]
