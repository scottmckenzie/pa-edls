import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pa_edl.providers import REGISTRY, ConfigError
from pa_edl.writer import DEFAULT_MIN_PREFIXLEN

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
IP_VERSIONS = ("ipv4", "ipv6")
_MAX_PREFIXLEN = {"ipv4": 32, "ipv6": 128}


@dataclass
class EdlSpec:
    provider: str
    cloud: str
    name: str
    ip_versions: tuple[str, ...]
    min_entries: int
    min_prefixlen: dict[str, int]  # shortest prefix allowed per IP version
    options: dict[str, Any]  # full table, passed to the provider

    @property
    def path(self) -> str:
        return f"{self.provider}/{self.cloud}/{self.name}"


@dataclass
class Config:
    output_dir: Path
    max_shrink: float
    max_growth: float
    edls: list[EdlSpec] = field(default_factory=list)


def load_config(path: Path) -> Config:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    output_dir = Path(raw.get("output_dir", "generated"))
    if not output_dir.is_absolute():
        output_dir = path.parent / output_dir

    max_shrink = float(raw.get("max_shrink", 0.5))
    if not (math.isfinite(max_shrink) and 0 <= max_shrink <= 1):
        raise ConfigError("max_shrink must be between 0 and 1")
    max_growth = float(raw.get("max_growth", 2.0))
    if not (math.isfinite(max_growth) and max_growth >= 1):
        raise ConfigError("max_growth must be a finite number of at least 1")

    specs: list[EdlSpec] = []
    seen: set[str] = set()
    for i, table in enumerate(raw.get("edl", [])):
        where = f"[[edl]] #{i + 1}"
        provider = table.get("provider")
        if provider not in REGISTRY:
            raise ConfigError(f"{where}: unknown provider {provider!r}; available: {sorted(REGISTRY)}")
        name = table.get("name")
        if not isinstance(name, str) or not _SAFE_NAME.match(name):
            raise ConfigError(f"{where}: 'name' must match {_SAFE_NAME.pattern}")
        cloud = table.get("cloud", "public")
        if not isinstance(cloud, str) or not _SAFE_NAME.match(cloud):
            raise ConfigError(f"{where}: 'cloud' must match {_SAFE_NAME.pattern}")
        versions = tuple(table.get("ip_versions", IP_VERSIONS))
        if not versions or any(v not in IP_VERSIONS for v in versions):
            raise ConfigError(f"{where}: ip_versions must be a non-empty subset of {list(IP_VERSIONS)}")
        min_prefixlen = {}
        for v in IP_VERSIONS:
            value = table.get(f"min_prefixlen_{v}", DEFAULT_MIN_PREFIXLEN[v])
            if not isinstance(value, int) or not 1 <= value <= _MAX_PREFIXLEN[v]:
                raise ConfigError(f"{where}: min_prefixlen_{v} must be an integer from 1 to {_MAX_PREFIXLEN[v]}")
            min_prefixlen[v] = value
        spec = EdlSpec(provider, cloud, name, versions, int(table.get("min_entries", 1)), min_prefixlen, dict(table))
        if spec.path in seen:
            raise ConfigError(f"{where}: duplicate EDL {spec.path}")
        seen.add(spec.path)
        specs.append(spec)

    if not specs:
        raise ConfigError(f"{path}: no [[edl]] entries defined")
    return Config(output_dir, max_shrink, max_growth, specs)
