# Architecture refactor — registry-driven pipeline (proposal for review)

## Goal
The code may be published (the data stays private; see §7). Two things must become easy and local:
1. **Change a rule for a tool argument** (e.g. how `search_images` dates are compared, a new search slot) by
   editing YAML, not code spread across stages.
2. **Add a new tool** so that catalog paths, specs, dialogues, simulator outputs and verification for it are
   generated from its YAML.

Scope: **what works today is locked.** The refactor changes structure, not behavior: every prompt, spec and
tool output for the current six tools must stay byte-identical (§6). No new scenarios or failure cases.

---

## 1. What the code does today (measured)

4,315 lines of Python in 24 modules, 455 lines of YAML config, 985 lines of tests.

**The registry is the declared source of truth, but code reads only its prompt part.**
`model_facing` feeds the system prompt and the spec validator's arg check. Of `pipeline`, code reads two
constraints (through `collage_bounds()` / `effect_values()`, which name `make_collage` / `apply_effect` inside a
generic loader) and `top_k`. `pipeline.output`, `pipeline.effect` and `pipeline.errors` are read by **nothing**.
Tool behavior is re-implemented by hand instead:

| Tool knowledge | Where it actually lives | Registry field that should drive it |
|---|---|---|
| handle in/out, what can follow what | `catalog/generate_catalog.py` `PRODUCERS` / `CONSUMERS`, `ok_*` rules | `pipeline.effect`, arg types, output type |
| count rules (collage 2..max, effect = same count) | `spec_sampler.need_bounds` / `sample_counts` | `pipeline.constraints`, output count |
| model-facing results | `sim/simulator.py` (7 per-tool branches) | `pipeline.output` |
| delete is terminal / destructive | catalog rules, simulator, turn rules | `pipeline.effect: destructive` |
| outcome text for the filler | `intent_filler.render_steps` (per-tool lambdas) | `pipeline.output` + a template |
| how the user asks for it | `user_sim.turn_intent` (9 branches) | per-tool request template |
| what the teacher is told | `teacher_guidance.txt` (prose) | per-tool guidance lines |
| what the verifier compares | `verifier.EXACT_ARGS`, `norm_arg` | arg type → comparison rule |
| error scenarios | `scenarios.ELIGIBLE` + overlay functions | `pipeline.errors` |

Counted: **~140 hardcoded tool-name references in 12 source files, 33 per-tool `if/elif` branches.**

**Search-slot knowledge is spread the same way:** `people / location / date / query` appear in 13 files
(27× `spec_sampler`, 26× `user_sim`, 11× `verifier`, …). `SLOTS` is defined twice; `date_core` and
`normalize_person` live in `specs/spec_validator.py` but are imported by `realize/` and `verify/`; private
helpers (`_lexicon`, `_has_phrase`) are imported across packages; `quota_list` / `Dealer` (sampling utilities)
live in `persona_outline.py`.

**Other structure issues:** 17 module-level `yaml.safe_load` / template reads at import time (config can't be
injected per run, tests work around it); `llm.py` is one 497-line file with four providers; `spec_sampler.py`
(390) mixes counts, slots, quotas and turns; `user_sim.py` (322) mixes surface forms, intents and checks; the
catalog generator builds structured steps, throws them away, writes a rendered string that `spec_sampler`
re-parses with a regex, and still writes its output into the current directory.

### What a change costs today
| Change | Files touched today |
|---|---|
| Change how `date` is normalized / compared | `spec_validator`, `verifier`, `user_sim`, `query_purity`, `teacher_guidance.txt`, `config/specs.yaml` (6) |
| Add a search slot (e.g. `album` filter) | registry, `config/specs.yaml`, `spec_sampler`, `scenarios`, `spec_validator`, `user_sim`, `verifier`, `intent_filler`, guidance (9) |
| Add a tool | registry, catalog generator, `spec_sampler`, `intent_filler`, `spec_validator`, `simulator`, `user_sim`, `verifier`, `cleanup`, `scenarios`, guidance, tests (12) |

---

## 2. Target design

**Principle:** YAML declares *what* a tool or argument is; code provides a small set of named, reusable
*strategies* that interpret it. Stages never name a tool; they read its spec.

### 2.1 Tool spec (registry, extended — every field has a consumer)
```yaml
# registry/apply_effect.yaml
name: apply_effect
model_facing:                                  # unchanged: the on-device prompt
  description: "Apply a photo effect; saves new copies, originals unchanged"
  args:
    images: {type: ImageSet, required: true}
    effect: {type: enum, values: "{effects}", required: true}
pipeline:
  effect: write                                # read | write | destructive (destructive = terminal + app confirms)
  io:                                          # catalog, sampler counts, simulator handles
    consumes: images
    produces: {kind: same_as_input, count: same_as_input}     # set | single | same_as_input | none
  constraints:
    effect: {values: [cool, warm, sepia, black_and_white], randomize: true, shown: [2, 4]}
  output: {status: created, images: $out}      # simulator result template ($out = new handle)
  catalog: {max_per_path: 1}                   # plausibility rules as data
  conversation:
    request: "Ask to apply a \"{effect}\" effect to {images}."          # user simulator
    outcome: "{count} new copies ({out}); originals unchanged"          # intent filler
    guidance: "Map the user's words onto the listed effects (\"grayscale\" -> black_and_white)."   # teacher
    request_words: [effect, filter, tone, apply]                        # cleanup: words that ask for it
  errors: {}                                   # round-2 behaviors (no_results, too_many_images, ...)
```

### 2.2 Argument types (new `config/arg_types.yaml`) — the search-rule knob
Each argument (search slots and tool args) names a type; the type says how to sample it, how a user says it,
how the verifier compares it and whether query purity applies:
```yaml
people:
  sample: persona_people          # strategy: names / relations / me / named pets, from the persona
  say: relation_alias             # surface form: canonical or an alias from config/relations.yaml
  compare: {normalize: [relation_alias, lower], as: set}
location:
  sample: persona_place           # places_visited 80% / home_city 20% (weights stay in config/specs.yaml)
  say: verbatim
  compare: {normalize: [lower, strip_leading_the]}
date:
  sample: date_phrase             # generic + region phrases; no personal events
  say: strip_preposition          # the user says "2023", not "in 2023"
  compare: {normalize: [lower, strip_preposition]}
query:
  sample: pool_fill               # the intent filler writes it from query-pool examples
  say: paraphrase
  compare: {semantic: 0.75}       # embedding cosine threshold (today in config/verify.yaml)
  purity: true
effect: {sample: from_constraint, say: effect_synonym, compare: exact}
album:  {sample: album_fill, say: verbatim, compare: {normalize: [strip]}}
```
The strategies (`persona_place`, `strip_preposition`, …) are small Python functions registered by name in one
module. YAML composes them; a genuinely new behavior is one new function.

### 2.3 What stages become
| Stage | Today | After |
|---|---|---|
| catalog | hardcoded producer/consumer tables | reads `io` + `effect` + `catalog` from every tool; writes structured steps |
| spec sampler | per-tool step builder | generic: arg values from arg types, outcomes from `io` + constraints |
| intent filler | per-tool lambdas | `conversation.outcome` templates |
| simulator | 7 branches | `pipeline.output` template + `io` for handles |
| user simulator | 9 branches | `conversation.request` templates + arg-type surface forms |
| teacher guidance | one prose file | general rules + each tool's `conversation.guidance` |
| verifier | `EXACT_ARGS`, `norm_arg` | per-arg `compare` from arg types |
| cleanup | `TOOL_WORDS` | `conversation.request_words` |

### 2.4 Where "YAML only" stops (stated in the docs, not over-promised)
- **YAML only:** a tool shaped like an existing one (consumes an ImageSet, returns a status and/or a handle);
  a new search slot using existing strategies; a changed comparison or normalization; changed constraints,
  pools, weights, templates.
- **One small code file:** a new argument behavior (new strategy), a tool that consumes two ImageSets, a new
  outcome category, a new round-2 scenario kind.
- The count logic (backward bounds over a path), the purity checker and the turn-grouping rules stay code; they
  become parameterized by the specs instead of naming tools.

---

## 3. The two walkthroughs, after the refactor

**Change a search rule** — e.g. locations compare case- and "the"-insensitively, and the user may say either:
edit `location.compare` in `config/arg_types.yaml`. Nothing else.

**Add a search slot** — e.g. an `album` filter on `search_images`: add the arg to
`registry/search_images.yaml`, give it a type in `config/arg_types.yaml`, add it to the slot patterns in
`config/specs.yaml`, regenerate. (Plus one strategy function only if sampling it needs something new.)

**Add a tool** — e.g. `favorite_images`:
```yaml
name: favorite_images
model_facing:
  description: "Mark photos as favorites"
  args: {images: {type: ImageSet, required: true}}
pipeline:
  effect: write
  io: {consumes: images, produces: none}
  output: {status: favorited, count: $in_count}
  catalog: {max_per_path: 1, terminal: false}
  conversation:
    request: "Ask to mark {images} as favorites."
    outcome: "marked {count} as favorites"
    guidance: "Favoriting changes nothing else; don't ask for confirmation."
    request_words: [favorite, favourite, star, heart]
  errors: {}
```
then `gallery-data catalog` regenerates paths including it, and specs → dialogues → verification work
unchanged. An **acceptance test** proves it: a fixture tool YAML flows through catalog → sampler → filler
prompt → simulator → validator → verifier with no Python edits.

---

## 4. Code organization for publication
- **Package layout:** `src/gallery_agent/` with `registry/`, `catalog/`, `specs/`, `realize/`, `sim/`,
  `verify/`, `export/`, `train/`, `llm/`, and a shared `core/` (typed models, config loader, text helpers,
  sampling utilities). `pyproject.toml`, one CLI (`gallery-data personas|pool|catalog|specs|realize|verify|export`)
  instead of `python -m` per module.
- **Typed records:** pydantic models for `ToolSpec`, `ArgType`, `Spec`, `Step`, `Episode`, `Verdict` (versioned),
  replacing dicts with ad-hoc keys (`expect`, `skipped`, `refines`, `loosens`, `requests`).
- **Config:** one loader; a `Config` object passed explicitly; no reads at import time.
- **Split big modules:** `llm/` (client + cache, one file per provider); `specs/sampler/` (counts, slots,
  quotas, turns); `realize/user_sim/` (surface forms, intents, checks).
- **Shared helpers move to `core/text.py`:** normalization (`date_core`, `normalize_person`), purity, lexicons,
  self-contained check; `core/sampling.py`: `quota_list`, `Dealer`.
- **Catalog:** writes structured steps (with tool names, not short kinds) and writes where it lives; the regex
  re-parse goes away.
- **Docs:** architecture overview, "add a tool", "change an argument rule", glossary (handle, spec, episode,
  scenario, surface form), the decision docs we already have.

## 5. Migration plan (repo works and data stays valid at every step)
0. **Golden tests first.** Pin hashes of: skeletons (v2 and round 2), filler prompts, user-sim prompts per turn,
   tool declarations, system prompt + guidance, query-pool call plan, simulator outputs for every committed
   episode's calls, and verifier verdicts on committed batches — under three `PYTHONHASHSEED` values. Every
   later step must keep them identical (so the LLM cache stays valid and nothing is regenerated).
1. Typed registry loader; remove `collage_bounds()` / `effect_values()`.
2. Catalog from registry `io` / `effect` / `catalog`; structured steps; fix the output path.
3. Move shared helpers to `core/`, config loader, no import-time reads (moves only).
4. Arg types (`config/arg_types.yaml`) driving sampler, user sim, verifier.
5. Per-tool templates and output templates driving filler, simulator, user sim, guidance, cleanup.
6. Fixture-tool acceptance test.
7. Package layout, CLI, pyproject, docs.

Each step is its own commit; the golden tests are the review gate.

## 6. The constraint that dominates
Every LLM call is cached by its prompt. A refactor that shifts one byte of a prompt for the current tools
invalidates that stage's cache (≈ 2,300 flash calls to regenerate v2 + round 2). The templates in §2.1 must
reproduce today's strings exactly for the six current tools; the golden tests (step 0) enforce it. Intentional
prompt changes stay separate commits with a regeneration noted.

## 7. Decisions (2026-09-22)
Approved in two phases. **Phase 1 (now):** step 0 golden tests, registry-driven `io` / `output` / `conversation`
fields, `config/arg_types.yaml`, and the acceptance test with a sample tool — each its own commit, golden tests
unchanged. **Phase 2 (later, only if publishing, after the policy check):** `src/` package, CLI, typed records,
module splits, guides.
1. The §2.4 boundary is right: round-2 scenario kinds stay code.
2. Names: `gallery_agent` (package) / `gallery-data` (CLI).
3. **Data stays in the private repo.** Publishing anything (code or data) needs a company policy check first.
4. Drop the `anthropic` provider.

## 8. Open questions (original, answered above)
1. Scope of "YAML only": is §2.4 the boundary you want, or should round-2 scenario kinds also be YAML?
2. Package name and CLI name (`gallery_agent` / `gallery-data`)?
3. Published data: keep `data/` (personas, pool, specs, episodes, exports) in the repo, or publish datasets
   separately (e.g. a Hugging Face dataset) and keep only small samples here?
4. Keep the `anthropic` provider (JSON only, unused by the pipeline) or drop it before publishing?

## 9. Phase 1 status (done; phase 1 stops here)
| Step | Commit | Golden tests |
|---|---|---|
| 0 golden tests (32 pinned artifacts, 3 hash seeds) | `cc47584` | recorded |
| drop the `anthropic` provider | `357cec7` | unchanged |
| 1 registry `io` / `catalog` / `output` → catalog generator + simulator | `870e99a` | unchanged |
| 2 `sample` / `spec_outcome` / `conversation.outcome` → spec sampler, system prompt, intent filler | `06e2ae7` | unchanged |
| 3 `conversation` templates → user simulator, cleanup, teacher guidance | `c39894c` | unchanged |
| 4 `config/arg_types.yaml` → sampling, wording, verifier comparison | `3289359` | unchanged |
| 5 acceptance test: `tests/fixtures/favorite_images.yaml` | this commit | unchanged |

**Adding a tool shaped like an existing one is now one YAML file.** The acceptance test copies the registry,
adds `favorite_images.yaml`, and runs the unmodified pipeline: the catalog grows from 86 to 114 paths (28 with
the tool), and all 28 sampled specs pass the spec validator, get user requests from the tool's template
("Ask to mark the edited collage as favorites."), are realized against the simulator (result from the tool's
output template) and are accepted by the verifier.

**Changing an argument rule is one YAML edit** in `config/arg_types.yaml` (comparison, wording, required,
sampling strategy, thresholds).

Hardcoded tool references went from ~140 in 12 files to 24 in 5; per-tool branches from 33 to 18. What is left:
- `specs/scenarios.py`, `realize/user_sim.py` round-2 answer lines: scenario kinds, which stay code (§8.1).
- `specs/spec_validator.py`: per-tool content checks (ask count ≤ top_k, effect in the episode's list, collage
  count, album consistency, delete counts). **Decision:** these move into the registry when a real new tool is
  added; until then a new tool gets the generic schema / handle / argument checks only.
- `verify/verifier.py`: reply checks for ask ("answer relayed"), delete ("says deleted") and cancelled deletes
  (same decision as the validator's content checks).
- `realize/run.py`: the feature tags used only to pick a varied review batch.
