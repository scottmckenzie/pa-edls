import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pa_edl.providers import REGISTRY, ConfigError

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
IP_VERSIONS = ("ipv4", "ipv6")


@dataclass
class EdlSpec:
    provider: str
    cloud: str
    name: str
    ip_versions: tuple[str, ...]
    min_entries: int
    options: dict[str, Any]  # full table, passed to the provider

    @property
    def path(self) -> str:
        return f"{self.provider}/{self.cloud}/{self.name}"


@dataclass
class Config:
    output_dir: Path
    max_shrink: float
    edls: list[EdlSpec] = field(default_factory=list)


def load_config(path: Path) -> Config:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    output_dir = Path(raw.get("output_dir", "edls"))
    if not output_dir.is_absolute():
        output_dir = path.parent / output_dir

    max_shrink = float(raw.get("max_shrink", 0.5))
    if not 0 <= max_shrink <= 1:
        raise ConfigError("max_shrink must be between 0 and 1")

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
        spec = EdlSpec(provider, cloud, name, versions, int(table.get("min_entries", 1)), dict(table))
        if spec.path in seen:
            raise ConfigError(f"{where}: duplicate EDL {spec.path}")
        seen.add(spec.path)
        specs.append(spec)

    if not specs:
        raise ConfigError(f"{path}: no [[edl]] entries defined")
    return Config(output_dir, max_shrink, specs)
