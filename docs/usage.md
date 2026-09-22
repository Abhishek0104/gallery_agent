# Using the pipeline

This repo generates synthetic conversations for an on-device gallery assistant (a < 1B tool-calling model),
checks every conversation against its ground truth, and exports it for training. This guide is how to run it
and how to change it. Design reasoning lives in the other docs; this is the how-to.

---

## 1. How it fits together

```
registry/*.yaml ──► catalog ──► specs ──► episodes ──► verdicts ──► export ──► train / eval
 (tools)            (paths)     (meaning)   (wording)    (checks)     (JSONL)
```

| Stage | Command | Reads | Writes |
|---|---|---|---|
| Personas (once) | `python -m specs.make_personas` | `config/personas.yaml`, `regions.yaml`, `relations.yaml` | `data/personas/persona_*.json` |
| Query pool (once) | `python -m specs.make_query_pool` | `config/query_pool.yaml`, personas | `data/query_pool.json` |
| Catalog | `python catalog/generate_catalog.py` | `registry/*.yaml` | `catalog/path_catalog.yaml` |
| Specs | `python -m specs.make_specs` | catalog, personas, pool, `config/specs.yaml` | `data/specs/specs_<tag>.jsonl` |
| Episodes | `python -m realize.run` | specs, `config/realize.yaml` | `data/episodes/episodes_<tag>.jsonl` |
| Verify | `python -m verify.run` | episodes | `data/episodes/verified_<tag>.jsonl` |
| Export | `python -m export.build_export` then `python -m export.render_check` | `config/export.yaml` | `data/export/<version>/` |
| Train | `python -m train.sft_lora` (CUDA machine) | `config/train.yaml` | `runs/<name>/adapter` |

- A **spec** is the ground truth of one conversation, fixed before any wording: the tool calls, their arguments,
  their outcomes, and how the user's messages are grouped into turns.
- An **episode** is that spec acted out: the user simulator writes the user's messages, the teacher plays the
  assistant, and the simulator returns tool results from the spec.
- A **verdict** scores the episode against its spec. Only accepted episodes are exported.
- Everything is tagged: `--tag v2` reads `specs_v2.jsonl` and writes `episodes_v2.jsonl` / `verified_v2.jsonl`.

## 2. Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt           # pipeline
uv pip install --python .venv/bin/python -r requirements-train.txt     # export render check + training
echo 'GEMINI_API_KEY=...' > .env          # git-ignored; llm.py loads it automatically
.venv/bin/python -m pytest -q tests        # ~120 tests, a few seconds, no API calls
```
Run every command from the repo root with the venv's Python (`.venv/bin/python -m ...`).

## 3. Generate a batch (the common case)

Personas, query pool and catalog already exist in the repo; a new batch starts at specs.

```bash
# 1. specs: 200 happy-path specs (every catalog path at least twice)
python -m specs.make_specs --n 200 --seed 301 --tag v3
#    or round 2 (no_results / cancelled delete / collage over the limit / missing album or effect):
python -m specs.make_specs --round2 --n 200 --seed 302 --tag r3

# 2. episodes: realize every spec in the file
python -m realize.run --tag v3 --all

# 3. verify
python -m verify.run --tag v3 --quiet     # prints only rejected episodes, then totals and failures by check
```
Then read `data/specs/review_v3.md` and `data/episodes/review_v3.md` (human-readable transcripts).

**Always use a new `--tag` and `--seed` for a new batch.** The same tag overwrites files; the same seed
reproduces the same specs (and, through the cache, the same episodes).

Useful flags:

| Flag | Where | Effect |
|---|---|---|
| `--dry-run` | make_personas, make_query_pool, make_specs | print one prompt, no LLM call |
| `--limit N` | make_personas, realize.run | only the first N (smoke test before a full run) |
| `--all` | realize.run | realize every spec; without it, a 15-episode review batch that covers each feature |
| `--only ep_0010 ...` / `--only-file ids.txt` | realize.run | re-realize just these episodes, in place |
| `--review` | make_specs, realize.run | rewrite the review markdown from saved files |
| `--show` | make_personas, make_query_pool | print a summary of what's saved |

**Before a large run, do a small one:** `make_specs --n 8 ...`, `realize.run --tag ... --all`, `verify.run`.
It costs a few dozen calls and catches problems before hundreds.

## 4. Reading the results

| File | What it tells you |
|---|---|
| `data/specs/_report_<tag>.json` | spec validation: accepted, retries, rejections by check |
| `data/episodes/_realize_failed_<tag>.json` | episodes dropped during realization, and why |
| `data/episodes/verified_<tag>.jsonl` | one verdict per episode: `score` (0–1), `accept`, per-check scores, failure messages |
| `data/episodes/review_<tag>.md` | transcripts with tool calls, results and any mismatch against the spec |

The verifier's **hard checks** must all pass for `accept`: the tool-call sequence, every argument (after
normalization), photo-set ids, query/question similarity and purity, answers copied exactly, no ids in replies,
and round-2 behavior (e.g. no call before asking a clarification). **Soft checks** only lower the score: numbers
grounded in tool results, "says what was deleted", clarification wording.

A drop in acceptance usually points at one check. Read those transcripts before changing anything: it is either
the teacher (fix `realize/prompts/teacher_guidance.txt`), the user simulator (its prompt or checks), or a
verifier rule that is too strict.

## 5. Export, train, evaluate

```bash
# export: set `version` and `sources` in config/export.yaml, then
python -m export.build_export     # train/eval JSONL, eval ids, manifest (split by held-out personas)
python -m export.render_check     # per-turn prompt/completion for the base model; checks every turn round-trips
python -m train.sft_lora --check  # data + loss masking only; safe anywhere
```
Training and serving need a CUDA machine; the exact `train.sft_lora`, `vllm serve` and eval commands are in
README "End-to-end training run". Eval puts the trained model in the teacher's seat
(`realize.run --assistant-role student --only-file data/export/<version>/eval_ids_<tag>.txt --out-tag student_<tag>`)
and scores it with the same verifier.

## 6. Changing things

### Tune ratios, counts, styles
Edit YAML only; no code:

| To change | Edit |
|---|---|
| search slot mix, people / place / date sampling, counts, turn grouping, album exists vs new | `config/specs.yaml` |
| round-2 scenario mix | `config/round2.yaml` |
| user message styles, alias / synonym rates, effect wording | `config/realize.yaml` |
| verifier score weights | `config/verify.yaml` |
| held-out personas, export sources | `config/export.yaml` |
| LoRA settings | `config/train.yaml` |

### Change an argument rule
`config/arg_types.yaml` says, per argument, how it is sampled, how the user says it, whether the spoken value
must appear in the message, and how the verifier compares it (normalization steps, or a similarity threshold).
Example: to accept "The Maldives" and "Maldives" as the same location, `location.compare` already has
`strip_leading_the`; to loosen query matching, lower `query.compare.semantic`.

### Add a tool
Write one file in `registry/` (copy the closest existing tool; `tests/fixtures/favorite_images.yaml` is a
minimal example). A tool shaped like an existing one (consumes a photo set, returns a status and/or a new set)
needs **no code**:
1. `model_facing`: the description and args the on-device model sees.
2. `pipeline.effect`, `io`, `catalog`: what it consumes / produces and where it may appear in a conversation.
3. `pipeline.sample` + `spec_outcome`: how specs set its args and its happy-path outcome.
4. `pipeline.output`: the result the model sees.
5. `pipeline.conversation`: how the user asks for it, how the filler is told the outcome, guidance for the teacher.

Then regenerate the catalog (`python catalog/generate_catalog.py`), run the tests, and generate a small batch.
`tests/test_new_tool.py` shows the whole flow for a YAML-only tool. Two limits: a tool with a new *kind* of
argument needs a strategy function in `specs/strategies.py`, and its content checks (like the collage count)
still go into the spec validator / verifier by hand for now.

### Change a prompt
Prompts live in `specs/prompts/`, `realize/prompts/` and the registry `conversation` templates. **Changing a
prompt changes the cache key of every call that uses it**, so that stage regenerates in full on the next run
(the cost of a batch, not a few calls). The golden tests will fail on purpose; if the change is intended,
re-record them: `python -m tests.golden.snapshot --write`.

### Switch the LLM for a role
Each role in `config/llm.yaml` (`generation`, `filler`, `user_sim`, `teacher`, `cleanup`, `embedding`, `student`)
has its own provider and model: `gemini` (default), `openai_compatible` (a vLLM server: `base_url` + `model`),
or `local` (in-process embeddings). Changing a role's model regenerates that stage. vLLM launch flags for tool
calling are in the README.

## 7. Caching, reruns and cost

- Every LLM call is cached in `data/cache/llm/` (git-ignored), keyed by provider, model, prompt and seed.
  **Rerunning a command only pays for calls it hasn't made before**, so a stopped run resumes where it stopped.
- The same seed + same prompts = the same output (Gemini also receives the seed).
- Rough cost per 200 episodes on flash: ~200 filler calls for specs, ~1,150 calls to realize, plus embeddings for
  verification. Pro models have a low daily quota (250 requests/day on our key); use them only for one-time
  stages (personas, query pool).
- Deleting `data/cache/` forces a full regeneration of everything.

## 8. When a run stops or drops episodes

| What you see | What it means | What to do |
|---|---|---|
| `[llm] ... 429, retry 1/5 in 30s` | rate limit | nothing; it waits and retries (up to 5 times) |
| `429 ... per_day` quota | daily quota used up | wait for the reset, or move that role to another model |
| `402 ... prepayment credits are depleted` | billing | add credits; rerun the same command (it resumes) |
| `RemoteProtocolError` / timeout, then retries | network drop or a hung request (180 s timeout) | nothing; retried automatically |
| `realization failed: EmptyResponseError / BlockedResponseError / TruncatedResponseError` | a bad model output (empty, safety-blocked, too long) | that one episode is dropped and listed in `_realize_failed_<tag>.json`; the batch continues |
| `realization failed: UserSimError` | the user simulator couldn't write a message passing its checks in 3 tries | dropped and listed; look at the spec if it repeats |
| `LLMError: ... failed (400)` | a real request error | the run stops; fix the cause and rerun |

There are no silent fallbacks: a model is never swapped behind your back, and a dropped episode is always listed.

## 9. Tests

```bash
python -m pytest -q tests                        # everything (~5 s)
python -m pytest -q tests/test_golden.py         # the refactor gate
python -m pytest -q tests/test_new_tool.py       # a YAML-only tool through the whole pipeline
```
The golden snapshot pins every prompt, skeleton, simulator result, verdict and export record produced from the
committed data. Run the tests before and after any code change; if golden fails and you didn't mean to change
a prompt or output, the change has a side effect.

## 10. Where to look next
- Tool specs: `registry/*.yaml`; argument rules: `config/arg_types.yaml`.
- Spec format and sampler: `docs/intent_filler_design.md`. Round 2: `docs/round2_design.md`.
- Dialogue realization and cleanup: `docs/user_simulator_design.md`. Verifier: `docs/verifier_design.md`.
- Export and eval: `docs/export_design.md`. Architecture: `docs/architecture_refactor.md`.
