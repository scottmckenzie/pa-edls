"""Provider interface and registry.

A provider turns one [[edl]] spec from the config into one or more named lists
of IP prefixes. Normalisation (validation, de-duplication, collapsing, sorting,
IP version filtering) and file writing are handled centrally, so a provider only
needs to know how to read its upstream feed.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from pa_edl.fetch import Fetcher


class ConfigError(ValueError):
    pass


@dataclass
class EdlList:
    name: str  # file stem, e.g. "eastus" -> eastus.txt
    prefixes: list[str]  # raw prefixes; normalised by the writer
    source: str  # human-readable provenance, recorded in the manifest
    change: str | None = None  # upstream version of this list (e.g. Azure tag changeNumber), shown in READMEs


class Provider(ABC):
    id: ClassVar[str]
    description: ClassVar[str]

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    @abstractmethod
    def build(self, spec: dict[str, Any]) -> list[EdlList]:
        """Return the lists for one [[edl]] spec. Raise ConfigError on bad specs."""


REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    if cls.id in REGISTRY:
        raise RuntimeError(f"duplicate provider id: {cls.id}")
    REGISTRY[cls.id] = cls
    return cls
