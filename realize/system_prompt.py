"""
On-device system prompt + tool declarations, generated from the registry (model_facing only).

Volatile values are filled per episode: the collage limits in make_collage's description and this
episode's effect list (and order) as apply_effect's enum. The teacher gets the same prompt plus the
teacher guidance block, which is never exported.
"""
from pathlib import Path

from registry import collage_bounds, load_registry

ROOT = Path(__file__).resolve().parent.parent
GUIDANCE = (ROOT / "realize" / "prompts" / "teacher_guidance.txt").read_text().strip()
GUIDANCE_VERSION = 4
GUIDANCE_START, GUIDANCE_END = "<teacher_guidance>", "</teacher_guidance>"

SYSTEM_PROMPT_V0 = (
    "You are the photo gallery assistant on this phone. Use the tools to find and act on the user's photos. "
    "Photo sets are referred to by ids like r1. When the user selects photos in the app, you see a line like "
    "[user selected 5 photos → r3]."
)

TYPE = {
    "str": {"type": "string"},
    "list[str]": {"type": "array", "items": {"type": "string"}},
    "ImageSet": {"type": "string", "description": "photo set id, e.g. r1"},
}


def tool_declarations(config):
    """Neutral declarations [{name, description, parameters}] for one episode's config."""
    cmin, _ = collage_bounds()
    decls = []
    for name, spec in load_registry().items():
        mf = spec["model_facing"]
        desc = mf["description"].replace("{min}", str(cmin)).replace("{max}", str(config["collage_max"]))
        props, required = {}, []
        for arg, a in mf["args"].items():
            if a["type"] == "enum":
                props[arg] = {"type": "string", "enum": list(config["effects"])}
            else:
                props[arg] = dict(TYPE[a["type"]])
            if a.get("required"):
                required.append(arg)
        decls.append({"name": name, "description": desc,
                      "parameters": {"type": "object", "properties": props, "required": required}})
    return decls


def system_prompt():
    return SYSTEM_PROMPT_V0


def teacher_system_prompt():
    return f"{SYSTEM_PROMPT_V0}\n\n{GUIDANCE_START}\n{GUIDANCE}\n{GUIDANCE_END}"
