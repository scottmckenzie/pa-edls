# Import provider modules so they register themselves.
from pa_edl.providers import azure  # noqa: F401
from pa_edl.providers.base import REGISTRY, ConfigError, EdlList, Provider, register

__all__ = ["REGISTRY", "ConfigError", "EdlList", "Provider", "register"]
