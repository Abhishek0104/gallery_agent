# Gallery Agent — Data Generation Pipeline

## Project
On-device conversational tool-calling agent for a Samsung-style gallery app.
- Orchestrator LLM: **< 1B params**, text-only, **English only**.
- Vision lives behind tools: SigLIP-based search and an internal question-answering pipeline.
- Tools (v1.0): `search_images`, `delete_images`, `move_to_album`, `make_collage`, `apply_effect`, `ask_gallery`.
- Target behaviors: single call, multi-step, multi-turn, clarification, human-in-the-loop, failure recovery,
  while staying natural and conversational.

This repo builds the **synthetic data generation pipeline** used to train and evaluate that model.

## Design docs (read these first)
- `registry/*.yaml` — tool specs. **Source of truth for tool behavior** (argument rules: `config/arg_types.yaml`).
- `docs/registry_decisions.md` — registry design rules, conversation/context conventions, per-tool behavior decisions.
- `docs/intent_filler_design.md` — episode spec format, spec sampler, intent filler, validator (+ parking list).
- `docs/simulator_design.md` — lightweight simulator: handle ledger + outcomes from the spec.
- `docs/user_simulator_design.md` — dialogue realization: user simulator (no backstory), teacher, cleanup pass.
- `docs/verifier_design.md` — scored checks of a conversation against its spec.
- `docs/round2_design.md` — no_results, delete cancelled, collage over limit, missing-arg clarification.
- `docs/export_design.md` — training export (per-turn prompt/completion), persona holdout, eval.
- `docs/architecture_refactor.md` — registry-driven pipeline (phase 1 done; phase 2 only if publishing).
- `docs/usage.md` — **how to use the pipeline**: run a batch, read results, change rules, add a tool, troubleshoot.
- `README.md` — setup, pipeline commands, LLM providers, vLLM flags, end-to-end training run.

## Pipeline
1. **Registry** — one YAML spec per tool (`io`, `catalog`, `output`, `outcomes`, `sample`, `conversation`). ✅
2. **Path catalog** — generated from the registry: 86 paths (`catalog/path_catalog.yaml`). ✅
3. **Episode specs** — personas (20), query pool (1,588), spec sampler, intent filler, validator; round-2 scenario
   overlays. ✅
4. **Dialogue realization** — user simulator + teacher against the simulator; backstory cleanup pass. ✅
5. **Simulator** — handle ledger; tool outputs from the spec via registry templates. ✅
6. **Verification** — calls and replies vs spec, scores + accept. ✅ (LLM judge: later)
7. **Export** — HF chat-format JSONL, Qwen3.5 render check, per-turn SFT data, LoRA script, student eval hooks. ✅
   (training and serving run on a CUDA machine, not here)

Core idea: **meaning first, wording last.** Specs fix the ground truth (args + outcomes) before any dialogue;
LLMs only make it natural; the verifier checks calls against the spec.

## Layout
```
registry/        # tool YAML specs + typed loader (registry/args.py: argument types, normalizers); v2/ = backlog
catalog/         # path catalog generator + path_catalog.yaml
config/          # llm roles, personas, query pool, specs, round2, realize, verify, arg_types, export, train, interactive
specs/           # personas, query pool, spec sampler + strategies, scenarios, intent filler, validator, purity
sim/             # handle ledger, tool-output simulator
realize/         # user simulator, teacher loop, system prompt + teacher guidance, cleanup pass, interactive CLI
verify/          # verifier + runner
export/          # canonical export, render check
train/           # LoRA SFT script
llm.py           # one client: gemini (default), openai_compatible (vLLM), local embeddings; cache + retries
data/            # personas, query pool, specs, episodes, exports (JSONL); data/cache/ (LLM cache) is git-ignored
tests/           # unit tests, golden snapshot (tests/golden/), YAML-only tool acceptance test
docs/
```

## Conventions
- **Registry is the single source of truth.** Adding a tool = YAML spec (+ catalog regen); minimal code changes.
- **Everything seeded and reproducible.** LLM outputs cached as JSON keyed by seed.
- **Stages communicate via versioned JSON/JSONL records** (spec → episode), so any stage can rerun alone.
- **LLM only where unavoidable** (personas, query pool, intent filler, user simulator, teacher, judges). Everything else is code.
- **Verifiers return scores, not just pass/fail**, so they can double as RL rewards later.
- Keep modules framework-agnostic; a NeMo Gym adapter may wrap simulator + verifier later.

## Working rules
- **Base case first.** v0 = happy path only (normal counts, deletes confirmed, no clarifications/errors).
- **See the first generation before tuning** weights, ratios or adding complexity.
- New edge cases go on a **parking list** (in the relevant doc), not into code.
- Don't add fields, arguments or errors that the real backend doesn't support.
- **Golden tests gate refactors.** `tests/golden/golden.json` pins every prompt, skeleton, simulator output,
  verdict and export record; a changed prompt byte invalidates the LLM cache. Rewrite it only for an intended
  change (`python -m tests.golden.snapshot --write`).
- **LLM roles** (`config/llm.yaml`): `gemini-3.1-pro-preview` for personas and the query pool;
  `gemini-3.8-flash` for the intent filler, user simulator, teacher and cleanup; `gemini-embedding-2`.
  No silent fallbacks; bad outputs drop the episode; API errors stop the run.

## Current status / next steps
Data (all in this private repo; publishing anything needs a company policy check first):
- `v2c` — 199 happy-path episodes (all 86 paths), backstory cleaned, 199/199 verified.
- `r2b` — 197 round-2 episodes (seed fix + no-backstory prompt), 193/197 verified.
- `data/export/e2e_v2` — export from v2c + r2b: 314 train / 78 eval episodes (held-out personas 04, 17, 19, 20
  cover all four round-2 scenario types); 1,748 / 425 per-turn examples. Base model: Qwen/Qwen3.5-0.8B
  (752M text params), non-thinking.
- Tags: `personas-v0`, `v1`, `v2`, `r2`, `e2e_v2`.

Eval — two metrics, both against the same export:
- **Interactive (primary)**: the model takes the teacher's seat unguided, then the verifier scores it. Measures
  model + Gemini user simulator + embeddings jointly; needs CUDA (vLLM) and the API.
- **Teacher-forced (secondary)**: `export.eval_forced` predicts each turn from the gold history. Pure function
  of model + data — no simulator, no API, runs on a Mac (MPS). Headline is the structural match; it cannot see
  error recovery or exposure bias.
- Untrained base, 24 sampled rows (`data/export/e2e_v2/forced_eval_base_24.*`): calls are well-formed
  (0 malformed / 0 truncated, tool name 0.85) but structural 0.54 — it packs filter slots into `query` and
  calls a tool where the gold asks the user something (reply `kind` 0.55). Directional only at n=24.
- **`accept` is a data filter, not a model metric.** Every hard check must pass, so one extra call or one
  `off_script` clarification rejects an otherwise fine conversation (the teacher lost r2_0136 at score 0.984 on
  a 0.750 cosine tie). Read the continuous `score` and the per-group means for a model; keep `accept` for
  filtering training data.

Next:
1. End-to-end pipeline test on a CUDA machine: `bash scripts/e2e_cuda.sh [prep|baseline|train|eval]`
   (preflight → base teacher-forced baseline → `train.sft_lora` → adapter teacher-forced → vLLM →
   `realize.run --assistant-role student` → `verify.run`). Goal: test the pipeline, not the model — the
   numbers only need to be sane and better than the baseline, not good.
2. Then scale (≈5k episodes, ≈60 personas; `docs/export_design.md` §6).
3. When a real new tool is added: move the spec validator's content checks and the verifier's reply checks into
   the registry (`docs/architecture_refactor.md` §9).
4. Phase 2 (package, CLI, typed records, guides) only if publishing is approved.

Parked (not started): LLM judge for faithfulness / naturalness; out-of-scope requests (nothing in the specs
asks for something the tools can't do); offline query similarity (needs the 0.75 threshold re-tuned).
