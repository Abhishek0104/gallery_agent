"""
Registry loader. One YAML spec per v1.0 tool in registry/*.yaml; backlog tools live in registry/v2/
and are never loaded here.
"""
from functools import lru_cache
from pathlib import Path

import yaml

REGISTRY = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_registry():
    """name -> spec dict, for every v1.0 tool."""
    tools = {}
    for f in sorted(REGISTRY.glob("*.yaml")):
        spec = yaml.safe_load(f.read_text())
        tools[spec["name"]] = spec
    return tools


def model_facing(name):
    return load_registry()[name]["model_facing"]


def collage_bounds():
    c = load_registry()["make_collage"]["pipeline"]["constraints"]["images.count"]
    return c["min"], c["randomize_max"]


def effect_values():
    return list(load_registry()["apply_effect"]["pipeline"]["constraints"]["effect"]["values"])
