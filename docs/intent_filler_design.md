# Episode Specs & Intent Filler — v0 Design

## Purpose
Produce **episode specs**: the recipe for one conversation, fixed *before* any dialogue is written.

- **Catalog** = the menu (which tools, in which order).
- **Episode spec** = one order (concrete arguments, outcomes, turn grouping, persona).
- **Dialogue** = that order acted out (user simulator + teacher; later stage).

Meaning comes first, wording later: the intent filler decides *what the user means* (structured data only);
the user simulator later decides *how they say it*. The verifier checks the teacher's calls against the spec,
so ground truth never depends on how an LLM interpreted a sentence.

---

## 1. Components (build in this order)

| # | Component | Kind | Output |
|---|---|---|---|
| 1 | Personas | LLM, once | `data/personas/*.json` (~20) |
| 2 | Query pool | LLM, once, deduped | `data/query_pool.json` |
| 3 | Spec sampler | code | spec skeletons |
| 4 | Intent filler | LLM, per spec | filled specs |
| 5 | Intent validator | code (+ LLM judge for query purity) | accepted / rejected specs |

**v0 = happy path only:** every search returns a normal count (2 … collage_max), every delete is confirmed,
no clarifications, no errors. Failure outcomes, cancellations and clarification turns come in round 2.

**Checkpoint:** generate ~50 specs and review by eye *before* building the user simulator:
are intents plausible, are slot splits correct, is there real variety?

---

## 2. Personas

```yaml
owner: {name: Abhinav, home_city: Bengaluru}
people:
  - {name: Riya,  relation: daughter}
  - {name: Priya, relation: daughter}
  - {name: Meera, relation: wife}
  - {name: Karan, relation: friend}
pets:
  - {name: Bruno, species: dog}     # optional; some personas have unnamed pets or none
albums: ["Goa 2024", "Riya's birthday", "Wedding"]
places_visited: [Goa, Mysuru, Manali, Dubai]
```
Relations resolve on the app side (`daughter → {Riya, Priya}`). Named pets behave like people.

---

## 3. Spec format

```yaml
episode_id: ep_000417
path: P032                        # from catalog/path_catalog.yaml
persona: persona_07
config: {collage_max: 9, effects: [cool, warm, sepia, black_and_white]}
motivation: "wants a vintage-looking keepsake of the girls' Goa trip"   # for the user simulator only

steps:
  - call: search_images
    args: {people: [daughter], location: Goa, query: "on the beach"}
    outcome: {count: 7}
  - call: apply_effect
    args: {images: r1, effect: sepia}
    outcome: {count: 7}
  - call: make_collage
    args: {images: r2}
    outcome: {status: created}

turns: [[1], [2, 3]]              # step indices per user turn
```

For `ask_gallery`, the filler also writes the answer, so the oracle output is fixed:
```yaml
  - call: ask_gallery
    args: {question: "What is my passport number?"}
    outcome: {answer: "K4829163", count: 1}
```
Selection events appear as steps too: `{event: select, from: r1, count: 5}`.

---

## 4. Spec sampler (code)

For each spec:
1. Pick a catalog path with `status: keep` (uniform for v0).
2. Pick a persona.
3. Sample `config` (collage_max, effect list).
4. Sample happy-path outcomes: search/ask count in `[2, collage_max]` when a collage follows, otherwise `[1, 40]`;
   selection count in `[2, min(count, collage_max)]`; delete = confirmed; album exists or new (50/50).
5. Sample turn grouping: all steps in one message, one step per turn, or mixed.
6. **Sample the skeleton for search args** (see §5) and pass value hints to the filler.

---

## 5. Diversity: code picks the skeleton, LLM writes the content

### Search slot patterns (sampled with quotas)
Leans on people and location, matching production traffic:
- people + query
- people + location
- location only
- people only
- query only
- location + date
- people + location + query
- occasionally all four

### Value hints (sampled from pools)
- location from persona's places / home city
- date phrase style: "last December", "2023", "last weekend", "during Diwali". Personal events ("my birthday",
  "our anniversary") are not date hints: they are photo content and go in `query`.
- person: name, relation, `me`, or named pet
- query category + 3 example queries from the pool

### Query pool categories (starting weights — revise after first generation)
- **Heavy:** actions/activities, documents, relational (people + query)
- **Medium:** scenes, pets (generic), clothing/attributes
- **Light:** general objects

The pool is brainstormed once by an LLM per category, deduplicated (embeddings), and sampled from.
The filler receives examples and writes a *fresh* query, not a copy.

---

## 6. Slot-boundary rules (labeling guidelines — validator enforces)

| User says | people | location | query |
|---|---|---|---|
| "Goa beach photos" | | Goa | beach |
| "me wearing a black suit" | me | | wearing a black suit |
| "Riya dancing at the wedding" | Riya | | dancing at a wedding |
| "my passport" | | | passport |
| "kids playing in the park" | | | kids playing in the park |
| "my daughter with our dog" | daughter | | with a dog |
| "Bruno at the park" | Bruno | | at the park |
| "my dog on the sofa" | | | dog on the sofa |

- Named place → `location`; type of scene → `query`.
- Named person, relation, `me`, or **named pet** → `people`; unnamed person/pet description → `query`.
- Actions and attributes always go in `query`, with people split out.
- Relational queries split: *who* → `people`, *what they're doing / who they're with* → `query`.
- Documents are `query` only.
- **"my" means ownership, not a people filter** ("my passport", "my dog", "my Goa photos").
- `people` is AND across elements; each element is a group (relations expand to OR). No OR across elements in v0.

---

## 7. Intent filler (LLM, one call per spec)

Input: path, persona, config, sampled skeleton + value hints, slot rules, registry model-facing specs.
Output: `motivation` + `args` for every step (+ `answer` for `ask_gallery`), as JSON.

All steps are filled in **one call** so the episode tells one coherent story
(the daughters' Goa photos → sepia → collage, not unrelated arguments per step).

---

## 8. Intent validator (code first, LLM judge last)

Reject the spec if any check fails:
1. Every argument matches the registry schema; handles reference earlier outputs.
2. `effect` ∈ the episode's effect list.
3. `people` values ∈ {`me`} ∪ persona names ∪ persona relations ∪ named pets, after alias → canonical
   normalization via `config/relations.yaml` (same mapping as the tool wrapper).
4. Sampled slot pattern was respected (no extra or missing search args).
5. **Query purity:** `query` contains no names, relations, places or date expressions
   (string checks against persona + gazetteer + date regexes; LLM judge as a second pass).
6. `ask_gallery` question is self-contained (no unresolved pronouns) and has an `answer`.
7. `move_to_album` album name matches the sampled outcome (existing persona album vs new name).

Track rejection reasons by check — a high rate on one check means the filler prompt needs fixing.
---

## Parking list
- **Counts ignore search specificity.** Search counts are uniform in 1..40 regardless of how many filters
  are set, so narrow searches (query + 2 people + location) can return 40+. Refinements only need
  `count < previous`, so some barely narrow (40 → 39). Later: shrink counts as filters are added and
  require a real cut (e.g. ≥ 30%) on refinement.
- **Existing-album ratio is skewed.** Specs sample 50% moves into an existing album, but the filler answers
  "none fits" for most (v1: 11/65 = 17% existing), since personas have only 2–6 albums. Later: sample
  "existing" only when an album plausibly matches the search (its place or person).
- **Festival date hints can clash with the persona.** Region festival lists include minority festivals, so
  e.g. a UK persona gets "during Eid" next to drinking pints. Later: split each region's festivals into
  major vs other and use only major ones for date hints (or give personas their own festivals).
