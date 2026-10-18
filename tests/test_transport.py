"""The bundled stdlib transport, exercised without a network.

A fake HTTPS handler is installed in front of the real urllib opener chain, so the
redirect and error handling under test is urllib's own rather than a stand-in.
"""

from __future__ import annotations

import io
import urllib.request
import urllib.response
from email.message import Message

import pytest

from thinwrap.location.transport import (
    HttpRequest,
    UrllibTransport,
    _DEFAULT_TIMEOUT_SECONDS,
    _NoRedirect,
    default_transport,
)


def _headers(**values: str) -> Message:
    msg = Message()
    for k, v in values.items():
        msg[k.replace("_", "-")] = v
    return msg


class _FakeHTTPS(urllib.request.BaseHandler):
    """Answers every https request with a canned status, headers and body, and
    records what urllib actually sent (headers, body, timeout)."""

    handler_order = 100  # ahead of the stock HTTPSHandler

    def __init__(self, status: int, body: bytes = b"", **headers: str) -> None:
        self.status, self.body, self.headers = status, body, headers
        self.requests: list = []

    def https_open(self, req):
        self.requests.append(req)
        resp = urllib.response.addinfourl(io.BytesIO(self.body), _headers(**self.headers), req.full_url, self.status)
        resp.msg = "canned"
        return resp


def _transport_with(handler: _FakeHTTPS, **kwargs) -> UrllibTransport:
    t = UrllibTransport(**kwargs)
    # Same chain the default builds, plus the fake network.
    t._opener = urllib.request.build_opener(_NoRedirect, handler)
    return t


def test_success_returns_status_lowercased_headers_and_body():
    handler = _FakeHTTPS(200, b'{"ok":true}', Content_Type="application/json", X_Request_Id="r1")
    resp = _transport_with(handler).send(
        HttpRequest(method="POST", url="https://api.example.com/x", headers={"X-Api-Key": "k"}, body=b"{}")
    )
    assert resp.status == 200
    assert resp.body == b'{"ok":true}'
    assert resp.headers == {"content-type": "application/json", "x-request-id": "r1"}
    sent = handler.requests[0]
    assert sent.get_method() == "POST"
    assert sent.data == b"{}"
    assert sent.get_header("X-api-key") == "k"


def test_http_error_is_returned_as_a_response_not_raised():
    handler = _FakeHTTPS(429, b'{"error":"slow down"}', Retry_After="7")
    resp = _transport_with(handler).send(HttpRequest(method="GET", url="https://api.example.com/x"))
    assert resp.status == 429
    assert resp.body == b'{"error":"slow down"}'
    assert resp.headers["retry-after"] == "7"


@pytest.mark.parametrize("status", [301, 302, 303, 307])
def test_redirect_is_surfaced_not_followed(status):
    handler = _FakeHTTPS(status, b"", Location="https://elsewhere.example.com/y")
    resp = _transport_with(handler).send(
        HttpRequest(method="GET", url="https://api.example.com/x", headers={"Authorization": "secret"})
    )
    assert resp.status == status
    assert resp.headers["location"] == "https://elsewhere.example.com/y"
    # Exactly one request: the credentials never went to the redirect target.
    assert [r.full_url for r in handler.requests] == ["https://api.example.com/x"]


def test_no_redirect_handler_declines_every_redirect():
    req = urllib.request.Request("https://api.example.com/x")
    assert _NoRedirect().redirect_request(req, None, 302, "Found", _headers(), "https://evil.example.com/") is None


def test_default_timeout_is_applied_to_every_request():
    handler = _FakeHTTPS(200, b"")
    _transport_with(handler).send(HttpRequest(method="GET", url="https://api.example.com/x"))
    assert _DEFAULT_TIMEOUT_SECONDS == 30.0
    assert handler.requests[0].timeout == 30.0


def test_custom_timeout_is_applied():
    handler = _FakeHTTPS(200, b"")
    _transport_with(handler, timeout=2.5).send(HttpRequest(method="GET", url="https://api.example.com/x"))
    assert handler.requests[0].timeout == 2.5


def test_default_transport_is_a_shared_urllib_transport():
    t = default_transport()
    assert isinstance(t, UrllibTransport)
    assert t is default_transport()
    assert t._timeout == _DEFAULT_TIMEOUT_SECONDS


def test_default_user_agent_is_sent_because_cloudflare_blocks_urllibs():
    handler = _FakeHTTPS(200)
    _transport_with(handler).send(HttpRequest(method="GET", url="https://api.example.com/x"))
    assert handler.requests[0].get_header("User-agent") == "thinwrap-location-python"


@pytest.mark.parametrize("key", ["User-Agent", "user-agent"])
def test_a_user_agent_on_the_request_wins(key):
    handler = _FakeHTTPS(200)
    _transport_with(handler).send(HttpRequest(method="GET", url="https://api.example.com/x", headers={key: "acme/2"}))
    assert handler.requests[0].get_header("User-agent") == "acme/2"
    assert [k for k in handler.requests[0].header_items() if k[0].lower() == "user-agent"] == [("User-agent", "acme/2")]
