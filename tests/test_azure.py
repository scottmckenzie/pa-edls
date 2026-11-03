import json
from typing import cast

import pytest

from pa_edl.fetch import Fetcher
from pa_edl.providers import ConfigError
from pa_edl.providers.azure import AzureProvider, find_feed_url


def build(fetcher, **spec):
    return {lst.name: lst for lst in AzureProvider(fetcher).build({"service": "Storage", **spec})}


def test_find_feed_url_picks_newest():
    html = (
        '"https://download.microsoft.com/download/a/b/ServiceTags_Public_20261019.json" '
        '"https://download.microsoft.com/download/c/d/ServiceTags_Public_20261026.json"'
    )
    assert find_feed_url(html).endswith("ServiceTags_Public_20261026.json")


def test_find_feed_url_missing():
    with pytest.raises(RuntimeError):
        find_feed_url("<html>nothing here</html>")


def test_split_by_region(azure_fetcher):
    lists = build(azure_fetcher)
    assert sorted(lists) == ["_all", "eastus", "eastusstage", "francecentral", "westeurope"]
    assert "2603:1030:209::/48" in lists["eastus"].prefixes
    assert "Storage.EastUS" in lists["eastus"].source
    assert "changeNumber 424" in lists["eastus"].source


def test_prefix_match_requires_dot(azure_fetcher):
    # StorageSyncService must not leak into Storage lists.
    lists = build(azure_fetcher)
    assert all("4.171.52.120/29" not in lst.prefixes for lst in lists.values())


def test_without_global(azure_fetcher):
    assert "_all" not in build(azure_fetcher, include_global=False)


def test_region_filter_is_case_insensitive(azure_fetcher):
    assert list(build(azure_fetcher, regions=["EastUS"])) == ["eastus"]


def test_unknown_region_fails(azure_fetcher):
    with pytest.raises(ConfigError, match="eastus9"):
        build(azure_fetcher, regions=["eastus9"])


def test_split_none_uses_global_tag(azure_fetcher):
    lists = build(azure_fetcher, split="none")
    assert list(lists) == ["_all"]
    assert "Storage" in lists["_all"].source


def test_split_none_with_regions_combines(azure_fetcher):
    lists = build(azure_fetcher, split="none", regions=["eastus", "westeurope"])
    assert set(lists["_all"].prefixes) >= {"13.65.107.32/28", "40.71.10.16/28"}


def test_unknown_service(azure_fetcher):
    with pytest.raises(ConfigError, match="NoSuchService"):
        AzureProvider(azure_fetcher).build({"service": "NoSuchService"})


def test_unknown_cloud(azure_fetcher):
    with pytest.raises(ConfigError, match="mars"):
        build(azure_fetcher, cloud="mars")


def test_explicit_url_skips_discovery(azure_fetcher):
    from conftest import FEED_URL

    build(azure_fetcher, url=FEED_URL)
    assert azure_fetcher.calls == [FEED_URL]


def test_case_colliding_regions_fail():
    from conftest import FakeFetcher

    doc = {
        "changeNumber": 1,
        "values": [
            {"name": "Storage.EastUS", "properties": {"region": "eastus", "addressPrefixes": ["20.0.0.0/24"]}},
            {"name": "Storage.EASTUS", "properties": {"region": "EastUS", "addressPrefixes": ["20.0.1.0/24"]}},
        ],
    }
    url = "https://download.microsoft.com/download/x/ServiceTags_Public_20261026.json"
    with pytest.raises(RuntimeError, match="both map to list"):
        AzureProvider(cast(Fetcher, FakeFetcher({url: json.dumps(doc)}))).build({"service": "Storage", "url": url})


def test_lists_named_by_tag_not_region_id(azure_fetcher):
    lists = build(azure_fetcher)
    # Storage.EastUSStage shares region "eastus" but must not overwrite or merge into eastus.
    assert "52.239.152.0/24" not in lists["eastus"].prefixes
    assert lists["eastusstage"].prefixes == ["52.239.152.0/24"]
    # Storage.FranceCentral's region id is the legacy "centralfrance"; the list uses the tag name.
    assert "centralfrance" not in lists
    assert "Storage.FranceCentral" in lists["francecentral"].source
