"""
Lightweight simulator (docs/simulator_design.md): a handle ledger whose tool outputs come from the spec.

The teacher's calls are matched in order against the spec's planned calls. A matched call returns the
spec outcome; handles are allocated here in call order and spec handles are mapped onto them. An
unplanned call gets the tool's default outcome and flags the episode. Pure function of (spec, calls so far).

Tool behavior comes from the registry (`pipeline.io`, `output`, `outcomes`, `errors`, `constraints`); nothing
here names a tool.
"""
from registry import tools


def render(template, values):
    """Fill a result template: "$out", "$count", "$answer", "$created", "$max", "$arg.<name>"."""
    if isinstance(template, dict):
        return {k: render(v, values) for k, v in template.items()}
    if isinstance(template, str) and template.startswith("$"):
        return values[template[1:]]
    return template


class Simulator:
    def __init__(self, spec):
        self.spec = spec
        self.tools = tools()
        self.planned = [s for s in spec["steps"] if "call" in s and not s.get("skipped")]
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
        tool = self.tools.get(name)
        exp = self.expected()
        src = args.get(tool.io.consumes if tool and tool.io.consumes else "images")
        limit = tool.count_constraint if tool else None
        if limit and src in self.ledger:                       # backend safety net; the plan is not advanced
            hi = self.spec["config"][limit["config_key"]]
            if not limit["min"] <= self.ledger[src]["count"] <= hi:
                err = tool.errors[limit["reject"]]
                self.flags.append(err["flag"])
                return render(err["result"], {"max": hi})
        planned = exp is not None and exp["call"] == name
        if planned:
            self.pos += 1
            out = exp["outcome"]
        else:
            self.flags.append(f"unplanned_call:{name}")
            out = tool.output.get("default_outcome", {}) if tool else {}
        if src is not None and (src not in self.ledger or not self.ledger[src]["alive"]):
            self.flags.append(f"bad_handle:{name}:{src}")
        if tool is None:
            self.flags.append(f"unknown_tool:{name}")
            return {"error": "unknown tool"}

        src_count = self.ledger.get(src, {}).get("count", 1)
        count = {"outcome": lambda: out["count"], "outcome_or_input": lambda: out.get("count", src_count),
                 "one": lambda: 1}[tool.io.count]
        _, variant = tool.match_outcome(out)
        if variant:                                            # e.g. no_results, cancelled
            if tool.destructive and not variant.get("keeps_input") and src in self.ledger:
                self.ledger[src]["alive"] = False
            return render(variant["result"], {"count": out.get("count", src_count)})
        values = {"count": None, "answer": out.get("answer"), "created": out.get("created", True),
                  **{f"arg.{k}": v for k, v in args.items()}}
        if tool.io.produces != "none":
            values["count"] = count()
            h = self._new(values["count"], tool.catalog.short)
            if planned and exp.get("out"):
                self.map[exp["out"]] = h
            values["out"] = h
        else:
            values["count"] = count()
        if tool.destructive and src in self.ledger:
            self.ledger[src]["alive"] = False
        return render(tool.output["result"], values)

    def done(self):
        return self.pos == len(self.planned)
