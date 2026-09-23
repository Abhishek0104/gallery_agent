"""
Interactive CLI: talk to a trained model against the simulator, to find failure modes by hand.

    python -m realize.interactive --model http://localhost:8000/v1        # a vLLM server (config/llm.yaml `student`)
    python -m realize.interactive --model runs/e2e_v2_lora/adapter        # a local LoRA adapter (or a model dir / HF id)
    python -m realize.interactive --model ... --persona 04 --selection 6 --seed 7
    python -m realize.interactive --model ... --replay data/interactive/sessions.jsonl   # same inputs, new checkpoint

The model gets exactly what it gets on the phone: the on-device system prompt and tool declarations for a
sampled episode config (no teacher guidance). Its calls run against the realization simulator (sim/), through
the same turn loop (realize.run.run_teacher). With no spec, outcomes come from config/interactive.yaml: sampled
counts for search / ask, the happy path for everything else; /outcome forces the next one.

Commands: /select N, /outcome ..., /state, /persona, /save [note], /reset, /help, /quit.
"""
import argparse
import copy
import json
import random
import re
import shlex
import time
from pathlib import Path

import yaml

from llm import LLM, BadOutputError, ChatResult, EmptyResponseError, TruncatedResponseError
from llm import chat_openai, openai_client
from realize.run import run_teacher
from realize.system_prompt import system_prompt, tool_declarations
from registry import tools as registry_tools
from sim.simulator import Simulator
from specs.strategies import episode_config, render
from verify.verifier import HANDLE

ROOT = Path(__file__).resolve().parent.parent
ICFG = yaml.safe_load((ROOT / "config" / "interactive.yaml").read_text())
SPECS_CFG = yaml.safe_load((ROOT / "config" / "specs.yaml").read_text())
EXPORT_CFG = yaml.safe_load((ROOT / "config" / "export.yaml").read_text())
PERSONAS = ROOT / "data" / "personas"
HELP = """commands:
  /select N       the user selects N photos from the last photo set (shown with the next message)
  /outcome ...    force the next tool result: a registry outcome (no_results, cancelled) and/or key=value
                  pairs (count=0, count=3, answer="Yes, in March", created=false); /outcome clear drops it
  /state          handle ledger, episode config, pending selection / outcome, flags
  /persona        the persona (people, relations, pets, places, albums)
  /save [note]    append this session to {sessions}
  /reset          new session, new persona (unless --persona)
  /quit"""


# ---------------------------------------------------------------- personas
def load_personas():
    return {p["persona_id"]: p for p in (json.loads(f.read_text()) for f in sorted(PERSONAS.glob("persona_*.json")))}


def resolve_persona(personas, want):
    """'persona_04', '04' or '4' -> 'persona_04'."""
    pid = want if want in personas else f"persona_{int(want):02d}" if want.isdigit() else want
    if pid not in personas:
        raise SystemExit(f"unknown persona {want!r}; have {', '.join(sorted(personas))}")
    return pid


def persona_text(p):
    held = " (held out of training)" if p["persona_id"] in EXPORT_CFG["held_out_personas"] else " (seen in training)"
    o = p["owner"]
    lines = [f"{p['persona_id']}{held}: {o['name']}, {o['age']}, {p['household']}, {o['home_city']} ({p['region']})",
             "  people:  " + ", ".join(f"{x['name']} ({x['relation']})" for x in p["people"])]
    if p.get("pets"):
        lines.append("  pets:    " + ", ".join(f"{x['name']} ({x['species']})" for x in p["pets"]))
    lines += ["  places:  " + ", ".join(p["places_visited"]), "  albums:  " + ", ".join(p["albums"])]
    return "\n".join(lines)


# ---------------------------------------------------------------- printing
def fmt_value(v):
    return v if isinstance(v, str) and re.fullmatch(r"[\w.-]+", v) else json.dumps(v, ensure_ascii=False)


def fmt_call(name, args):
    return f"{name}({', '.join(f'{k}={json.dumps(v, ensure_ascii=False)}' for k, v in args.items())})"


def fmt_result(r):
    return "{" + ", ".join(f"{k}: {fmt_value(v)}" for k, v in r.items()) + "}" if isinstance(r, dict) else str(r)


# ---------------------------------------------------------------- model backends
# Both expose what run_teacher calls on an LLM: chat(system, contents, tools, seed) -> ChatResult, user_message,
# tool_results. Contents are OpenAI-style messages. Neither caches: a new checkpoint served under the same name
# must not replay the old one's answers.
class VLLMBackend:
    def __init__(self, url, served_model=None):
        self.llm = LLM("student")                  # temperature 0, thinking off (extra_body), max_tokens
        url = url.rstrip("/")
        self.llm.spec = {**self.llm.spec, "base_url": url if url.endswith("/v1") else url + "/v1"}
        client, _ = openai_client(self.llm)
        served = [m.id for m in client.models.list().data]
        want = served_model or self.llm.model
        if want not in served:
            if served_model or len(served) != 1:
                raise SystemExit(f"model {want!r} is not served at {url}; pass --served-model, one of {served}")
            want = served[0]
        self.llm.model = want
        self.name = f"{want} @ {url}"
        self.user_message, self.tool_results = self.llm.user_message, self.llm.tool_results

    def chat(self, system, contents, tools, seed):
        content, served = chat_openai(self.llm, system, contents, tools, seed)
        return ChatResult(content, {"provider": "openai_compatible", "model": self.llm.model, "served_model": served})


def hf_messages(system, contents):
    """OpenAI-style contents -> the chat messages the export renders: tool-call arguments as objects."""
    msgs = [{"role": "system", "content": system}]
    for m in contents:
        if m["role"] == "assistant":
            out = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                out["tool_calls"] = [{"type": "function", "function": {
                    "name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}}
                    for c in m["tool_calls"]]
            msgs.append(out)
        elif m["role"] == "tool":
            msgs.append({"role": "tool", "name": m["name"], "content": m["content"]})
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def parse_output(text, tools, n):
    """Raw Qwen completion -> an OpenAI-style assistant message (as vLLM's parser would return it)."""
    from export.render_check import END, RenderError, parse_completion

    if not text.rstrip().endswith(END):
        raise TruncatedResponseError(f"no {END} (max_new_tokens?): {text!r}")
    try:
        kind, a, b = parse_completion(text.rstrip(), tools)
    except RenderError as e:
        raise BadOutputError(f"{e}") from e
    if kind == "text":
        if not a:
            raise EmptyResponseError("empty reply")
        return {"role": "assistant", "content": a}
    before = text.split("<tool_call>")[0].strip()
    return {"role": "assistant", "content": before or None,
            "tool_calls": [{"id": f"call_{n}", "type": "function",
                            "function": {"name": a, "arguments": json.dumps(b, ensure_ascii=False)}}]}


class LocalBackend:
    """A model dir / HF id, or a LoRA adapter dir (adapter_config.json) on its base. Every turn re-renders the
    whole history with the chat template, exactly as export/render_check builds the training prompts."""

    def __init__(self, path, device="auto", dtype="float16", max_new_tokens=ICFG["max_new_tokens"]):
        from export.eval_forced import load_model

        p = Path(path)
        if (p / "adapter_config.json").exists():
            base = json.loads((p / "adapter_config.json").read_text()).get("base_model_name_or_path")
            base, adapter = base or EXPORT_CFG["base_model"], str(p)
        else:
            base, adapter = path, None
        self.tok, self.model, self.device = load_model(base, adapter, device, dtype)
        self.kwargs = EXPORT_CFG.get("chat_template_kwargs") or {}
        self.max_new_tokens, self.n = max_new_tokens, 0
        self.name = f"{base}{' + ' + adapter if adapter else ''} ({self.device})"

    @staticmethod
    def user_message(text):
        return {"role": "user", "content": text}

    @staticmethod
    def tool_results(results):
        return [{"role": "tool", "tool_call_id": cid, "name": name, "content": json.dumps(r, ensure_ascii=False)}
                for name, cid, r in results]

    def chat(self, system, contents, tools, seed):
        import torch
        from export.render_check import END

        decl = [{"type": "function", "function": t} for t in tools]
        prompt = self.tok.apply_chat_template(hf_messages(system, contents), tools=decl, tokenize=False,
                                              add_generation_prompt=True, **self.kwargs)
        ids = self.tok(prompt, add_special_tokens=False, return_tensors="pt").to(self.device)
        eos = self.tok.convert_tokens_to_ids(END)
        with torch.no_grad():
            out = self.model.generate(**ids, max_new_tokens=self.max_new_tokens, do_sample=False,
                                      eos_token_id=eos, pad_token_id=eos)
        text = self.tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=False)
        self.n += 1
        return ChatResult(parse_output(text, decl, self.n), {"provider": "local", "served_model": self.name})


def make_backend(model, served_model=None, device="auto", dtype="float16"):
    if re.match(r"https?://", model):
        return VLLMBackend(model, served_model)
    return LocalBackend(model, device, dtype)


class Printing:
    """Shows the model's text as it comes (tool calls are shown by the simulator)."""

    def __init__(self, backend, show):
        self.backend, self.show = backend, show
        self.user_message, self.tool_results = backend.user_message, backend.tool_results

    def chat(self, system, contents, tools, seed):
        res = self.backend.chat(system, contents, tools, seed)
        if res.text:
            leaked = sorted(set(HANDLE.findall(res.text)))
            self.show(f"assistant: {res.text}" + (f"   ⚠ handle in reply: {', '.join(leaked)}" if leaked else ""))
        return res


# ---------------------------------------------------------------- simulator without a spec
def sample_count(dist, rng):
    ranges = list(dist)
    lo, hi = map(int, rng.choices(ranges, weights=[dist[r] for r in ranges])[0].split("-"))
    return rng.randint(lo, hi)


def parse_force(text):
    """'no_results' / 'count=0 answer="Yes, in March"' -> {"names": ["no_results"], "set": {"count": 0, "answer": ...}}.
    Values: integers and true/false are typed, everything else stays text."""
    names, fields = [], {}
    for tok in shlex.split(text):
        k, sep, v = tok.partition("=")
        if not sep:
            names.append(k)                        # a registry outcome name
        elif re.fullmatch(r"-?\d+", v):
            fields[k] = int(v)
        elif v.lower() in ("true", "false"):
            fields[k] = v.lower() == "true"
        else:
            fields[k] = v
    return {"names": names, "set": fields}


def apply_force(tool, outcome, force):
    """-> (outcome, None), or (None, why) when the force does not fit this tool (it stays pending).
    Never builds a state the backend can't produce: a zero count becomes the tool's empty outcome (no_results)."""
    fields = set(tool.raw["pipeline"].get("spec_outcome", {})) | {k for o in tool.outcomes.values() for k in o["when"]}
    out = dict(outcome)
    for name in force["names"]:
        if name not in tool.outcomes:
            return None, f"{name!r} is not an outcome of {tool.name} (has: {', '.join(tool.outcomes) or 'none'})"
        out.update(tool.outcomes[name]["when"])
    for k, v in force["set"].items():
        if k not in fields:
            return None, f"{k!r} does not apply to {tool.name}"
        out[k] = v
    if out.get("count") == 0 and not tool.match_outcome(out)[0]:
        empty = [n for n, o in tool.outcomes.items() if o.get("produces") == "none"]
        if not empty:
            return None, f"{tool.name} has no empty result in the registry (count=0)"
        out.update(tool.outcomes[empty[0]]["when"])
    return out, None


class LiveSimulator(Simulator):
    """The realization simulator with one synthesized plan step per call: its outcome is sampled (or forced)
    right before the call, so handles, limits and result shapes all come from sim/ and the registry."""

    def __init__(self, spec, persona, seed, show=print):
        super().__init__(spec)
        self.persona, self.seed, self.show = persona, seed, show
        self.force, self.calls = None, 0

    def outcome(self, tool, args):
        src = args.get(tool.io.consumes) if tool.io.consumes else None
        in_count = self.ledger.get(src, {}).get("count", 1) if isinstance(src, str) else 1
        albums = {a.lower() for a in self.persona["albums"]}
        values = {"in_count": in_count, "count": in_count, "fill": ICFG["ask_answer"],
                  "created": str(args.get("album", "")).strip().lower() not in albums,
                  **{f"arg.{k}": args.get(k) for k in tool.model_facing["args"]}}
        if tool.io.count == "outcome":
            if tool.catalog.short not in ICFG["counts"]:
                raise SystemExit(f"config/interactive.yaml counts: nothing for {tool.catalog.short!r}")
            values["count"] = sample_count(ICFG["counts"][tool.catalog.short],
                                           random.Random(f"{self.seed}/{self.calls}"))
        return render(tool.raw["pipeline"]["spec_outcome"], values)

    def call(self, name, args):
        tool, note = self.tools.get(name), ""
        out = self.outcome(tool, args) if tool else {}
        if tool and self.force:
            forced, why = apply_force(tool, out, self.force)
            if forced is None:
                note = f"   (/outcome still pending: {why})"
            else:
                out, self.force, note = forced, None, "   (forced)"
        self.planned[self.pos:] = [{"i": self.calls, "call": name, "outcome": out}]
        self.calls += 1
        self.show(f"  → {fmt_call(name, args)}")
        n_flags = len(self.flags)
        result = super().call(name, args)
        warn = "".join(f"   ⚠ {f}" for f in self.flags[n_flags:])    # e.g. bad_handle: the backend would not take it
        self.show(f"  ← {fmt_result(result)}{note}{warn}")
        return result

    def last_set(self):
        alive = [h for h, v in self.ledger.items() if v["alive"]]
        return max(alive, key=lambda h: int(h[1:])) if alive else None


# ---------------------------------------------------------------- one session
class Session:
    def __init__(self, backend, persona, seed, selection=None, show=print, config=None):
        self.persona, self.seed, self.show = persona, seed, show
        self.backend = Printing(backend, show)
        self.model_name = getattr(backend, "name", type(backend).__name__)
        self.config = config or episode_config(random.Random(seed), SPECS_CFG["episode_config"], registry_tools())
        self.spec = {"episode_id": f"live_{time.strftime('%Y%m%d_%H%M%S')}_{seed}", "persona": persona["persona_id"],
                     "config": self.config, "initial_selection": {"count": selection} if selection else None,
                     "steps": [], "turns": []}
        self.sim = LiveSimulator(self.spec, persona, seed, show)
        self.system, self.tools = system_prompt(), tool_declarations(self.config)
        self.contents, self.messages, self.metas = [], [], []
        self.pending = [self.sim.initial_line()] if selection else []
        self.script = []                  # every input that shaped the conversation, in order (for --replay)
        self.errors = []                  # rolled-back turns: what was said, what the model produced, the error

    # ------------------------------------------------------------ inputs
    def select(self, n):
        h = self.sim.last_set()
        if h is None:
            raise ValueError("no photo set to select from yet")
        if not 1 <= n <= self.sim.ledger[h]["count"]:
            raise ValueError(f"{h} has {self.sim.ledger[h]['count']} photo(s); select 1..{self.sim.ledger[h]['count']}")
        line = self.sim.select({"count": n, "out": f"sel{len(self.script)}"})
        self.pending.append(line)
        self.script.append({"select": n})
        return line

    def outcome(self, text):
        if text.strip() == "clear":
            self.sim.force = None
        else:
            self.sim.force = parse_force(text)
        self.script.append({"outcome": text.strip()})

    def send(self, text):
        """One user message -> the model's calls and reply. On a bad output or API error the turn is rolled back
        (nothing recorded) and the error re-raised."""
        snap = copy.deepcopy(self.sim.__dict__)
        n_contents, n_messages, n_metas = len(self.contents), len(self.messages), len(self.metas)
        full = "\n".join(self.pending + [text])
        self.messages.append({"role": "user", "content": full})
        self.contents.append(self.backend.user_message(full))
        try:
            self.metas.append(run_teacher(self.backend, self.sim, self.system, self.tools, self.contents,
                                          self.messages, f"{self.spec['episode_id']}/t{len(self.script)}",
                                          self.sim.flags))
        except (Exception, KeyboardInterrupt) as e:
            tried = [c for m in self.messages[n_messages:] if m["role"] == "assistant" for c in m["tool_calls"] or []]
            self.errors.append({"say": text, "error": f"{type(e).__name__}: {e}", "calls": tried,
                                "after_script_step": len(self.script)})
            self.sim.__dict__.update(snap)
            del self.contents[n_contents:], self.messages[n_messages:], self.metas[n_metas:]
            raise
        self.pending = []
        if self.messages[-1]["role"] == "tool":
            self.show(f"  (cut off: {self.sim.calls - snap['calls']} calls and no reply; max_calls_per_turn)")
        calls = [{"name": c["name"], "args": c["args"]} for m in self.messages[n_messages:]
                 if m["role"] == "assistant" for c in m["tool_calls"] or []]
        reply = next((m["content"] for m in reversed(self.messages) if m["role"] == "assistant"), None)
        self.script.append({"say": text, "calls": calls, "reply": reply})
        return calls

    # ------------------------------------------------------------ views
    def state(self):
        lines = [f"config: {json.dumps(self.config)}", "handles:"]
        lines += [f"  {h}: {v['count']} photo(s) from {v['src']}{'' if v['alive'] else ' (gone)'}"
                  for h, v in self.sim.ledger.items()] or ["  (none)"]
        lines.append(f"last set: {self.sim.last_set()}")
        if self.pending:
            lines.append(f"pending selection: {' '.join(self.pending)}")
        if self.sim.force:
            lines.append(f"pending outcome: {self.sim.force}")
        if self.sim.flags:
            lines.append(f"flags: {sorted(set(self.sim.flags))}")
        return "\n".join(lines)

    def record(self, note=None, **extra):
        """This session in the episode format (realize.run), plus what --replay needs."""
        return {
            "episode_version": 1, "episode_id": self.spec["episode_id"], "spec": self.spec,
            "system_prompt": self.system, "tools": self.tools, "teacher_guidance_version": None,
            "assistant_role": "student", "surface": None, "messages": self.messages,
            "flags": sorted(set(self.sim.flags)), "generation": {"user_sim": [], "teacher": self.metas},
            "interactive": {"note": note, "model": self.model_name, "seed": self.seed,
                            "persona": self.persona["persona_id"],
                            "selection": (self.spec["initial_selection"] or {}).get("count"),
                            "script": self.script, "errors": self.errors, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra},
        }


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- replay
def replay(backend, records, show=print):
    """Re-run each saved session's inputs (messages, selections, forced outcomes) against `backend`, with the
    same persona, seed and episode config. Prints where the calls differ from the saved session."""
    personas, out = load_personas(), []
    for rec in records:
        it = rec["interactive"]
        show(f"\n=== {rec['episode_id']} · {it['persona']} · seed {it['seed']}"
             f"{' · ' + it['note'] if it.get('note') else ''}\n    was: {it['model']}")
        s = Session(backend, personas[it["persona"]], it["seed"], it.get("selection"), show, rec["spec"]["config"])
        same = total = 0
        for step in it["script"]:
            if "select" in step:
                try:
                    show(f"/select {step['select']}  {s.select(step['select'])}")
                except ValueError as e:
                    show(f"/select {step['select']}  skipped: {e}")
            elif "outcome" in step:
                s.outcome(step["outcome"])
                show(f"/outcome {step['outcome']}")
            else:
                show(f"you> {step['say']}")
                total += 1
                try:
                    calls = s.send(step["say"])
                except Exception as e:
                    show(f"  ! {type(e).__name__}: {e}")
                    continue
                if calls == step["calls"]:
                    same += 1
                else:
                    show("  ≠ was: " + ("; ".join(fmt_call(c["name"], c["args"]) for c in step["calls"]) or "no calls"))
        show(f"--- calls identical in {same}/{total} message(s)")
        out.append(s.record(it.get("note"), replay_of=rec["episode_id"], replay_of_model=it["model"]))
    return out


# ---------------------------------------------------------------- REPL
def repl(backend, personas, seed, persona=None, selection=None, sessions=ROOT / ICFG["sessions"]):
    import readline  # noqa: F401  line editing and history for input()

    rng = random.Random(seed)

    def new_session(s):
        pid = persona or rng.choice(sorted(personas))
        sess = Session(backend, personas[pid], s, selection)
        print(f"\n--- session seed {s} · model {sess.model_name}\n{persona_text(personas[pid])}\n"
              f"config: {json.dumps(sess.config)}" + (f"\n{sess.pending[0]}" if sess.pending else "")
              + "\n(/help for commands; ask_gallery answers are canned: /outcome answer=\"...\" before asking)")
        return sess

    sess = new_session(seed)
    while True:
        try:
            line = input("you> ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print()
            continue
        if not line:
            continue
        if not line.startswith("/"):
            try:
                sess.send(line)
            except KeyboardInterrupt:
                print("  (interrupted; turn rolled back)")
            except Exception as e:         # a bad output, an API error, or a call the simulator can't take
                print(f"  ! {type(e).__name__}: {e}\n  (turn rolled back; kept under `errors` if you /save)")
            continue
        cmd, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        if cmd in ("quit", "q", "exit"):
            return
        elif cmd == "select" and rest.isdigit():
            try:
                print(f"  {sess.select(int(rest))}  (shown with your next message)")
            except ValueError as e:
                print(f"  ! {e}")
        elif cmd == "outcome" and rest:
            try:
                sess.outcome(rest)
            except ValueError as e:          # shlex: unbalanced quotes
                print(f"  ! {e}")
                continue
            print(f"  next tool result: {sess.sim.force or 'sampled'}")
        elif cmd == "state":
            print(sess.state())
        elif cmd == "persona":
            print(persona_text(sess.persona))
        elif cmd == "save":
            append_jsonl(sessions, sess.record(rest or None))
            print(f"  saved {sess.spec['episode_id']} -> {Path(sessions).relative_to(ROOT)}")
        elif cmd == "reset":
            sess = new_session(rng.randrange(10 ** 6))
        else:
            print(HELP.format(sessions=ICFG["sessions"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument("--model", required=True, help="vLLM base URL (http://host:8000/v1), or a model dir / HF id / "
                                                   "LoRA adapter dir to run in-process")
    ap.add_argument("--served-model", help="vLLM model name (default: config/llm.yaml student.model)")
    ap.add_argument("--seed", type=int, help="session seed (default: random, printed)")
    ap.add_argument("--persona", help="force a persona: persona_04 or 04")
    ap.add_argument("--selection", type=int, help="start with N photos selected (r0)")
    ap.add_argument("--replay", help="sessions JSONL: re-run their inputs against --model")
    ap.add_argument("--only", nargs="*", help="with --replay: only these episode ids")
    ap.add_argument("--out", help="with --replay: output JSONL (default data/interactive/replay_<time>.jsonl)")
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "mps", "cpu"))
    ap.add_argument("--dtype", default="float16", choices=("float32", "float16", "bfloat16"))
    args = ap.parse_args()

    personas = load_personas()
    backend = make_backend(args.model, args.served_model, args.device, args.dtype)
    if args.replay:
        records = [json.loads(l) for l in Path(args.replay).read_text().splitlines() if l.strip()]
        records = [r for r in records if not args.only or r["episode_id"] in args.only]
        out = Path(args.out) if args.out else ROOT / "data" / "interactive" / f"replay_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        for r in replay(backend, records):
            append_jsonl(out, r)
        print(f"\nwrote {len(records)} replayed session(s) -> {out}")
        return
    seed = args.seed if args.seed is not None else random.randrange(10 ** 6)
    repl(backend, personas, seed, resolve_persona(personas, args.persona) if args.persona else None, args.selection)


if __name__ == "__main__":
    main()
