# Dialogue Realization — v0 Design (user simulator + teacher)

## Purpose
Turn one **episode spec** into one **conversation**. The spec already fixes the meaning (tool calls, arguments,
outcomes, turn grouping); realization only adds wording. Stage output is an **episode record** that the
verifier (next stage) scores against the spec.

v0 = happy path, as in the specs: no clarifications, no errors, no closing "thanks" turns.

---

## 1. Actors

| Actor | Kind | Sees | Produces |
|---|---|---|---|
| User simulator | LLM (`user_sim` role) | persona, motivation, conversation text so far, this turn's intent + required phrases | one user message per spec turn |
| Teacher | LLM (`teacher` role), function calling | on-device system prompt + tool declarations + **teacher guidance** + conversation | tool calls and assistant replies |
| Simulator | code (`sim/`) | spec, calls so far | tool results from the spec; app events |

Both LLM roles run on `gemini-3.8-flash` (rate limits). Every call is cached (seeded), so an episode replays exactly.

## 2. Loop (one spec turn at a time)
```
for turn in spec.turns:
    app events for this turn  -> "[user selected N photos → rN]"   (selection steps; r0 before turn 1)
    user message              <- user simulator(turn intent, required phrases, conversation so far)
    repeat (max 8):
        teacher               -> tool calls and/or text
        simulator             -> results for the tool calls
    until the teacher answers with text and no tool calls
```
The user simulator reacts to the teacher's **actual** replies ("great, now…"), but *what* it asks for comes
only from the spec turn.

## 3. On-device system prompt (generated from the registry)
What the small model will see at inference, and what training data is exported with.
- One short role line, plus: photo sets are ids like `r1`; the app injects `[user selected N photos → rN]`.
- Tool declarations built from `registry/*.yaml` `model_facing` only, with volatile values filled per episode:
  `{min}–{max}` in the collage description, `{effects}` as the effect enum (this episode's list and order).
- Arg types: `str` → string, `list[str]` → array of strings, `ImageSet` → string id (`rN`), `enum` → string enum.

Stable behavior (slot rules, routing, confirmation habits) is **not** in this prompt: it lives in the weights
(registry rule 6) and reaches the teacher through the guidance block.

## 4. Teacher guidance (teacher only, never exported)
Appended to the teacher's system prompt between markers; stripped at export. Written from
`docs/registry_decisions.md` §2 and the per-tool behavior notes:
- Routing: questions (what / when / how many / is there / do I have) → `ask_gallery`; find / show → `search_images`.
- Search args: copy people / place / date as spoken; `"me"` for I/me/myself/selfies; "my" is ownership, not a
  people filter; named pets in `people`, unnamed animals in `query`; `query` = visual content only.
- Refinement ("only the ones from Goa") = a new search with the previous filters plus the new one.
- "these" / "them" = the newest relevant set; act on the newest set.
- Collage: read the count first; call only if within limits (otherwise ask the user to select — not in v0).
- Effects: map synonyms onto the listed effects ("grayscale" → `black_and_white`).
- Delete: call directly (the app confirms), and say what is being deleted in the same reply.
- `ask_gallery`: a self-contained question (resolve "it" / "that"); relay the answer verbatim.
- Multi-step requests: one call at a time, each using the previous result; then one short reply.
- Replies: short and natural, give counts, never show ids like `r2`, never invent results.

## 5. Surface forms: code picks the pieces, the LLM writes the sentence
Per episode, code samples how each fixed value is *said*; the user simulator must include those exact phrases.

| Value | Surface choices |
|---|---|
| person name / named pet | the name |
| relation | an alias from `config/relations.yaml` (e.g. `mum`, `hubby`, `daughters`) |
| `me` | the simulator writes it naturally; check accepts me / myself / I / my selfies |
| location, date | exactly the spec value |
| effect | the enum name in words, or a synonym (`config/realize.yaml`) |
| album | exactly the spec value |
| style | terse / casual / polite / chatty (sampled with quotas) |

The **query** is the exception: the user describes the content in their own words and the teacher writes
its own query. The verifier compares it to the spec query by embedding similarity; all other arguments
must match exactly (relations after alias normalization).

## 6. User simulator
- One LLM call per spec turn. Output `{message}`.
- Never sees tool names, handles or argument syntax.
- Code checks, with retry + feedback (max 3): every required phrase present (case-insensitive), no `r\d+` ids,
  no tool names, not empty, at most ~60 words.
- If the teacher asked a question the spec did not plan, the simulator answers it from the turn intent and
  the episode is flagged `off_script`.

## 7. Simulator (code)
Handle ledger as in `docs/simulator_design.md`. The teacher's calls are matched in order against the spec's
planned calls; a matched call returns the spec outcome. Handles are allocated by the simulator in call order
(spec handles map onto them). An unplanned call gets a default outcome and flags the episode `unplanned_call`.

Model-facing results: `search_images {id, count}`, `ask_gallery {answer, id, count}`,
`apply_effect {status, images}`, `make_collage {status, collage}`,
`move_to_album {status, count, album, created}`, `delete_images {status, count}`.

## 8. Episode record (`data/episodes/episodes_v0.jsonl`)
```json
{"episode_id": "ep_0001", "spec": {...}, "system_prompt": "...", "tools": [...], "teacher_guidance_version": 1,
 "surface": {"people": {"daughter": "daughters"}, "effect": "vintage", "style": "terse"},
 "messages": [{"role": "user", "content": "[user selected 5 photos → r0]\nmake these vintage"},
              {"role": "assistant", "content": "...", "tool_calls": [{"name": "apply_effect", "args": {...}}]},
              {"role": "tool", "name": "apply_effect", "content": {"status": "created", "images": "r1"}}],
 "flags": [], "generation": {"user_sim": {...served_model...}, "teacher": {...}}}
```
Messages are provider-neutral; the teacher guidance is stored by version only.

## 9. Checkpoint
Realize 10–20 episodes from `data/specs/specs_v0.jsonl` (spread over paths, including selection, ask,
refinement and delete paths) and review by eye **before** building the verifier. A quick realization report
(tool sequence vs spec, exact-arg matches, flags) helps the review but is not the verifier.

## Parking list
- Closing / small-talk turns ("thanks!") without tool calls.
- Typos and heavy slang in user messages.
- User simulator answering clarification turns on purpose (round 2, with clarification specs).
- Teacher deviating on purpose to create recovery data.
