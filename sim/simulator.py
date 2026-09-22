"""
Lightweight simulator (docs/simulator_design.md): a handle ledger whose tool outputs come from the spec.

The teacher's calls are matched in order against the spec's planned calls. A matched call returns the
spec outcome; handles are allocated here in call order and spec handles are mapped onto them. An
unplanned call gets a default outcome and flags the episode. Pure function of (spec, calls so far).
"""

DEFAULTS = {                     # unplanned calls only; the episode is flagged
    "search_images": {"count": 5},
    "ask_gallery": {"answer": "I couldn't find anything about that.", "count": 1},
}


class Simulator:
    def __init__(self, spec):
        self.spec = spec
        self.planned = [s for s in spec["steps"] if "call" in s and not s.get("skipped")]
        self.cmax = spec["config"]["collage_max"]
        self.pos = 0
        self.n = 0
        self.ledger = {}         # actual handle -> {"count", "alive", "src"}
        self.map = {}            # spec handle -> actual handle
        self.flags = []
        if spec["initial_selection"]:
            self.ledger["r0"] = {"count": spec["initial_selection"]["count"], "alive": True, "src": "selection"}
            self.map["r0"] = "r0"

    # ------------------------------------------------------------------ app events
    def _new(self, count, src):
        self.n += 1
        h = f"r{self.n}"
        self.ledger[h] = {"count": count, "alive": True, "src": src}
        return h

    def select(self, step):
        """Selection event from the spec -> the line the app injects."""
        h = self._new(step["count"], "selection")
        self.map[step["out"]] = h
        return f"[user selected {step['count']} photo{'s' * (step['count'] != 1)} → {h}]"

    def initial_line(self):
        c = self.spec["initial_selection"]
        return f"[user selected {c['count']} photo{'s' * (c['count'] != 1)} → r0]" if c else None

    # ------------------------------------------------------------------ tool calls
    def expected(self):
        return self.planned[self.pos] if self.pos < len(self.planned) else None

    def call(self, name, args):
        """Model-facing result for one tool call."""
        exp = self.expected()
        src = args.get("images")
        if name == "make_collage" and src in self.ledger and not 2 <= self.ledger[src]["count"] <= self.cmax:
            self.flags.append("collage_outside_limits")          # backend safety net; the plan is not advanced
            return {"error": "too_many_images", "max": self.cmax}
        planned = exp is not None and exp["call"] == name
        if planned:
            self.pos += 1
            out = exp["outcome"]
        else:
            self.flags.append(f"unplanned_call:{name}")
            out = DEFAULTS.get(name, {})
        if src is not None and (src not in self.ledger or not self.ledger[src]["alive"]):
            self.flags.append(f"bad_handle:{name}:{src}")
        src_count = self.ledger.get(src, {}).get("count", 1)

        def produce(count, kind):
            h = self._new(count, kind)
            if planned and exp.get("out"):
                self.map[exp["out"]] = h
            return h

        if name == "search_images":
            if out.get("error") == "no_results":
                return {"error": "no_results"}                   # no handle is created
            return {"id": produce(out["count"], "search"), "count": out["count"]}
        if name == "ask_gallery":
            return {"answer": out["answer"], "id": produce(out["count"], "ask"), "count": out["count"]}
        if name == "apply_effect":
            return {"status": "created", "images": produce(out.get("count", src_count), "effect")}
        if name == "make_collage":
            return {"status": "created", "collage": produce(1, "collage")}
        if name == "move_to_album":
            return {"status": "moved", "count": out.get("count", src_count), "album": args.get("album"),
                    "created": out.get("created", True)}
        if name == "delete_images":
            if out.get("status") == "cancelled":
                return {"status": "cancelled", "count": 0}      # the user cancelled the app's dialog
            if src in self.ledger:
                self.ledger[src]["alive"] = False
            return {"status": "deleted", "count": out.get("count", src_count)}
        self.flags.append(f"unknown_tool:{name}")
        return {"error": "unknown tool"}

    def done(self):
        return self.pos == len(self.planned)
