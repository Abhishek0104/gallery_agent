"""
Path catalog generator for the gallery agent.

Enumerates every valid tool-call path (up to MAX_CALLS tool calls) from the tool specs in the registry
(`pipeline.io`, `pipeline.effect`, `pipeline.catalog`), then applies the general plausibility rules below.
Adding a tool = a registry YAML; regenerate with `python catalog/generate_catalog.py`.

Handle kinds:
  set     - one or more images (search, ask_gallery, selection, effect output)
  single  - exactly one image (collage output)
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from registry import tools  # noqa: E402

MAX_CALLS = 4
SELECT = "select"                # user selection event (not a tool call)
INITIAL_STATES = ["empty", "selection"]
OUT = Path(__file__).resolve().parent / "path_catalog.yaml"


def enumerate_paths(initial):
    """Depth-first enumeration of step sequences with handle bookkeeping."""
    specs = tools()
    producers = [t for t in specs.values() if t.io.consumes is None]
    consumers = [t for t in specs.values() if t.io.consumes is not None]
    results = []

    def rec(steps, handles, calls):
        # handles: dict name -> {"kind", "alive", "src"}
        if steps:
            results.append(list(steps))
        if calls >= MAX_CALLS:
            return
        last = steps[-1] if steps else None
        first = specs.get(steps[0]["tool"]) if steps else None
        # a tool that caps the path when it opens it (ask_gallery: a question plus at most one action)
        if first and first.catalog.max_calls_when_first and calls >= first.catalog.max_calls_when_first:
            return
        # terminal tools (destructive ones, and e.g. move_to_album): nothing follows
        if last and last["tool"] in specs and specs[last["tool"]].terminal:
            return

        live = {h: v for h, v in handles.items() if v["alive"]}
        n = len([h for h in handles if h != "r0"])

        # --- tools that consume nothing (search, ask)
        for t in producers:
            if not ok_producer(t, steps, initial):
                continue
            h = f"r{n + 1}"
            steps.append({"tool": t.name, "in": None, "out": h})
            handles[h] = {"kind": t.io.produces, "alive": True, "src": t.name}
            rec(steps, handles, calls + 1)
            del handles[h]; steps.pop()

        # --- selection event
        for hin, v in live.items():
            if not ok_select(hin, v, steps):
                continue
            h = f"r{n + 1}"
            steps.append({"tool": SELECT, "in": hin, "out": h})
            handles[h] = {"kind": "set", "alive": True, "src": SELECT}
            rec(steps, handles, calls)          # not a tool call
            del handles[h]; steps.pop()

        # --- tools that consume a photo set
        for t in consumers:
            for hin, v in live.items():
                if not ok_consumer(t, hin, v, steps, initial):
                    continue
                out = None
                if t.io.produces != "none":
                    out = f"r{n + 1}"
                    handles[out] = {"kind": v["kind"] if t.io.produces == "same" else t.io.produces,
                                    "alive": True, "src": t.name}
                if t.destructive:
                    handles[hin]["alive"] = False
                steps.append({"tool": t.name, "in": hin, "out": out})
                rec(steps, handles, calls + 1)
                steps.pop()
                if t.destructive:
                    handles[hin]["alive"] = True
                if out:
                    del handles[out]

    handles = {}
    if initial == "selection":
        handles["r0"] = {"kind": "set", "alive": True, "src": "initial_selection"}
    rec([], handles, 0)
    return results


# ---------------------------------------------------------------- plausibility (general rules)
def ok_producer(t, steps, initial):
    # With an initial selection, the first action must use it.
    if initial == "selection" and not steps:
        return False
    if not steps:
        return t.catalog.start
    # later in a path only directly after an allowed tool (search refinement), within its per-path limit
    if steps[-1]["tool"] not in t.catalog.after:
        return False
    if t.catalog.max_per_path and sum(s["tool"] == t.name for s in steps) >= t.catalog.max_per_path:
        return False
    return True


def ok_select(hin, v, steps):
    if v["kind"] != "set":                 # can't select from a single collage image
        return False
    if steps and steps[-1]["tool"] == SELECT:
        return False                       # no consecutive selections
    if steps and steps[-1]["out"] != hin:  # select from what was just shown
        return False
    if not steps:                          # nothing shown yet
        return False
    if any(s["tool"] == SELECT for s in steps):
        return False                       # at most one selection per path (v0)
    return True


def ok_consumer(t, hin, v, steps, initial):
    # Only act on the most recent handle.
    newest = steps[-1]["out"] if steps and steps[-1]["out"] else (
        "r0" if initial == "selection" and not steps else None)
    if steps and steps[-1]["out"] is None:
        newest = steps[-1]["in"]           # last step produced nothing: its input is still the newest
    if hin != newest:
        return False
    if t.io.input and v["kind"] != t.io.input:
        return False                       # e.g. no collage of a collage (needs a multi-image set)
    # no repeating the same tool on the same handle (e.g. move twice)
    if any(s["tool"] == t.name and s["in"] == hin for s in steps):
        return False
    if t.catalog.max_per_path and sum(s["tool"] == t.name for s in steps) >= t.catalog.max_per_path:
        return False                       # e.g. at most one apply_effect per path
    return True


def filter_path(p, initial):
    if p[-1]["tool"] == SELECT:            # selection must lead somewhere
        return False
    if initial == "selection" and p[0]["in"] != "r0":
        return False
    return True


# ---------------------------------------------------------------- render
def render(p):
    short = {t.name: t.catalog.short for t in tools().values()} | {SELECT: "[select]"}
    parts = []
    for s in p:
        t = short[s["tool"]]
        if s["in"]:
            t += f"({s['in']})"
        if s["out"]:
            t += f"→{s['out']}"
        parts.append(t)
    return "  ▸  ".join(parts)


def build_catalog():
    """Every valid path, sorted and numbered (P001, ...)."""
    catalog = []
    for init in INITIAL_STATES:
        for p in enumerate_paths(init):
            if filter_path(p, init):
                calls = sum(s["tool"] != SELECT for s in p)
                catalog.append({"initial": init, "calls": calls,
                                "has_select": any(s["tool"] == SELECT for s in p),
                                "path": render(p), "steps": p})
    catalog.sort(key=lambda c: (c["initial"], c["calls"], c["path"]))
    for i, c in enumerate(catalog, 1):
        c["id"] = f"P{i:03d}"
    return catalog


if __name__ == "__main__":
    catalog = build_catalog()
    with open(OUT, "w") as f:
        yaml.safe_dump([{"id": c["id"], "initial": c["initial"], "calls": c["calls"],
                         "path": c["path"], "weight": 1.0, "status": "keep", "notes": "",
                         "steps": c["steps"]} for c in catalog], f, sort_keys=False, allow_unicode=True)
    print(f"{len(catalog)} paths -> {OUT}")
