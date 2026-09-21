# World Simulator — v0 Design (lightweight)

## Why lightweight
The orchestrator only ever sees handles, counts, statuses and short answers — never which images matched.
So the simulator does **not** need a realistic gallery. It needs tool outputs that are
**plausible and consistent within one short conversation.**

Earlier design (full synthetic gallery with images, metadata, resolvers, state diff) was dropped:
it existed only to verify by replaying against a "true" gallery. Instead, we verify the **model's calls**
against the episode spec (see `intent_filler_design.md`), and every tool output comes from the spec.

## Components

### 1. Handle ledger
```python
handles = {
  "r1": {"count": 7,  "kind": "set",    "src": "search_images", "alive": True},
  "r2": {"count": 7,  "kind": "set",    "src": "apply_effect",  "alive": True},
  "r3": {"count": 1,  "kind": "single", "src": "make_collage",  "alive": True},
}
```
- New handles are sequential (`r1`, `r2`, …); `r0` = selection present at conversation start.
- `apply_effect` output: same count as input. `make_collage` output: count 1.
- Selection event: new handle with the selected count; injects `[user selected N photos → rN]`.
- `delete_images` marks the handle dead.

### 2. Outcomes come from the spec
When the teacher calls a tool, the simulator returns the outcome written in the spec step
(count, status, `created`, `ask_gallery` answer). Nothing is rolled at call time.
Outcome ratios are controlled by the spec sampler (quotas), not by randomness in the simulator.

If the teacher makes a call the spec did not plan, a default outcome fills in and the episode is flagged
(usually rejected by the verifier).

### 3. Registry rules on handle counts
- `make_collage` rejects count outside `[2, collage_max]` → `too_many_images`.
- `search_images` count 0 → `no_results` (round 2; v0 is happy path).

### 4. Persona context
Owner, people, relations, named pets, albums — used by the user simulator to sound real and
to keep `move_to_album`'s `created` flag consistent within a conversation.

### 5. Model-facing tool outputs
| Tool | Model sees |
|---|---|
| `search_images` | `{id: rN, count}` |
| `ask_gallery` | `{answer, id: rN, count}` |
| `apply_effect` | `{status: created, images: rN}` |
| `make_collage` | `{status: created, collage: rN}` |
| `move_to_album` | `{status: moved, count, album, created}` |
| `delete_images` | `{status: deleted \| cancelled, count}` |

## Determinism
Everything seeded. The simulator is a pure function of (spec, calls so far), so episodes replay exactly.

## Later
- For RL (free exploration), this ledger + a default outcome sampler is enough; a heavier world can be added then.
- NeMo Gym adapter can wrap simulator + verifier.

## Parking list
- OR searches across different people
- Album name fuzzy matching / duplicate-album prevention
- VQA on a specific open/selected photo
- `select_images` (v2.0: top-k by relevance or aesthetic score)
- Deleting originals after an effect ("keep only the sepia versions")