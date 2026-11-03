"""HTTP fetching with retries and a per-run cache.

Several EDL definitions often read the same upstream feed (e.g. Azure service
tags), so responses are cached by URL for the lifetime of a Fetcher.
"""

import json
import time
import urllib.request
from typing import Any

from pa_edl import __version__

USER_AGENT = f"pa-edl/{__version__} (+https://github.com/)"


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, retries: int = 3, timeout: float = 60.0, backoff: float = 2.0):
        self.retries = retries
        self.timeout = timeout
        self.backoff = backoff
        self._cache: dict[str, bytes] = {}

    def bytes(self, url: str) -> bytes:
        if url in self._cache:
            return self._cache[url]
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                self._cache[url] = data
                return data
            except Exception as exc:  # retried, then re-raised below
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * attempt)
        raise FetchError(f"failed to fetch {url}: {last_exc}") from last_exc

    def text(self, url: str) -> str:
        return self.bytes(url).decode("utf-8", errors="replace")

    def json(self, url: str) -> Any:
        return json.loads(self.bytes(url))
