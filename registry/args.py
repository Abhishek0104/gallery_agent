"""
Argument types (config/arg_types.yaml) and the normalizers the verifier and user simulator share.

    ARG_TYPES           name -> type spec
    SLOTS               search filter slots, in slot-pattern order
    normalized(arg, v)  a value normalized for exact comparison by the arg's `compare.normalize` ops
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_FILE = yaml.safe_load((ROOT / "config" / "arg_types.yaml").read_text())
SAY_ORDER = _FILE.pop("say_order")
ARG_TYPES = _FILE
SLOTS = [name for name, t in ARG_TYPES.items() if t.get("slot")]
DATE_PREPOSITIONS = re.compile(r"^(back in|in|during|from|on|over|at)\s+", re.I)


@lru_cache(maxsize=None)
def _aliases():
    rel = yaml.safe_load((ROOT / "config" / "relations.yaml").read_text())
    return {a.lower(): canon for canon, aliases in rel.items() for a in aliases + [canon]}


def normalize_person(v):
    """Tool-wrapper normalization: relation alias -> canonical word; names and "me" pass through."""
    return _aliases().get(v.lower(), v)


def date_core(v):
    """A date phrase without its leading preposition: "in 2023" -> "2023", "during Eid" -> "Eid"."""
    return DATE_PREPOSITIONS.sub("", v.strip()) if isinstance(v, str) else v


OPS = {
    "strip": lambda v: v.strip(),
    "lower": lambda v: v.lower(),
    "strip_leading_the": lambda v: re.sub(r"^the\s+", "", v),
    "relation_alias": normalize_person,
    "date_core": lambda v: date_core(v) or "",
}


def exact_args():
    """Arguments compared exactly (after normalizing), in check order."""
    return [a for a, t in ARG_TYPES.items() if "normalize" in (t.get("compare") or {})]


def semantic_args():
    return [a for a, t in ARG_TYPES.items() if "semantic" in (t.get("compare") or {})]


def normalized(arg, v):
    c = ARG_TYPES[arg]["compare"]
    ops = [OPS[o] for o in c["normalize"]]
    if c.get("as") == "set":
        if not isinstance(v, list):
            return v.strip().lower() if isinstance(v, str) else v
        out = []
        for x in v:
            for op in ops:
                x = op(x)
            out.append(x)
        return sorted(out)
    if "date_core" in c["normalize"] and not isinstance(v, str):
        return ""
    if not isinstance(v, str):
        return v
    for op in ops:
        v = op(v)
    return v
