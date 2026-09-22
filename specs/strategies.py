"""
Named strategies the spec sampler uses to set tool arguments (registry `pipeline.sample`) and per-episode
volatile values (config/specs.yaml `episode_config`). YAML says *which* strategy an argument uses; the code for
each strategy lives here, once. A new tool reuses these; a genuinely new kind of argument adds one function.

Every strategy takes a StepContext and returns the argument value; it may add fill requests (free text the
intent filler writes) and outcome variables (e.g. $created) to the context.
"""
from dataclasses import dataclass, field

FILL = "<fill>"


@dataclass
class StepContext:
    rng: object
    j: int                        # step index (1-based) in the path
    step: dict                    # catalog step {kind, in, out}
    path: dict                    # catalog path
    counts: dict                  # handle -> count
    config: dict                  # this episode's volatile values (collage_max, effects, ...)
    arg: str = None               # the argument being set
    arg_spec: dict = None         # its model_facing spec
    fill: list = field(default_factory=list)
    vars: dict = field(default_factory=dict)   # outcome template variables


def handle(ctx):
    """The photo set this step consumes (the newest handle, fixed by the catalog path)."""
    return ctx.step["in"]


def episode_value(ctx):
    """One of this episode's volatile values for an enum argument ("{effects}" -> config["effects"])."""
    key = ctx.arg_spec["values"].strip("{}")
    return ctx.rng.choice(ctx.config[key])


def question_fill(ctx, types=None):
    """A question the intent filler writes, of a sampled type; the answer is written too ($fill)."""
    from specs.spec_sampler import CFG
    before_action = ctx.j < len(ctx.path["steps"])
    qtype = ctx.rng.choice(CFG["ask_types"]["before_action" if before_action else "standalone"])
    ctx.fill.append({"field": "question", "step": ctx.j, "type": qtype, "count": ctx.counts[ctx.step["out"]],
                     "before_action": before_action})
    return FILL


def album_fill(ctx):
    """An album the intent filler names; whether it already exists is sampled here ($created)."""
    from specs.spec_sampler import CFG
    exists = ctx.rng.random() < CFG["album_exists"]     # the filler picks which album fits the story
    ctx.fill.append({"field": "album", "step": ctx.j, "exists": exists})
    ctx.vars["created"] = not exists
    return FILL


ARG_STRATEGIES = {"handle": handle, "episode_value": episode_value, "question_fill": question_fill,
                  "album_fill": album_fill}


# ---------------------------------------------------------------- per-episode volatile values
def episode_config(rng, cfg, tools):
    """Sample the volatile values named in config/specs.yaml `episode_config`, in that order."""
    out = {}
    for key, how in cfg.items():
        c = tools[how["tool"]].constraints[how["constraint"]]
        if "choose_from" in how:
            out[key] = rng.choice(c[how["choose_from"]])
        else:
            out[key] = rng.sample(c["values"], rng.randint(*how["sample_size"]))
    return out


def render(template, values):
    """Fill a spec-outcome template ("$count", "$in_count", "$fill", "$created", "$arg.<name>")."""
    if isinstance(template, dict):
        return {k: render(v, values) for k, v in template.items()}
    if isinstance(template, str) and template.startswith("$"):
        return values[template[1:]]
    return template
