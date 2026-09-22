# Export — training format and train/eval split

## Purpose
Turn verified episodes into (1) training data for the < 1B on-device model and (2) a held-out eval set whose
primary metric is the same verifier, run on the small model's own conversations. Code: `export/`, `train/`
(see README "End-to-end training run").

---

## 1. What goes in
| Source | Episodes | Accepted | Guidance | Use |
|---|---|---|---|---|
| `v2` (happy path, 200 specs, all 86 paths) | 199 | 199 | v3 | yes |
| `r2` (round 2 scenarios) | 197 | 197 | v4 | yes |
| `v1` baseline | 200 | 192 | v2 | no: older guidance (e.g. "my cousin"), superseded by v2 |
| `v0` | 15 | 15 | v1–v2 | no: review batch |

Only **verifier-accepted** episodes are exported. Rejected / dropped episodes are kept on disk, not exported
(parking: preference data later). 396 episodes is too few to train on; see §6.

## 2. Canonical record (model-agnostic JSONL)
One line per episode, in the Hugging Face chat format that `tokenizer.apply_chat_template(messages, tools=tools)`
accepts. The model-specific rendering happens at training time (§3), so the export stays reusable across base models.

```json
{"id": "r2/r2_0084",
 "tools": [{"type": "function", "function": {"name": "make_collage",
            "description": "Make a grid collage from 2–4 photos",
            "parameters": {"type": "object", "properties": {"images": {"type": "string", "description": "photo set id, e.g. r1"}},
                           "required": ["images"]}}}, "..."],
 "messages": [
   {"role": "system", "content": "You are the photo gallery assistant on this phone. ..."},
   {"role": "user", "content": "show photos of mum and me drinking coffee, make a collage of them, and put it in a new album called Mom and Me"},
   {"role": "assistant", "tool_calls": [{"type": "function", "function": {"name": "search_images",
      "arguments": {"people": ["mum", "me"], "query": "drinking coffee"}}}]},
   {"role": "tool", "name": "search_images", "content": "{\"id\": \"r1\", \"count\": 17}"},
   {"role": "assistant", "content": "I found 17 photos of you and your mum drinking coffee. A collage can hold at most 4 photos—please select up to 4 ..."},
   {"role": "user", "content": "[user selected 4 photos → r2]\nuse these"},
   "..."],
 "meta": {"source": "r2", "spec_version": 1, "path": "P028", "persona": "persona_10", "scenario": {"type": "collage_over_limit"},
          "style": "terse", "verifier_score": 1.0, "teacher_guidance_version": 4, "models": {"teacher": "gemini-3.8-flash"}}}
```
Rules:
- **System prompt = the on-device prompt** stored with the episode. The teacher guidance is never in the export.
- **Tools = this episode's declarations** (collage max and effect list/order vary per episode), so the model learns to
  read them instead of memorizing them (registry rule 6).
- **Tool call arguments as objects**, not JSON strings (HF convention; templates serialize them). Tool results as
  compact JSON strings, exactly what the model saw.
- **App events stay inline** in the user message (`[user selected 4 photos → r2]`), as the app will inject them.
- **One tool call per assistant message** (true for all current data: the teacher never batched calls), so no
  parallel-call template support is needed.
- **No call ids.** HF templates for small models mostly don't use them; add synthetic ids only if the chosen
  template requires them.
- `meta` is for filtering and analysis; trainers ignore it.

## 3. Rendering and loss (training time)
- Render with the chosen base model's chat template: `apply_chat_template(messages, tools=tools, tokenize=False)`.
  The template must support a tools block, assistant tool calls and a `tool` role (e.g. Qwen-style `<tool_call>`).
- **Loss on assistant tokens only**: tool calls and replies. System, user, app events and tool results are
  context. (`train/sft_lora.py` masks the prompt tokens.)
- **One example per assistant turn** (prompt = render of the history with the generation prompt, completion = the
  turn). Not one example per episode: Qwen3.5 renders earlier assistant turns *without* the empty
  `<think></think>` block that the non-thinking generation prompt adds, so whole-episode training would teach a
  prompt shape the model never sees at inference. Per-turn examples match inference exactly (e2e_v1: 1,751 train
  / 453 eval examples, ≤ 1,388 tokens).
- `export/render_check.py` asserts for every turn: the generation prompt is a prefix of the rendered turn, it ends
  with the non-thinking marker, the completion parses back to exactly the gold tool call or reply (parameters
  converted by the tool schema, since the XML call format loses "2022" vs 2022), and no guidance leaked.

## 4. Train / eval split
**Primary: hold out personas.** Eval episodes come only from personas never seen in training, so eval measures
copying unseen names, relations, places and album names — the thing a small model most easily memorizes instead.
- Hold out **4 of 20 personas** (≈ 20%): chosen by code to cover regions (2 India, 1 US or UK, 1 other) and
  households, and to include tricky names (hyphen, apostrophe, two-word). The list is fixed in `config/export.yaml`.
- Every held-out persona's episodes from both `v2` and `r2` go to eval (~80 episodes today).
- Report eval coverage of paths and scenarios; with 4 personas some paths will be missing at 396 episodes, which
  scale (§6) fixes.
- The split is by persona id, so it is stable when more data is generated: new episodes of a held-out persona
  always land in eval.

**Secondary (later): held-out paths** — a small probe of a few catalog paths never trained on, to test composing
known steps in a new order. Parked until there is enough data to spare paths from training.

## 5. Eval
1. **Interactive (primary):** the trained model takes the teacher's seat in `realize/run.py` for each held-out spec
   (simulator + user simulator unchanged), and the **verifier** scores its conversation. Report acceptance and
   failures by check, split by path, scenario and turn style — the same table as for teacher data.
2. **Teacher-forced (fast, secondary):** on held-out transcripts, predict each assistant turn given the gold
   history; exact match on tool name + normalized args (verifier normalization), and "call vs. reply" accuracy.
   Cheap enough to run every checkpoint.

## 6. Scale and mix before training
- 396 accepted episodes is a starting point. Proposed first training set: **~5,000 episodes**, e.g. 3,000 happy
  path (all 86 paths × ~35) + 2,000 round 2, with the same generate → verify flow and the flash model.
- Keep the happy:round-2 ratio as a config value; revisit after the first eval.
- Personas: 20 is thin for 5k episodes (≈ 250 each). Propose generating **~60 personas** first (same outline
  sampler and validator), then holding out ~12 of them.

## 7. Output layout
```
export/                      # code: build_export.py, render_check.py
data/export/<version>/train.jsonl
data/export/<version>/eval.jsonl
data/export/<version>/manifest.json   # sources + tags, counts by split/path/scenario/persona, held-out personas,
                                      # registry + prompt hashes, guidance versions, git commit
```

## 8. Decisions (review, 2026-09-22)
1. Base model: **Qwen/Qwen3.5-0.8B**, non-thinking mode. Render with `enable_thinking=False` for training and at
   inference, so train and eval see the same prompt. (Note: the repo is an image-text-to-text model,
   `Qwen3_5ForConditionalGeneration`; used text-only.)
2. Sources: `v2` + `r2` only.
3. Split: persona holdout.
4. **No scaling yet.** First a small end-to-end run on the current ~396 episodes — export → render check → short
   LoRA fine-tune → eval with simulator + verifier — to test the pipeline, not the model. §6 is deferred.
5. Tool-call arguments as objects, no call ids.

## Parking list
- Preference / negative data from rejected episodes (DPO) and verifier scores as RL rewards.
- Held-out-path probe.
- Per-turn training examples, if full-episode examples prove too long for the target.
