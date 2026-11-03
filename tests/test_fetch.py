import urllib.request

import pytest

from pa_edl.fetch import Fetcher, FetchError, UnsafeFetchError, _HttpsOnlyRedirectHandler


class FakeResponse:
    def __init__(self, body: bytes, url: str):
        self.body = body
        self.url = url

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, failures=0, body=b'{"ok": true}', final_url=None):
        self.calls = 0
        self.failures = failures
        self.body = body
        self.final_url = final_url

    def __call__(self, req, timeout):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("boom")
        return FakeResponse(self.body, self.final_url or req.full_url)


@pytest.fixture
def opener():
    return FakeOpener()


@pytest.fixture
def fetcher(opener):
    return Fetcher(backoff=0, opener=opener)


def test_caches_by_url(fetcher, opener):
    assert fetcher.json("https://example.com/feed") == {"ok": True}
    assert fetcher.text("https://example.com/feed") == '{"ok": true}'
    assert opener.calls == 1


def test_retries_then_succeeds():
    opener = FakeOpener(failures=2)
    f = Fetcher(retries=3, backoff=0, opener=opener)
    assert f.bytes("https://example.com/feed") == b'{"ok": true}'
    assert opener.calls == 3


def test_gives_up():
    opener = FakeOpener(failures=5)
    f = Fetcher(retries=2, backoff=0, opener=opener)
    with pytest.raises(FetchError) as excinfo:
        f.bytes("https://example.com/feed")
    assert "boom" in str(excinfo.value)
    assert opener.calls == 2


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_rejects_non_https_url(fetcher, opener, bad_url):
    with pytest.raises(UnsafeFetchError):
        fetcher.bytes(bad_url)
    assert opener.calls == 0


def test_rejects_oversized_response_without_retry():
    opener = FakeOpener(body=b"x" * 11)
    f = Fetcher(retries=3, backoff=0, max_bytes=10, opener=opener)
    with pytest.raises(UnsafeFetchError) as excinfo:
        f.bytes("https://example.com/feed")
    assert "exceeds" in str(excinfo.value)
    assert opener.calls == 1


def test_accepts_response_at_size_limit():
    opener = FakeOpener(body=b"x" * 11)
    f = Fetcher(retries=3, backoff=0, max_bytes=11, opener=opener)
    assert f.bytes("https://example.com/feed") == b"x" * 11


def test_rejects_non_https_final_url():
    opener = FakeOpener(final_url="http://example.com/x")
    f = Fetcher(retries=3, backoff=0, opener=opener)
    with pytest.raises(UnsafeFetchError):
        f.bytes("https://example.com/feed")
    assert opener.calls == 1


def test_redirect_handler_blocks_http():
    req = urllib.request.Request("https://example.com/a")
    handler = _HttpsOnlyRedirectHandler()
    with pytest.raises(UnsafeFetchError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://example.com/b")


def test_redirect_handler_allows_https():
    req = urllib.request.Request("https://example.com/a")
    handler = _HttpsOnlyRedirectHandler()
    result = handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/b")
    assert result is not None
    assert result.full_url == "https://example.com/b"
