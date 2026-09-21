# Gallery Agent — Tool Registry Decisions (v1.0, rev 3)

On-device conversational tool-calling agent (<1B params, English only) for gallery tasks.
Orchestrator LLM is text-only; vision lives behind tools (SigLIP search, internal VQA pipeline).

---

## 1. Registry design rules

1. **Every field needs a consumer.** A field stays only if something in the pipeline reads it
   (model prompt, executor/world simulator, sampler/planner, user simulator, verifier/eval).
   Implementation details (endpoints, latency, docs) do not belong in the registry.
2. **Two-part spec.**
   - `model_facing`: name, one-line description, args (type, required, default, enum). Kept minimal — every token costs on-device context.
   - `pipeline`: never shown to the model — output type, effect, constraints, errors.
3. **An argument exists only if:** the backend supports it, users naturally say it,
   a small model can fill it reliably, and it is not redundant with another arg/tool.
4. **Errors only if the backend actually returns them.** Each error carries an `expect`
   line describing the correct model behavior (used by the verifier).
5. **`effect` is the only policy field:** `read | write | destructive`. Confirmation is derived from it.
6. **Volatile values go in the prompt and are randomized in training** (limits, enums, option lists).
   The model learns to *read* them, so changing them is a registry edit, not a retrain.
   Stable behavior can live in the weights.

---

## 2. Conversation & context conventions

- **Handles:** every image set is a short sequential handle (`r1`, `r2`, …). The app maps handles to real image IDs.
  The model never sees raw image lists or UUIDs.
- **Model-facing form of an ImageSet:** `{id: "rN", count: N}`. `ImageSet` is a pipeline type name only.
- **Selection events:** when the user selects photos in the UI, the app registers a new ImageSet and injects:
  `[user selected N photos → rN]`. The model treats it like any other handle; "these" resolves to the newest relevant handle.
- **Raw phrases, app resolves:** dates ("last December") and locations ("Goa") are copied as the user said them.
  Locations are reverse-geocoded by the app; dates are resolved by a deterministic resolver.
- **People:** `"me"` is a reserved value for the gallery owner (I / me / myself / selfies).
  Names and relations are copied as spoken ("Riya", "daughter"). **The app resolves relations**
  (e.g., `daughter → {Riya, Priya}`); the model never resolves them itself.
- **Relation normalization (tool wrapper):** the gallery accepts only canonical relation words.
  The model keeps copying relations as spoken ("mum", "hubby", "my daughters"); the tool wrapper maps
  each alias to its canonical word via `config/relations.yaml` (`mum → mom`) before calling the gallery.
  Names, `me` and unknown words pass through unchanged. The alias list lives only in that file, so adding an
  alias is a config edit, not a retrain. Validators and the verifier compare `people` values after the same
  normalization.
  **"my" means ownership, not people:** "my Goa photos" → no people filter; "photos of me in Goa" → `people=["me"]`.
- **People matching semantics:** the `people` list is an **AND across elements**; each element resolves to a
  group that is an **OR across its members**. `["me", "daughter"]` → me AND (Riya OR Priya).
  OR across different elements is not supported in v1.0.
- **Search returns every match.** No result caps; the handle holds exactly what the tool returned.
- **Pets:** a named pet behaves like a person (`people=["Bruno"]`); unnamed or generic pets go in `query` ("dog").
- **Slot boundaries for search** (full table in `intent_filler_design.md`):
  named place → `location`, scene type → `query`; named person / relation / `me` / named pet → `people`,
  unnamed descriptions → `query`; actions and attributes → `query`; documents → `query` only.
- **Refinement = re-search with merged args** ("only the ones from Goa" → new `search_images` call carrying previous filters plus the new one).
- **Missing required argument → ask** (e.g., move to album with no album name).
- **Destructive actions:** the app shows its own native confirmation dialog. The model calls the tool directly,
  states what it is deleting in the same turn, and handles `cancelled` gracefully.
- **Routing rule:**
  - Any **question** (what / when / how many / is there / do I have) → `ask_gallery`
  - Any **find / show command** ("show me", "find", "pull up") → `search_images`
- **Verbatim answers:** numbers, IDs and codes from `ask_gallery` are copied exactly (verifier enforces string match).

---

## 3. Tool specs

### search_images
```yaml
name: search_images
model_facing:
  description: "Find photos by content, people, place, date"
  args:
    query:    {type: str}         # visual content → SigLIP
    people:   {type: list[str]}   # "me" = owner; names, relations or named pets as spoken; AND across elements
    location: {type: str}         # raw phrase; reverse-geocoded
    date:     {type: str}         # raw phrase; app resolves
pipeline:
  output: ImageSet                # model sees {id: "rN", count: N}
  effect: read
  errors:
    no_results: {expect: "say nothing was found, suggest loosening a filter; never invent results"}
```

### delete_images
```yaml
name: delete_images
model_facing:
  description: "Delete photos (moves them to Recycle bin)"
  args:
    images: {type: ImageSet, required: true}   # rN handle
pipeline:
  output: {status: enum[deleted, cancelled], count: int}
  effect: destructive      # app shows its own confirmation dialog
  errors: {}
```

### move_to_album
```yaml
name: move_to_album
model_facing:
  description: "Move photos to an album (creates it if missing)"
  args:
    images: {type: ImageSet, required: true}   # rN handle
    album:  {type: str, required: true}        # as the user said it
pipeline:
  output: {status: enum[moved], count: int, album: str, created: bool}
  effect: write
  errors: {}
```

### make_collage
```yaml
name: make_collage
model_facing:
  description: "Make a grid collage from {min}–{max} photos"
  args:
    images: {type: ImageSet, required: true}
pipeline:
  constraints: {images.count: {min: 2, max: 9, randomize_max: [4, 6, 9, 12]}}
  output: {status: enum[created], collage: ImageSet}   # new handle, count 1
  effect: write
  errors:
    too_many_images: {expect: "ask the user to select at most {max}"}
```
Behavior: read the count before calling. If count > max (or < min), don't call — ask the user to select.
Backend rejection is a safety net only.

### apply_effect
```yaml
name: apply_effect
model_facing:
  description: "Apply a photo effect; saves new copies, originals unchanged"
  args:
    images: {type: ImageSet, required: true}   # any count
    effect: {type: enum, values: "{effects}", required: true}
pipeline:
  constraints: {effect: {values: [cool, warm, sepia, black_and_white],
                         randomize: true}}
  output: {status: enum[created], images: ImageSet}   # new handle, same count as input
  effect: write
  errors: {}
```
Behavior: synonyms map onto the listed effects ("grayscale" → `black_and_white`).
No reasonable match → say it's unavailable and name what is.

### ask_gallery
```yaml
name: ask_gallery
model_facing:
  description: "Answer any question about the user's photos"
  args:
    question: {type: str, required: true}   # self-contained, no pronouns
pipeline:
  constraints: {top_k: 4}                   # internal; not model-facing
  output: {answer: str, images: ImageSet}   # model sees {answer, id: rN, count}
  effect: read
  errors: {}
```
Behavior: the internal pipeline handles intent, search and VQA. The orchestrator's job is to write a
self-contained question (resolve "it" / "that" from context) and relay the answer verbatim.

---

## 4. Out of scope (v1.0)

- Videos and screenshots (images only)
- Album name fuzzy matching / duplicate-album prevention
- Questions about a specific open or selected photo (`ask_gallery` always searches the whole gallery)
- OR searches across different people ("Riya or Karan")

## 5. v2.0 backlog

```yaml
name: select_images
model_facing:
  args:
    images: {type: ImageSet, required: true}
    k:      {type: int, required: true}
    by:     {type: enum[relevance, aesthetic], default: aesthetic}
pipeline:
  output: ImageSet            # new handle, count k
  effect: read
```
Enables "collage of my best 5" without user selection. When added, collage-overflow scenarios get a
second valid resolution (select top-k vs ask the user) — data must cover both.

## 6. Notes for later stages

- Verification compares the model's calls against the episode spec (no gallery state diff).
- Verifiers should output **scores, not just pass/fail**, so they can double as RL rewards.
- Keep core modules framework-agnostic; wrap world + verifiers as a NeMo Gym resources server via a thin adapter.