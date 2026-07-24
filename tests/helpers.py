from __future__ import annotations

import gzip
import json
from urllib.parse import parse_qs, urlsplit

from thinwrap.location.transport import HttpRequest, HttpResponse


class FakeTransport:
    """Test transport: returns queued responses in order (repeats the last once
    exhausted) and records every request."""

    def __init__(self, *responses: HttpResponse) -> None:
        self.queue = list(responses)
        self.calls: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.calls.append(request)
        idx = len(self.calls) - 1
        if idx < len(self.queue):
            return self.queue[idx]
        if self.queue:
            return self.queue[-1]
        return HttpResponse(status=200, headers={}, body=b"")

    @property
    def last(self) -> HttpRequest:
        return self.calls[-1]


def resp(status: int, body: str = "", **headers: str) -> HttpResponse:
    hdrs = {k.replace("_", "-").lower(): v for k, v in headers.items()}
    return HttpResponse(status=status, headers=hdrs, body=body.encode("utf-8"))


def gzip_resp(status: int, body: str, **headers: str) -> HttpResponse:
    """Like resp() but gzip-compresses the body and defaults Content-Encoding:
    gzip — mirrors HERE's gzip-only matrix result endpoint."""
    hdrs = {k.replace("_", "-").lower(): v for k, v in headers.items()}
    hdrs.setdefault("content-encoding", "gzip")
    return HttpResponse(status=status, headers=hdrs, body=gzip.compress(body.encode("utf-8")))


def qget(req: HttpRequest, key: str) -> str:
    # urlsplit (not urlparse) so ';' in a path segment isn't parsed as params.
    return parse_qs(urlsplit(req.url).query).get(key, [""])[0]


def path_of(req: HttpRequest) -> str:
    return urlsplit(req.url).path


def body_json(req: HttpRequest):
    return json.loads(req.body.decode("utf-8"))


def body_form(req: HttpRequest) -> dict:
    return {k: v[0] for k, v in parse_qs(req.body.decode("utf-8")).items()}


def no_sleep(_seconds: float) -> None:
    pass
