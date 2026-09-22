"""
Registry loader. One YAML spec per v1.0 tool in registry/*.yaml; backlog tools live in registry/v2/ and are
never loaded. The registry is the single source of truth for tool behavior: stages read these specs instead of
naming tools.

    tools()            -> {name: ToolSpec} (typed view)
    load_registry()    -> {name: raw dict}
Set GALLERY_AGENT_REGISTRY to a directory to load another registry (tests use a fixture registry).
"""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

REGISTRY = Path(__file__).resolve().parent


def registry_dir():
    return Path(os.environ.get("GALLERY_AGENT_REGISTRY") or REGISTRY)


@dataclass(frozen=True)
class IO:
    consumes: str = None          # arg holding the input handle; None = produces photos from nothing
    input: str = None             # required input kind ("set" = a multi-image set); None = any
    produces: str = "none"        # set | single | same (kind of the input) | none
    count: str = "outcome"        # outcome | outcome_or_input | one


@dataclass(frozen=True)
class Catalog:
    short: str                    # name in catalog path strings ("search", "effect", ...)
    start: bool = False           # may open a conversation (tools that consume nothing)
    after: tuple = ()             # tools that may directly precede it (for tools that consume nothing)
    max_per_path: int = None
    max_calls_when_first: int = None
    terminal: bool = False        # nothing follows it (destructive tools are always terminal)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    model_facing: dict
    effect: str                   # read | write | destructive
    io: IO
    catalog: Catalog
    output: dict                  # {result: template, default_outcome: {...}}
    outcomes: dict = field(default_factory=dict)      # alternative outcomes: {name: {when, result, ...}}
    constraints: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    conversation: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def destructive(self):
        return self.effect == "destructive"

    @property
    def terminal(self):
        return self.catalog.terminal or self.destructive

    @property
    def count_constraint(self):
        """The constraint on the input set's size (e.g. collage: 2..collage_max), if any."""
        return self.constraints.get(f"{self.io.consumes}.count") if self.io.consumes else None

    def match_outcome(self, outcome):
        """The alternative outcome whose `when` fields match the spec outcome, or None (happy path)."""
        for name, o in self.outcomes.items():
            if all(outcome.get(k) == v for k, v in o["when"].items()):
                return name, o
        return None, None


def to_spec(raw):
    p = raw["pipeline"]
    cat = dict(p["catalog"])
    cat["after"] = tuple(cat.get("after", ()))
    return ToolSpec(name=raw["name"], model_facing=raw["model_facing"], effect=p["effect"], io=IO(**p.get("io", {})),
                    catalog=Catalog(**cat), output=p.get("output", {}), outcomes=p.get("outcomes", {}),
                    constraints=p.get("constraints", {}), errors=p.get("errors", {}),
                    conversation=p.get("conversation", {}), raw=raw)


@lru_cache(maxsize=None)
def _load(path):
    tools = {}
    for f in sorted(Path(path).glob("*.yaml")):
        raw = yaml.safe_load(f.read_text())
        tools[raw["name"]] = raw
    return tools


def load_registry():
    """name -> raw spec dict, for every v1.0 tool."""
    return _load(str(registry_dir()))


def tools():
    """name -> ToolSpec."""
    return {name: to_spec(raw) for name, raw in load_registry().items()}


def by_short():
    return {t.catalog.short: t for t in tools().values()}


def model_facing(name):
    return load_registry()[name]["model_facing"]

