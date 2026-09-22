"""
Realize episodes: spec -> conversation (user simulator + teacher against the simulator).

    python -m realize.run            # realize n_episodes specs -> data/episodes/episodes_v0.jsonl
    python -m realize.run --review   # rewrite data/episodes/review_v0.md from saved episodes

Episodes run in parallel; each is a pure function of (spec, cached LLM calls), so reruns replay exactly.
"""
import argparse
import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llm import LLM, BadOutputError
from realize.system_prompt import GUIDANCE_VERSION, system_prompt, teacher_system_prompt, tool_declarations
from realize.user_sim import CFG, UserSimError, sample_surface, write_message
from sim.simulator import Simulator
from specs.persona_outline import quota_list
from registry.args import date_core, normalize_person

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "episodes"
WORKERS = 4
FEATURES = {
    "initial_selection": lambda s: s["initial_selection"] is not None,
    "ask": lambda s: any(x.get("call") == "ask_gallery" for x in s["steps"]),
    "refinement": lambda s: any("refines" in x for x in s["steps"]),
    "selection_event": lambda s: any("event" in x for x in s["steps"]),
    "delete": lambda s: any(x.get("call") == "delete_images" for x in s["steps"]),
    "move": lambda s: any(x.get("call") == "move_to_album" for x in s["steps"]),
    "collage": lambda s: any(x.get("call") == "make_collage" for x in s["steps"]),
    "one_message_multi": lambda s: any(len(t) > 1 for t in s["turns"]),
}


def pick_specs(specs, n, rng, per_feature=2):
    """At least `per_feature` specs with each feature, the rest random; original order kept."""
    chosen = []
    pool = specs[:]
    rng.shuffle(pool)
    for f in FEATURES.values():
        for s in [s for s in pool if f(s) and s not in chosen][:max(0, per_feature - sum(map(f, chosen)))]:
            chosen.append(s)
    chosen += [s for s in pool if s not in chosen][:max(0, n - len(chosen))]
    return sorted(chosen[:n], key=lambda s: s["episode_id"])


# ---------------------------------------------------------------- one episode
def history_text(messages):
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append(f"You: {m['content']}")
        elif m["role"] == "assistant" and m.get("content"):
            out.append(f"Assistant: {m['content']}")
    return "\n".join(out)


def run_teacher(teacher, sim, sys_prompt, tools, contents, messages, seed_prefix, flags):
    """Teacher turns until a text reply without tool calls (or the call cap)."""
    for k in range(CFG["teacher"]["max_calls_per_turn"]):
        res = teacher.chat(sys_prompt, contents, tools, seed=f"{seed_prefix}/c{k}")
        contents.append(res.content)
        calls = res.calls
        messages.append({"role": "assistant", "content": res.text or None,
                         "tool_calls": [{"name": n, "args": a} for n, a, _ in calls] or None})
        if not calls:
            return res.meta
        results = []
        for name, args, call_id in calls:
            result = sim.call(name, args)
            messages.append({"role": "tool", "name": name, "content": result})
            results.append((name, call_id, result))
        contents.extend(teacher.tool_results(results))
    flags.append("teacher_call_cap")
    return res.meta


def realize(spec, persona, style, user_llm, teacher, rng, guided=True):
    """guided: the assistant gets the teacher guidance block (data generation). A trained model under eval
    gets only the on-device system prompt, exactly as in its training data."""
    surface = sample_surface(spec, persona, style, rng)
    sim = Simulator(spec)
    tools = tool_declarations(spec["config"])
    sys_teacher = teacher_system_prompt() if guided else system_prompt()
    by_i = {s["i"]: s for s in spec["steps"]}
    contents, messages, flags, gen = [], [], sim.flags, {"user_sim": [], "teacher": []}
    planned_before = 0
    for t_no, turn in enumerate(spec["turns"], 1):
        events = [sim.initial_line()] if t_no == 1 and sim.initial_line() else []
        events += [sim.select(by_i[i]) for i in turn if "event" in by_i[i]]
        msg, meta, attempts = write_message(user_llm, spec, persona, t_no, turn, surface, history_text(messages))
        gen["user_sim"].append({**meta, "attempts": attempts})
        text = "\n".join(events + [msg])
        messages.append({"role": "user", "content": text})
        contents.append(teacher.user_message(text))
        gen["teacher"].append(run_teacher(teacher, sim, sys_teacher, tools, contents, messages,
                                          f"{spec['episode_id']}/t{t_no}", flags))
        planned_before += sum(1 for i in turn if "call" in by_i[i] and not by_i[i].get("skipped"))
        last = next((m for m in reversed(messages) if m["role"] == "assistant"), {})
        if sim.pos < planned_before and "?" in (last.get("content") or ""):
            # the teacher asked something the spec did not plan: answer from the intent, once
            flags.append("off_script")
            msg, meta, attempts = write_message(
                user_llm, spec, persona, f"{t_no}b", turn, surface, history_text(messages),
                extra="The assistant just asked you something. Answer it briefly and restate what you want.")
            gen["user_sim"].append({**meta, "attempts": attempts})
            messages.append({"role": "user", "content": msg})
            contents.append(teacher.user_message(msg))
            gen["teacher"].append(run_teacher(teacher, sim, sys_teacher, tools, contents, messages,
                                              f"{spec['episode_id']}/t{t_no}b", flags))
        if sim.pos != planned_before:
            flags.append(f"turn_{t_no}_calls:{sim.pos}/{planned_before}")
            planned_before = sim.pos           # keep going; the verifier scores the damage
    if not sim.done():
        flags.append("incomplete")
    return {
        "episode_version": 1, "episode_id": spec["episode_id"], "spec": spec,
        "system_prompt": system_prompt(), "tools": tools,
        "teacher_guidance_version": GUIDANCE_VERSION if guided else None, "assistant_role": teacher.role,
        "surface": surface, "messages": messages, "flags": sorted(set(flags)), "generation": gen,
    }


# ---------------------------------------------------------------- quick report (not the verifier)
def compare(ep):
    """Planned vs actual calls: tool names in order, and non-query args after normalization."""
    planned = [s for s in ep["spec"]["steps"] if "call" in s and not s.get("skipped")]
    actual = [c for m in ep["messages"] if m["role"] == "assistant" for c in (m["tool_calls"] or [])]
    rows = []
    for k in range(max(len(planned), len(actual))):
        p = planned[k] if k < len(planned) else None
        a = actual[k] if k < len(actual) else None
        diffs = []
        if p and a and p["call"] == a["name"]:
            for key in set(p["args"]) | set(a["args"]):
                if key in ("images", "query", "question"):     # handles / free text: judged elsewhere
                    continue
                pv, av = p["args"].get(key), a["args"].get(key)
                if key == "people" and isinstance(av, list) and isinstance(pv, list):
                    pv, av = sorted(map(normalize_person, pv)), sorted(map(normalize_person, av))
                if key == "date":
                    pv, av = date_core(pv), date_core(av)
                if pv != av:
                    diffs.append(f"{key}: spec={pv!r} teacher={av!r}")
        rows.append({"planned": p and p["call"], "actual": a and a["name"],
                     "match": bool(p and a and p["call"] == a["name"] and not diffs), "diffs": diffs,
                     "query": (p or {}).get("args", {}).get("query"), "teacher_query": (a or {}).get("args", {}).get("query")})
    return rows


def write_review(episodes, tag="v0"):
    lines = ["# Episodes v0 — review", ""]
    for ep in episodes:
        sp = ep["spec"]
        rows = compare(ep)
        ok = sum(r["match"] for r in rows)
        lines += [f"## {ep['episode_id']} · {sp['path']} · {sp['persona']} · style {ep['surface']['style']} · "
                  f"calls matched {ok}/{len(rows)}" + (f" · flags {ep['flags']}" if ep["flags"] else ""),
                  f"*{sp['motivation']}*", ""]
        for m in ep["messages"]:
            if m["role"] == "user":
                lines.append(f"**User:** {m['content']}".replace("\n", "  \n"))
            elif m["role"] == "assistant":
                for c in m["tool_calls"] or []:
                    lines.append(f"- `{c['name']}({json.dumps(c['args'], ensure_ascii=False)})`")
                if m["content"]:
                    lines.append(f"**Assistant:** {m['content']}")
            else:
                lines.append(f"  - → `{json.dumps(m['content'], ensure_ascii=False)}`")
        bad = [r for r in rows if not r["match"]]
        for r in bad:
            lines.append(f"- ⚠️ planned {r['planned']} / actual {r['actual']} {'; '.join(r['diffs'])}")
        queries = [(r["query"], r["teacher_query"]) for r in rows if r["query"] or r["teacher_query"]]
        if queries:
            lines.append("- queries (spec → teacher): " + "; ".join(f"{q!r} → {t!r}" for q, t in queries))
        lines.append("")
    (OUT / f"review_{tag}.md").write_text("\n".join(lines))


# ---------------------------------------------------------------- main
def main(tag="v0", limit=None, only=None, all_specs=False, assistant_role="teacher", out_tag=None):
    """Realize specs_{tag} -> episodes_{tag}. `only`: re-realize these episode ids in place.
    `assistant_role` != "teacher" (a trained model under eval): no guidance block; with `out_tag` the episodes go
    to episodes_{out_tag}.jsonl on their own (no splice), ready for `verify.run --tag {out_tag}`."""
    specs = [json.loads(l) for l in (ROOT / "data" / "specs" / f"specs_{tag}.jsonl").read_text().splitlines()]
    out_tag = out_tag or tag
    episodes_path = OUT / f"episodes_{out_tag}.jsonl"
    guided = assistant_role == "teacher"
    personas = {json.loads(f.read_text())["persona_id"]: json.loads(f.read_text())
                for f in (ROOT / "data" / "personas").glob("persona_*.json")}
    rng = random.Random(CFG["seed"])
    chosen = specs if all_specs else pick_specs(specs, CFG["n_episodes"], rng)
    styles = quota_list(CFG["style"], len(chosen), rng)
    seeds = [rng.randrange(2 ** 31) for _ in chosen]
    todo = [k for k, s in enumerate(chosen) if not only or s["episode_id"] in only][:limit]
    user_llm, teacher = LLM("user_sim"), LLM(assistant_role)

    def one(k):
        try:
            return realize(chosen[k], personas[chosen[k]["persona"]], styles[k], user_llm, teacher,
                           random.Random(seeds[k]), guided)
        except (UserSimError, BadOutputError) as e:       # bad output: drop this episode, keep the batch
            return {"episode_id": chosen[k]["episode_id"], "realize_failed": f"{type(e).__name__}: {e}"}  # API errors raise

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, todo))
    failed = [r for r in results if "realize_failed" in r]
    episodes = [r for r in results if "realize_failed" not in r]
    for r in failed:
        print(f"{r['episode_id']}: realization failed: {r['realize_failed']}")
    OUT.mkdir(parents=True, exist_ok=True)
    if only and out_tag == tag:                # splice the re-realized episodes into the saved batch
        new = {e["episode_id"]: e for e in episodes}
        saved = [json.loads(l) for l in episodes_path.read_text().splitlines()]
        episodes = [new.get(e["episode_id"], e) for e in saved]
    (OUT / f"_realize_failed_{out_tag}.json").write_text(json.dumps(failed, indent=2))
    episodes_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in episodes))
    write_review(episodes, out_tag)
    matched = [sum(r["match"] for r in compare(e)) == len(compare(e)) for e in episodes]
    print(f"realized {len(episodes)} ({len(failed)} failed); all calls match the spec in {sum(matched)}; "
          f"flags: {dict(Counter(f for e in episodes for f in e['flags']))}")
    print("served:", dict(Counter(m["served_model"] for e in episodes for k in ("user_sim", "teacher")
                                  for m in e["generation"][k])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--tag", default="v0", help="specs_<tag>.jsonl -> episodes_<tag>.jsonl")
    ap.add_argument("--all", action="store_true", help="realize every spec in the file (no feature pick)")
    ap.add_argument("--only", nargs="*", help="re-realize these episode ids in place")
    ap.add_argument("--only-file", help="file with one episode id per line (e.g. data/export/<v>/eval_ids_v2.txt)")
    ap.add_argument("--assistant-role", default="teacher",
                    help="config/llm.yaml role playing the assistant; not 'teacher' = trained model, no guidance")
    ap.add_argument("--out-tag", help="write episodes_<out-tag>.jsonl instead of episodes_<tag>.jsonl")
    ap.add_argument("--limit", type=int, help="only the first N chosen specs (smoke test)")
    args = ap.parse_args()
    if args.only_file:
        args.only = (args.only or []) + Path(args.only_file).read_text().split()
    if args.review:
        write_review([json.loads(l) for l in (OUT / f"episodes_{args.tag}.jsonl").read_text().splitlines()], args.tag)
    else:
        main(args.tag, args.limit, set(args.only or []), args.all, args.assistant_role, args.out_tag)
