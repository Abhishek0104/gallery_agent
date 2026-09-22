"""
Path catalog generator for the gallery agent.

Enumerates every valid tool-call path (up to MAX_CALLS tool calls) from type rules,
then applies plausibility rules. Output is meant for human review.

Handle kinds:
  set     - one or more images (search, ask_gallery, selection, effect output)
  single  - exactly one image (collage output)
"""
import itertools, json, yaml

MAX_CALLS = 4

# ---------------------------------------------------------------- step types
# producer tools (need no handle)
PRODUCERS = {
    "search_images": "set",
    "ask_gallery":   "set",
}
# consumer tools: tool -> output kind (None = no new handle)
CONSUMERS = {
    "apply_effect":  "same",     # same kind as input, new handle
    "make_collage":  "single",
    "move_to_album": None,
    "delete_images": None,       # kills its input handle
}
SELECT = "select"                # user selection event (not a tool call)

INITIAL_STATES = ["empty", "selection"]


def enumerate_paths(initial):
    """Depth-first enumeration of step sequences with handle bookkeeping."""
    results = []

    def rec(steps, handles, calls):
        # handles: dict name -> {"kind", "alive", "src"}
        if steps:
            results.append(list(steps))
        if calls >= MAX_CALLS:
            return
        last = steps[-1] if steps else None

        # --- rule: ask_gallery chains stay short (question + at most one action)
        if steps and steps[0]["tool"] == "ask_gallery" and calls >= 2:
            return
        # --- rule: delete is terminal
        if last and last["tool"] == "delete_images":
            return

        live = {h: v for h, v in handles.items() if v["alive"]}
        n = len([h for h in handles if h != "r0"])

        # --- producers
        for tool, kind in PRODUCERS.items():
            if not ok_producer(tool, steps, initial):
                continue
            h = f"r{n + 1}"
            steps.append({"tool": tool, "in": None, "out": h})
            handles[h] = {"kind": kind, "alive": True, "src": tool}
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

        # --- consumers
        for tool, outk in CONSUMERS.items():
            for hin, v in live.items():
                if not ok_consumer(tool, hin, v, steps, initial):
                    continue
                out = None
                if outk is not None:
                    out = f"r{n + 1}"
                    handles[out] = {"kind": v["kind"] if outk == "same" else outk,
                                    "alive": True, "src": tool}
                if tool == "delete_images":
                    handles[hin]["alive"] = False
                steps.append({"tool": tool, "in": hin, "out": out})
                rec(steps, handles, calls + 1)
                steps.pop()
                if tool == "delete_images":
                    handles[hin]["alive"] = True
                if out:
                    del handles[out]

    handles = {}
    if initial == "selection":
        handles["r0"] = {"kind": "set", "alive": True, "src": "initial_selection"}
    rec([], handles, 0)
    return results


# ---------------------------------------------------------------- plausibility
def ok_producer(tool, steps, initial):
    # With an initial selection, the first action must use it.
    if initial == "selection" and not steps:
        return False
    # ask_gallery only as the opening step (a question starts the conversation).
    if tool == "ask_gallery" and steps:
        return False
    # search refinement: only directly after a search (merged args), max once.
    if tool == "search_images" and steps:
        if steps[-1]["tool"] != "search_images":
            return False
        if sum(s["tool"] == "search_images" for s in steps) >= 2:
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


def ok_consumer(tool, hin, v, steps, initial):
    # Only act on the most recent handle, or (for delete) on the source of an effect
    # ("I don't like it, delete the originals" is rare; default is the newest).
    newest = steps[-1]["out"] if steps and steps[-1]["out"] else (
        "r0" if initial == "selection" and not steps else None)
    if steps and steps[-1]["out"] is None:
        # last step produced nothing (move): allow acting on its input again
        newest = steps[-1]["in"]
    if hin != newest:
        return False
    if tool == "make_collage" and v["kind"] != "set":
        return False                       # no collage of a collage
    # no repeating the same tool on the same handle (e.g. move twice)
    if any(s["tool"] == tool and s["in"] == hin for s in steps):
        return False
    # effect only once per chain unless it's a correction; keep chains simple for v0
    if tool == "apply_effect" and any(s["tool"] == "apply_effect" for s in steps):
        return False
    # after a move, only a delete of the same handle makes little sense; block move -> *
    if steps and steps[-1]["tool"] == "move_to_album":
        return False
    # a trailing selection must be followed by a consumer: handled in filter()
    return True


def filter_path(p, initial):
    if p[-1]["tool"] == SELECT:            # selection must lead somewhere
        return False
    if initial == "selection" and p[0]["in"] != "r0":
        return False
    return True


# ---------------------------------------------------------------- render
SHORT = {"search_images": "search", "ask_gallery": "ask", "apply_effect": "effect",
         "make_collage": "collage", "move_to_album": "move", "delete_images": "delete",
         SELECT: "[select]"}

def render(p):
    parts = []
    for s in p:
        t = SHORT[s["tool"]]
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
    with open("path_catalog.yaml", "w") as f:
        yaml.safe_dump([{"id": c["id"], "initial": c["initial"], "calls": c["calls"],
                         "path": c["path"], "weight": 1.0, "status": "keep",
                         "notes": ""} for c in catalog], f, sort_keys=False, allow_unicode=True)
    print(len(catalog))
    for c in catalog:
        print(c["id"], c["initial"], c["calls"], c["path"])