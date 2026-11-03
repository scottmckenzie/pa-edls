"""HTTPS fetching with retries and a per-run cache.

Several EDL definitions often read the same upstream feed (e.g. Azure service
tags), so responses are cached by URL for the lifetime of a Fetcher.

Feeds decide firewall policy, so only https is allowed, including for every
redirect hop, and response size is capped.
"""

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from pa_edl import __version__

USER_AGENT = f"pa-edl/{__version__}"

# The Azure service tags file is ~4 MB; leave plenty of headroom.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024


class FetchError(RuntimeError):
    pass


class UnsafeFetchError(FetchError):
    """A request that must not be retried (wrong scheme, oversized response)."""


def _require_https(url: str) -> None:
    if urllib.parse.urlsplit(url).scheme != "https":
        raise UnsafeFetchError(f"refusing non-https URL {url!r}")


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    def __init__(
        self,
        retries: int = 3,
        timeout: float = 60.0,
        backoff: float = 2.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        opener: Callable[..., Any] | None = None,  # injectable for tests
    ):
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self.max_bytes = max_bytes
        self._cache: dict[str, bytes] = {}
        self._open = opener or urllib.request.build_opener(_HttpsOnlyRedirectHandler).open

    def bytes(self, url: str) -> bytes:
        if url in self._cache:
            return self._cache[url]
        _require_https(url)
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with self._open(req, timeout=self.timeout) as resp:
                    _require_https(resp.geturl())
                    data = resp.read(self.max_bytes + 1)
                if len(data) > self.max_bytes:
                    raise UnsafeFetchError(f"response from {url} exceeds {self.max_bytes} bytes")
                self._cache[url] = data
                return data
            except UnsafeFetchError:
                raise
            except Exception as exc:  # retried, then re-raised below
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * attempt)
        raise FetchError(f"failed to fetch {url}: {last_exc}") from last_exc

    def text(self, url: str) -> str:
        return self.bytes(url).decode("utf-8", errors="replace")

    def json(self, url: str) -> Any:
        return json.loads(self.bytes(url))
