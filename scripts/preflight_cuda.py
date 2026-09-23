"""
Preflight for the end-to-end training run (README "End-to-end training run").

    python -m scripts.preflight_cuda            # checks only; no training, no API calls
    python -m scripts.preflight_cuda --serving  # also check the vLLM servers (student; local user simulator)
    python -m scripts.preflight_cuda --serve-plan   # servers $LLM_OVERLAY asks for (read by scripts/e2e_cuda.sh)

Reads the configs and the exported data and fails fast on the things that would otherwise waste a GPU
session: a config pointing at the wrong export, an eval id with no spec, a missing training dependency,
a student role that renders differently from training. Errors exit 1; warnings are printed and pass.
"""
import argparse
import json
import os
from pathlib import Path

import yaml

from llm import OVERLAY_ENV, load_config

ROOT = Path(__file__).resolve().parent.parent
ERRORS, WARNINGS = [], []


def err(msg):
    ERRORS.append(msg)


def warn(msg):
    WARNINGS.append(msg)


def check_configs():
    """export.yaml, train.yaml and llm.yaml must describe the same run."""
    exp = yaml.safe_load((ROOT / "config" / "export.yaml").read_text())
    trn = yaml.safe_load((ROOT / "config" / "train.yaml").read_text())
    llm = load_config()                 # config/llm.yaml + $LLM_OVERLAY

    version = exp["version"]
    out = ROOT / "data" / "export" / version
    if trn["data"] != f"data/export/{version}":
        err(f"config/train.yaml data={trn['data']!r} but config/export.yaml version={version!r}")
    if trn["base_model"] != exp["base_model"]:
        err(f"base_model differs: export {exp['base_model']!r} vs train {trn['base_model']!r}")

    student = llm.get("roles", {}).get("student")
    if not student:
        err("config/llm.yaml has no 'student' role (realize.run --assistant-role student)")
    else:
        if student.get("provider") != "openai_compatible":
            err(f"student role provider is {student.get('provider')!r}, expected 'openai_compatible'")
        # The student must render exactly like training, or the eval measures the template, not the model.
        want = exp.get("chat_template_kwargs", {})
        got = (student.get("extra_body") or {}).get("chat_template_kwargs", {})
        if got != want:
            err(f"student chat_template_kwargs {got} != export {want} (training and inference must match)")
        if student.get("temperature") not in (0, 0.0):
            warn(f"student temperature is {student.get('temperature')!r}; 0 keeps the eval reproducible")
    return exp, trn, student or {}, version, out


def check_export(out, trn):
    """The exported files the training and eval steps read."""
    for name in ("sft_train.jsonl", "sft_eval.jsonl", "manifest.json", "render_report.json"):
        if not (out / name).exists():
            err(f"missing {out.relative_to(ROOT)}/{name} — run export.build_export then export.render_check")
    report = out / "render_report.json"
    if report.exists():
        rep = json.loads(report.read_text())
        if rep["base_model"] != trn["base_model"]:
            err(f"render_report base_model {rep['base_model']!r} != train base_model {trn['base_model']!r}")
        longest = max(s["tokens_max"] for s in rep["splits"].values())
        if longest > trn["max_length"]:
            err(f"longest rendered example is {longest} tokens > train max_length {trn['max_length']}")
        elif longest > 0.9 * trn["max_length"]:
            warn(f"longest example {longest} tokens is close to max_length {trn['max_length']}")


def check_eval_ids(out):
    """Every eval_ids_<tag>.txt must resolve: specs_<tag>.jsonl exists and holds every id."""
    files = sorted(out.glob("eval_ids_*.txt"))
    if not files:
        err(f"no eval_ids_*.txt in {out.relative_to(ROOT)} — step 5 has nothing to evaluate")
    tags = []
    for f in files:
        tag = f.stem[len("eval_ids_"):]
        specs = ROOT / "data" / "specs" / f"specs_{tag}.jsonl"
        if not specs.exists():
            err(f"{f.name} has no matching data/specs/specs_{tag}.jsonl (realize.run --tag {tag})")
            continue
        have = {json.loads(l)["episode_id"] for l in specs.read_text().splitlines() if l.strip()}
        want = [l.strip() for l in f.read_text().splitlines() if l.strip()]
        missing = [i for i in want if i not in have]
        if missing:
            err(f"{f.name}: {len(missing)} id(s) not in specs_{tag}.jsonl, e.g. {missing[:3]}")
        tags.append((tag, len(want)))
    return tags


def check_deps():
    """Training imports are lazy, so check them here rather than at the first GPU step."""
    import importlib

    for mod in ("torch", "transformers", "peft", "accelerate"):
        try:
            m = importlib.import_module(mod)
            print(f"  {mod:<13} {getattr(m, '__version__', '?')}")
        except ImportError:
            err(f"{mod} not installed — pip install -r requirements-train.txt")
    try:
        import torch

        if not torch.cuda.is_available():
            warn("torch.cuda.is_available() is False — steps 3b/4 need a CUDA machine")
        else:
            print(f"  cuda          {torch.cuda.device_count()} device(s): {torch.cuda.get_device_name(0)}")
    except ImportError:
        pass


EVAL_ROLES = ("user_sim", "embedding")      # what step 5 needs besides the student


def check_env(trn):
    """Credentials, optional dependencies and output paths for the roles step 5 actually uses."""
    roles = load_config()["roles"]
    print(f"  overlay       {os.environ.get(OVERLAY_ENV) or 'none (config/llm.yaml)'}")
    for r in EVAL_ROLES:
        print(f"  {r:<13} {roles[r]['provider']}: {roles[r]['model']}")
    gemini = [r for r in EVAL_ROLES if roles[r]["provider"] == "gemini"]
    dotenv = ROOT / ".env"
    in_env = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    in_file = dotenv.exists() and any(
        l.split("=", 1)[0].strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and l.split("=", 1)[-1].strip()
        for l in dotenv.read_text().splitlines()
    )
    if gemini and not (in_env or in_file):
        err(f"no GEMINI_API_KEY / GOOGLE_API_KEY in the environment or .env — {', '.join(gemini)} run on Gemini "
            f"in step 5 (or set {OVERLAY_ENV}=config/llm_local.yaml)")
    if roles["embedding"]["provider"] == "local":
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            err("embedding role is local but sentence-transformers is not installed — pip install -r requirements-train.txt")
    if roles["embedding"]["model"] != "gemini-embedding-2":
        warn(f"embedding is {roles['embedding']['model']}: the 0.75 query/question thresholds (config/arg_types.yaml) "
             "were tuned on gemini-embedding-2 — calibrate before trusting accept (README \"Local models for eval\")")
    adapter = ROOT / trn["output_dir"]
    if adapter.exists():
        warn(f"{trn['output_dir']} already exists — training will overwrite it")


def serve_plan():
    """One line per server the overlay's `serve:` block asks for: role model port gpu_fraction max_len devices."""
    cfg = load_config()
    from urllib.parse import urlparse

    for role, s in (cfg.get("overlay", {}).get("serve") or {}).items():
        r = cfg["roles"].get(role, {})
        dev = s.get("cuda_visible_devices")
        print(role, r.get("model", "-"), urlparse(r.get("base_url", "")).port or "-",
              s.get("gpu_memory_utilization") or "-", s.get("max_model_len") or "-", "-" if dev is None else dev)


def post(url, payload, timeout=120):
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def check_serving(student):
    """Optional: is the server up, serving the student's model, and parsing tool calls?"""
    import urllib.request

    base = student.get("base_url", "").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=5) as r:
            served = [m["id"] for m in json.load(r).get("data", [])]
    except Exception as e:  # server down, wrong port, no route
        err(f"cannot reach {base}/models ({e.__class__.__name__}) — is vLLM up? (step 4)")
        return
    print(f"  served        {served}")
    model = student.get("model")
    if model not in served:
        err(f"student model {model!r} not served; vLLM offers {served}")
        return

    # One real tool call: the wrong --tool-call-parser doesn't fail loudly, it leaves the raw call in
    # `content` and every episode scores as a miss. Cheaper to find out here than after 78 episodes.
    tool = {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": "Search the gallery for images matching a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "what to look for"}},
                "required": ["query"],
            },
        },
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "find my photos of the beach"}],
        "tools": [tool],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 128,
    }
    payload.update(student.get("extra_body") or {})   # chat_template_kwargs go top-level over HTTP
    try:
        msg = post(f"{base}/chat/completions", payload)["choices"][0]["message"]
    except Exception as e:
        err(f"tool-call probe failed ({e.__class__.__name__}): {e}")
        return
    if msg.get("tool_calls"):
        name = msg["tool_calls"][0]["function"]["name"]
        print(f"  tool calls    parsed ({name})")
    else:
        err("the server returned no parsed tool_calls; content="
            f"{(msg.get('content') or '')[:120]!r} — check --tool-call-parser "
            f"(config/train.yaml tool_call_parser) against the model family")


def check_user_sim_serving(role):
    """A local user simulator must return schema-valid JSON (structured outputs), or every episode is dropped."""
    if role["provider"] != "openai_compatible":
        return
    base = role["base_url"].rstrip("/")
    schema = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}
    payload = {"model": role["model"], "temperature": 0, "max_tokens": 200,
               "messages": [{"role": "user", "content": "Write one short message asking a gallery app to find beach photos."}],
               "response_format": {"type": "json_schema", "json_schema": {"name": "Probe", "schema": schema}}}
    payload.update(role.get("extra_body") or {})
    try:
        content = post(f"{base}/chat/completions", payload)["choices"][0]["message"]["content"] or ""
        msg = json.loads(content)["message"]
        print(f"  user_sim      json_schema ok ({msg[:60]!r})")
    except Exception as e:
        err(f"user_sim probe at {base} failed ({e.__class__.__name__}: {str(e)[:120]}) — is it served, "
            "structured outputs on, thinking off?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serving", action="store_true", help="also check the vLLM servers (after step 4)")
    ap.add_argument("--serve-plan", action="store_true", help="print the servers $LLM_OVERLAY asks for, and exit")
    args = ap.parse_args()
    if args.serve_plan:
        serve_plan()
        return

    exp, trn, student, version, out = check_configs()
    check_export(out, trn)
    tags = check_eval_ids(out)
    print(f"export        {version}  base {trn['base_model']}  adapter -> {trn['output_dir']}")
    print(f"eval tags     {', '.join(f'{t} ({n} ids)' for t, n in tags) or '-'}")
    check_deps()
    check_env(trn)
    if args.serving:
        check_serving(student)
        check_user_sim_serving(load_config()["roles"]["user_sim"])

    for w in WARNINGS:
        print(f"warn: {w}")
    for e in ERRORS:
        print(f"ERROR: {e}")
    print("preflight failed" if ERRORS else "preflight ok")
    raise SystemExit(1 if ERRORS else 0)


if __name__ == "__main__":
    main()
