import pytest

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
    assert sorted(lists) == ["_all", "eastus", "westeurope"]
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
