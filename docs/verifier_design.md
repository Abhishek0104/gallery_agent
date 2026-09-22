# Verifier — v0 Design

## Purpose
Score a realized episode against its spec. The spec is ground truth, so verification is code: the teacher's
tool calls and replies are compared to what the spec planned. Output is a **score in [0, 1] per check plus an
overall score and an accept flag**, so the same code can serve as an RL reward later. A faithfulness /
naturalness LLM judge comes later and adds checks; it does not replace these.

## Inputs
An episode record (`data/episodes/*.jsonl`): the spec, messages (user, assistant with tool calls, tool
results), realization flags. The verifier **replays the simulator** from the messages (selection lines +
tool calls) to rebuild the spec-handle → conversation-handle map; it does not trust realization's bookkeeping.

## Checks
| Group | Check | Score | Hard (blocks accept) |
|---|---|---|---|
| calls | planned vs actual tool sequence (aligned in order) | matched / max(planned, actual) | yes: must be 1.0 |
| args | handles: `images` is the handle the spec step reads | fraction correct | yes |
| args | people (set, after alias normalization), location (without a leading "the"), date (without leading preposition), effect, album | fraction of args exact | yes |
| query | teacher `query` vs spec `query`: embedding cosine | mean cosine | yes: each ≥ threshold |
| query | teacher `query` is pure (shared purity check, persona-aware, people-subject rule) | fraction pure | yes |
| ask | teacher `question` vs spec question: cosine; self-contained | mean | yes: ≥ threshold, self-contained |
| replies | `ask_gallery` answer relayed: every number / code in the answer appears in the next reply | fraction | yes |
| replies | every user turn ends with a non-empty text reply | fraction | yes |
| replies | no conversation ids (`r2`) in replies | fraction | yes |
| replies | delete replies say something was deleted | fraction | no |
| replies | grounded numbers: every number in a reply appears in a tool result or user message so far | fraction | no |
| flags | realization flags (unplanned call, bad handle, incomplete, off-script) | 1 if none | yes |

Overall score = weighted mean of the group scores (`config/verify.yaml`). **accept** = every hard check passes.

## Output
`data/episodes/verified_v0.jsonl` (one verdict per episode: scores, accept, failure messages) and a printed
summary with failures by check, so a high failure rate on one check points at the prompt or code to fix.

## Thresholds
Query and question similarity thresholds start at 0.75 (`config/verify.yaml`). Look at the score distribution
on the first batch before tuning.

## Parking list
- LLM judge: faithfulness (reply matches tool results) and naturalness (user and assistant turns).
- Offline query similarity: `config/llm.yaml` has a `local` embeddings provider, so verification without an API
  is reachable — but the 0.75 threshold was set against `gemini-embedding-2` and would have to be re-tuned for
  a different embedding model. Until then `verify.run` needs the API even when the model under eval is local.
- Partial credit for recovering from a wrong call (needs error / recovery specs first).
