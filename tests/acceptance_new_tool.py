"""
Acceptance flow for a tool added only in YAML (run by tests/test_new_tool.py in a subprocess, with
GALLERY_AGENT_REGISTRY = the real registry + tests/fixtures/favorite_images.yaml and GALLERY_AGENT_CATALOG = a
catalog regenerated from it). Every stage runs unmodified: catalog -> spec sampler -> intent-filler prompt ->
spec validator -> user-simulator request -> simulator -> verifier. Prints one JSON summary line.
"""
import json
import os
import random
import sys

import yaml

TOOL = "favorite_images"


def main():
    from catalog.generate_catalog import build_catalog
    catalog = build_catalog()
    with open(os.environ["GALLERY_AGENT_CATALOG"], "w") as f:
        yaml.safe_dump([{**{k: c[k] for k in ("id", "initial", "calls", "path")}, "weight": 1.0, "status": "keep",
                         "notes": "", "steps": c["steps"]} for c in catalog], f, sort_keys=False, allow_unicode=True)
    with_tool = [c for c in catalog if any(s["tool"] == TOOL for s in c["steps"])]

    import realize.run as run
    from realize.system_prompt import teacher_system_prompt, tool_declarations
    from realize.user_sim import turn_intent
    from specs.intent_filler import build_prompt, merge
    from specs.spec_sampler import load_catalog, load_personas, skeleton
    from specs.spec_validator import validate
    from tests.test_student_eval import ScriptedStudent
    from tests.test_verify import fake_embed
    from verify.verifier import verify
    import llm as llm_mod

    personas = {p["persona_id"]: p for p in load_personas()}
    paths = [c for c in load_catalog() if any(s["kind"] == "favorite" for s in c["steps"])]
    skels = skeleton(seed=1, paths=paths)
    decl = next(d for d in tool_declarations(skels[0]["config"]) if d["name"] == TOOL)

    accepted, requests, outcomes = 0, set(), set()
    for skel in skels:
        persona = personas[skel["persona"]]
        prompt = build_prompt(skel, persona)                     # the filler is told the tool's outcome
        assert "marked" in prompt and "as favorites" in prompt, prompt
        out = {"motivation": "m", "question": "Do I have photos of a sunset at the beach?", "answer": "Yes.",
               "album": next((persona["albums"][0] if f["exists"] else "Zz Test Album"
                              for f in skel["fill"] if f["field"] == "album"), None),
               "queries": [{"step": f["step"], "query": "sunset"} for f in skel["fill"] if f["field"] == "query"]}
        spec = merge(skel, out)
        problems = validate(spec, persona)
        assert problems == [], (spec["episode_id"], problems)
        step = next(s for s in spec["steps"] if s.get("call") == TOOL)
        outcomes.add(json.dumps(step["outcome"], sort_keys=True).replace(str(step["outcome"]["count"]), "N"))
        for turn in spec["turns"]:
            lines, _, _ = turn_intent(spec, turn, {"style": "casual", "people": {}, "effects": {}} | run.sample_surface(
                spec, persona, "casual", random.Random(0)))
            requests |= {l for l in lines if "favorites" in l}

        student = llm_mod.LLM("student")
        student._client = ScriptedStudent(spec)
        run.write_message = lambda *a, **k: ("please do it", {"served_model": "stub"}, 1)
        ep = run.realize(spec, persona, "casual", None, student, random.Random(0), guided=False)
        result = next(m["content"] for m in ep["messages"] if m["role"] == "tool" and m["name"] == TOOL)
        assert result["status"] == "favorited" and result["count"] == step["outcome"]["count"], result
        v = verify(ep, persona, fake_embed)
        assert v["accept"], (spec["episode_id"], v["failures"])
        accepted += 1

    print(json.dumps({"paths_with_tool": len(with_tool), "paths_total": len(catalog), "specs": len(skels),
                      "accepted": accepted, "declaration": decl, "requests": sorted(requests)[:3],
                      "outcomes": sorted(outcomes), "guidance_has_tool": TOOL in teacher_system_prompt()}))


if __name__ == "__main__":
    sys.exit(main())
