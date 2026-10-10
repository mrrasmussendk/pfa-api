"""Jev's HTTP call: retried with backoff on 429, never on 401."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from heimdall.commands import review
from heimdall.commands.review import ReviewError


def test_http_transport_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    sleeps: list[float] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return json.dumps({"model": "jev-1", "answers": {}, "usage": {}}).encode()

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("Authorization"), timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow", hdrs=None, fp=io.BytesIO(b"rate limited"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    call = review.http_transport("k3y", sleep=sleeps.append)

    assert call({"model": "jev-latest", "state": {}, "questions": {}}) == {"model": "jev-1", "answers": {}, "usage": {}}
    assert len(calls) == 2 and calls[0][0] == review.API_URL and calls[0][1] == "Bearer k3y" and sleeps == [1.0]


def test_http_transport_401_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "nope", hdrs=None, fp=io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ReviewError, match="401"):
        review.http_transport("bad", sleep=lambda _s: None)({"model": "jev-latest", "state": {}, "questions": {}})


def test_a_base_that_git_would_read_as_an_option_is_refused(tmp_path) -> None:
    from heimdall.commands.review import git_diff

    with pytest.raises(ReviewError, match="not an option"):
        git_diff(str(tmp_path), "--output=/tmp/x")
