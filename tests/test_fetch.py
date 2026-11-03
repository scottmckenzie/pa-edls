import io

import pytest

from pa_edl import fetch
from pa_edl.fetch import Fetcher, FetchError


class FakeUrlopen:
    def __init__(self, failures=0, body=b'{"ok": true}'):
        self.failures = failures
        self.body = body
        self.calls = 0

    def __call__(self, req, timeout):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("boom")
        return io.BytesIO(self.body)


def test_caches_by_url(monkeypatch):
    opener = FakeUrlopen()
    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)
    f = Fetcher(backoff=0)
    assert f.json("https://x") == {"ok": True}
    assert f.text("https://x") == '{"ok": true}'
    assert opener.calls == 1


def test_retries_then_succeeds(monkeypatch):
    opener = FakeUrlopen(failures=2)
    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)
    assert Fetcher(retries=3, backoff=0).bytes("https://x") == b'{"ok": true}'
    assert opener.calls == 3


def test_gives_up(monkeypatch):
    opener = FakeUrlopen(failures=5)
    monkeypatch.setattr(fetch.urllib.request, "urlopen", opener)
    with pytest.raises(FetchError, match="boom"):
        Fetcher(retries=2, backoff=0).bytes("https://x")
    assert opener.calls == 2
