# Round 2 — Non-happy outcomes and clarification

Round 1 (v0–v2) covered the happy path: every search finds photos, every delete is confirmed, every request
is complete. Round 2 adds the four behaviors the registry already defines but no data exercises yet:

| # | Scenario | Registry source | Correct model behavior |
|---|---|---|---|
| A | `no_results` | `search_images.errors.no_results` | say nothing was found, suggest loosening a filter, never invent results; stop the chain |
| B | delete `cancelled` | `delete_images.output.status` | acknowledge nothing was deleted; don't retry |
| C | collage over the limit | `make_collage` behavior + `too_many_images` | read the count; **don't call**; ask the user to select at most `{max}` |
| D | missing required argument | §2 "Missing required argument → ask" | **don't call**; ask for the missing value; call once the user answers |

Same principle as round 1: **the spec fixes the ground truth** (outcomes, what the assistant must and must not do,
and the user's follow-up) before any dialogue; the verifier checks calls against it.

---

## 1. How a scenario enters a spec
No new catalog paths. A scenario is an **overlay** on an existing path, sampled by code:

```json
"scenario": {"type": "no_results", "at": 2, "variant": "recover"}
```

| Scenario | Eligible paths / step | What the overlay changes |
|---|---|---|
| A no_results | any `search_images` step | that search's outcome → no results. **truncate**: the episode ends after the assistant's report (later steps dropped). **recover**: a new user turn drops one filter, a new search finds N photos, the rest of the path continues |
| B cancelled | any `delete_images` step (always terminal) | outcome → `cancelled` |
| C over limit | a `make_collage` whose input is a set with no selection before it (search, effect, `r0`) — not `ask_gallery` (≤ 4 photos, max ≥ 4) | input count sampled in `(max, 40]`; a no-call assistant turn is inserted, then a user turn with a selection event of `2..max` photos, then the collage on the selection |
| D missing arg | `move_to_album` (album) or `apply_effect` (effect) | the user's request omits the value; a no-call assistant turn asks; a new user turn gives the value; the call follows |

**One scenario per episode.** The rest of the episode stays happy path.

## 2. Spec format additions
Steps gain two kinds of planned non-call behavior, so the verifier knows which turns must have **no** tool call:
```json
{"i": 3, "expect": "ask", "about": "album"}            // D: assistant asks, no call
{"i": 3, "expect": "ask", "about": "select", "max": 6} // C: assistant asks to select <= max, no call
{"i": 2, "expect": "report", "about": "no_results"}    // A: assistant reports after the failed call
```
Outcome values: `search_images` → `{"count": 0, "error": "no_results"}`; `delete_images` → `{"status": "cancelled"}`.
Recovery searches carry `"loosens": <step>` (the previous args minus one slot), mirroring `"refines"`.
Turn grouping keeps the round-1 rules plus: a user turn always follows an `expect: ask` step (the answer), and
an `expect: ask` step always ends a turn.

## 3. Simulator (model-facing results)
| Case | Model sees |
|---|---|
| no results | `{"error": "no_results"}` (no handle is created) |
| delete cancelled | `{"status": "cancelled", "count": 0}` |
| collage over the limit, if called anyway | `{"error": "too_many_images", "max": N}` (safety net; the episode is rejected) |

A call where the spec expects `ask` gets the backend's normal response for that call (e.g. `too_many_images`) and
is flagged; there is no default "helpful" outcome.

## 4. Intent filler
Unchanged in shape. The motivation must fit the scenario (e.g. D: the owner wants the photos in an album but
hasn't said which one yet). For A-recover the filler also writes nothing new: the dropped slot is chosen by code.

## 5. User simulator
New turn intents, still code-rendered with required / forbidden phrases:
- **D first request:** the message must **not** contain the album name / any effect word ("put these in an
  album", "add a filter to these"). New check: forbidden phrases.
- **D answer:** reply with just the value ("Goa Trip", "the sepia one"), required phrase = the value
  (effects via the round-1 surface forms).
- **C:** after the assistant asks, the user selects fewer photos (app line `[user selected N photos → rK]`) and
  says "these".
- **A-recover:** "try without the date" / "any from Goa at all?": the dropped slot is named in the intent.
- **B:** nothing new: the cancel happens in the app dialog; the user's next message (if any) is not needed.

## 6. Teacher guidance (v4, teacher only)
Add, from the registry `expect` lines:
- A search with no results: say nothing was found, suggest loosening one filter, don't continue the chain,
  never invent results.
- Delete cancelled: say nothing was deleted; don't retry.
- Before make_collage, compare the count with the limits; if outside, don't call it: ask the user to select
  2–{max} photos.
- Missing album or effect: don't guess; ask. When asking for an effect, name the available ones.

## 7. Verifier additions
| Check | Hard | How |
|---|---|---|
| no call in an `expect: ask` turn | yes | the turn has no tool calls |
| no calls after `no_results` in that turn | yes | chain stopped |
| no second delete after `cancelled` | yes | |
| recovery search = previous args minus exactly the dropped slot | yes | exact args, like refinements |
| asks about the right thing | no (soft) | reply is a question mentioning the album / effect / "select" + `max` |
| no_results reply: says nothing found, suggests loosening, no invented counts | no (soft) | keyword + grounded-numbers check |
| cancelled reply: says nothing was deleted | no (soft) | keyword check |
| effect question names the offered effects | no (soft) | every listed effect word appears |

Soft checks are heuristics until the LLM judge exists (parking list).

## 8. Batch plan
- Sampler quotas for a round-2 batch of 200: A 25% (truncate 60 / recover 40), B 20%, C 25%, D 30% (album 50 / effect 50).
  Paths are drawn only from those eligible for the sampled scenario, covering each eligible path before repeating.
- Same flow as round 1: generate → review ~20 by eye → full batch → verify → acceptance + failures by check.
- Training mix (happy vs round 2) is decided at export, not here.

## 9. Decisions (review, 2026-09-22)
1. Empty search → `{"error": "no_results"}` (no handle); cancelled delete → `{"status": "cancelled", "count": 0}`.
2. no_results split: truncate 60 / recover 40.
3. Collage under the minimum: parked.
4. Unavailable effect: parked.
5. Missing-argument clarification: album and effect only.
6. Scenario mix as in §8.

## Parking list
- Collage under the minimum (1 photo selected, "make a collage").
- Unavailable effect ("add a vignette" → say it's unavailable and name what is).
- Other clarifications (e.g. "these" with no set shown).
- LLM judge for the soft checks (clarification quality, no_results wording).
- Multiple scenarios in one episode; user ignoring a clarification; repeated no_results.
- `select_images` (v2.0) as a second resolution for collage-over-limit.
