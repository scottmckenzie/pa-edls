"""Azure service tags (https://learn.microsoft.com/azure/virtual-network/service-tags-overview).

Microsoft publishes the full service tag list as a weekly JSON file whose URL
changes with every release, so the current URL is discovered from the download
page. Regional tags are named "<Service>.<Region>" (e.g. Storage.EastUS) and
carry the lowercase region id (e.g. "eastus") in properties.region.

Spec keys:
  service         service tag name, e.g. "Storage", "Sql", "AzureKeyVault" (required)
  cloud           public | usgov | china (default public)
  split           "region" (one list per region) | "none" (one combined list)
  include_global  with split=region, also write the service-wide list as _all (default true)
  regions         optional list of region ids to include, e.g. ["eastus", "westeurope"]
  url             optional explicit ServiceTags JSON URL (skips page discovery)
"""

import re
from typing import Any

from pa_edl.providers.base import ConfigError, EdlList, Provider, register

DOWNLOAD_PAGES = {
    "public": "https://www.microsoft.com/en-us/download/details.aspx?id=56519",
    "usgov": "https://www.microsoft.com/en-us/download/details.aspx?id=57063",
    "china": "https://www.microsoft.com/en-us/download/details.aspx?id=57062",
}

_JSON_URL_RE = re.compile(r"https://download\.microsoft\.com/download/[^\"'\s<>]+/ServiceTags_\w+?_(\d{8})\.json")

GLOBAL_LIST = "_all"


def find_feed_url(page_html: str) -> str:
    matches = {m.group(0): m.group(1) for m in _JSON_URL_RE.finditer(page_html)}
    if not matches:
        raise RuntimeError("no ServiceTags JSON link found on the Microsoft download page")
    # Newest file by the date stamp in its name.
    return max(matches, key=lambda url: matches[url])


@register
class AzureProvider(Provider):
    id = "azure"
    description = "Azure service tags (regional and global IP ranges per service)"

    def _feed(self, spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        url = spec.get("url")
        if not url:
            cloud = spec.get("cloud", "public")
            if cloud not in DOWNLOAD_PAGES:
                raise ConfigError(f"azure: unknown cloud {cloud!r}; expected one of {sorted(DOWNLOAD_PAGES)}")
            url = find_feed_url(self.fetcher.text(DOWNLOAD_PAGES[cloud]))
        data = self.fetcher.json(url)
        if not isinstance(data, dict) or not isinstance(data.get("values"), list):
            raise RuntimeError(f"azure: unexpected service tag document format at {url}")
        return url, data

    def build(self, spec: dict[str, Any]) -> list[EdlList]:
        service = spec.get("service")
        if not service or not isinstance(service, str):
            raise ConfigError("azure: 'service' is required (e.g. service = \"Storage\")")
        split = spec.get("split", "region")
        if split not in ("region", "none"):
            raise ConfigError(f"azure: split must be 'region' or 'none', got {split!r}")
        wanted = spec.get("regions")
        if wanted is not None:
            if not isinstance(wanted, list) or not all(isinstance(r, str) for r in wanted):
                raise ConfigError("azure: 'regions' must be a list of region ids")
            wanted = {r.lower() for r in wanted}

        url, data = self._feed(spec)
        feed_name = url.rsplit("/", 1)[-1]
        change = data.get("changeNumber")

        global_tag = None
        regional: dict[str, dict[str, Any]] = {}
        for tag in data["values"]:
            name = tag.get("name", "")
            props = tag.get("properties", {})
            if name == service:
                global_tag = tag
            elif name.startswith(service + ".") and props.get("region"):
                regional[props["region"].lower()] = tag

        if global_tag is None and not regional:
            raise ConfigError(f"azure: service tag {service!r} not found in {feed_name}")

        if wanted is not None:
            missing = wanted - regional.keys()
            if missing:
                raise ConfigError(f"azure: {service} has no tag for region(s): {', '.join(sorted(missing))}")
            regional = {r: t for r, t in regional.items() if r in wanted}

        def src(tag: dict[str, Any]) -> str:
            return f"Azure {feed_name} (changeNumber {change}), tag {tag['name']}"

        def prefixes(tag: dict[str, Any]) -> list[str]:
            return list(tag.get("properties", {}).get("addressPrefixes", []))

        lists: list[EdlList] = []
        if split == "region":
            for region in sorted(regional):
                tag = regional[region]
                lists.append(EdlList(region, prefixes(tag), src(tag)))
            if spec.get("include_global", True) and global_tag is not None and wanted is None:
                lists.append(EdlList(GLOBAL_LIST, prefixes(global_tag), src(global_tag)))
        else:  # none: one combined list
            if wanted is None:
                if global_tag is None:
                    raise ConfigError(f'azure: {service} has no service-wide tag; set split = "region"')
                lists.append(EdlList(GLOBAL_LIST, prefixes(global_tag), src(global_tag)))
            else:
                combined = [p for r in sorted(regional) for p in prefixes(regional[r])]
                tags = ", ".join(regional[r]["name"] for r in sorted(regional))
                lists.append(EdlList(GLOBAL_LIST, combined, f"Azure {feed_name} (changeNumber {change}), tags {tags}"))
        return lists
