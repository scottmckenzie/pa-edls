import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FEED_URL = "https://download.microsoft.com/download/x/ServiceTags_Public_20261026.json"


class FakeFetcher:
    """Serves canned responses instead of hitting the network."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def text(self, url):
        self.calls.append(url)
        return self.responses[url]

    def json(self, url):
        self.calls.append(url)
        return json.loads(self.responses[url])


@pytest.fixture
def azure_fetcher():
    page = f'<a href="{FEED_URL}">download</a>'
    return FakeFetcher(
        {
            "https://www.microsoft.com/en-us/download/details.aspx?id=56519": page,
            FEED_URL: (FIXTURES / "servicetags.json").read_text(),
        }
    )
