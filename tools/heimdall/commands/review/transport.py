"""The one network call: ``POST`` to the System One API with the bounded state and the questions."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .base import ReviewError

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"
API_URL_ENV = "TYPESAFE_API_URL"

Transport = Callable[[dict[str, Any]], dict[str, Any]]


def http_transport(api_key: str, url: str = API_URL, timeout: float = 90.0, sleep: Callable[[float], None] = time.sleep) -> Transport:
    """POST to the System One API; exponential backoff on 429/529 and transient network errors."""

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        delay = 1.0
        last: str = ""
        for attempt in range(4):
            req = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if e.code in (429, 529) and attempt < 3:
                    last = f"HTTP {e.code}: {detail}"
                elif e.code == 401:
                    raise ReviewError("TypeSafe API rejected the key (401)") from None
                else:
                    raise ReviewError(f"TypeSafe API error HTTP {e.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 3:
                    raise ReviewError(f"TypeSafe API unreachable: {e}") from None
                last = str(e)
            sleep(delay)
            delay *= 2
        raise ReviewError(f"TypeSafe API gave up after retries: {last}")

    return call
