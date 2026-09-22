# Gallery Agent — synthetic data pipeline

Synthetic training/eval data for an on-device (< 1B, English-only) tool-calling gallery assistant.
Meaning first, wording last: specs fix tool calls, arguments and outcomes before any dialogue is written;
LLMs only make it natural; a code verifier checks every conversation against its spec.
**How to use it: [`docs/usage.md`](docs/usage.md).** Design docs are in `docs/` (start with `CLAUDE.md`).

## Setup
```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt   # + requirements-train.txt for export/train
echo 'GEMINI_API_KEY=...' > .env          # git-ignored; llm.py loads it
.venv/bin/python -m pytest -q tests
```

## Pipeline
```bash
python -m specs.make_personas                                   # 20 personas            -> data/personas/
python -m specs.make_query_pool                                 # query pool             -> data/query_pool.json
python -m specs.make_specs --n 200 --seed 101 --tag v2          # happy-path specs       -> data/specs/specs_v2.jsonl
python -m specs.make_specs --round2 --tag r2                    # round-2 scenario specs -> data/specs/specs_r2.jsonl
python -m realize.run --tag v2 --all                            # dialogues              -> data/episodes/episodes_v2.jsonl
python -m verify.run --tag v2 --quiet                           # verdicts               -> data/episodes/verified_v2.jsonl
```
Every LLM call is cached under `data/cache/llm/` (git-ignored), keyed by provider, model, prompt and seed, so
reruns are free and resume where a failed run stopped.

## LLM providers
Each role in `config/llm.yaml` (generation, filler, user_sim, teacher, embedding, judge) picks its own provider:

| provider | use | notes |
|---|---|---|
| `gemini` (default) | all roles | `GEMINI_API_KEY` |
| `openai_compatible` | JSON, tool-calling chat, embeddings | any OpenAI-compatible server, e.g. vLLM: `base_url` + `model` |
| `local` | embeddings only | in-process `sentence-transformers` (optional install) |

Commented examples for `openai_compatible` and `local` are at the end of `config/llm.yaml`.

## Serving a model with vLLM
The `openai_compatible` provider sends `tools=[...]` with `tool_choice="auto"` for the teacher, and
`response_format={"type": "json_schema", ...}` for structured outputs (filler, user simulator).

**Tool calling needs two server flags** — without them vLLM rejects `tool_choice="auto"`:
```bash
vllm serve Qwen/Qwen3-8B \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --max-model-len 16384 \
  --port 8000
```
Pick `--tool-call-parser` for the model family (from the vLLM tool-calling docs):

| model family | `--tool-call-parser` | `--chat-template` |
|---|---|---|
| Qwen 2.5 / Qwen 3, Hermes 2 Pro+, QwQ | `hermes` | not needed |
| Qwen3-Coder | `qwen3_xml` | not needed |
| Llama 3.1 / 3.2 | `llama3_json` | needed (vLLM ships `examples/tool_chat_template_llama3.1_json.jinja`) |
| Llama 4 | `llama4_pythonic` | needed (examples provided) |
| Mistral 7B+ | `mistral` | needed (examples provided) |
| gpt-oss 20B / 120B | `openai` | not needed |
| DeepSeek-V3 / R1, V3.1 | `deepseek_v3`, `deepseek_v31` | needed |
| GLM-4.5 / 4.6 | `glm45` | not needed |
| Granite 3.x / 4.0 | `granite` | some models |
| FunctionGemma 270M | `functiongemma` | needed |

Notes:
- Llama 3.x parsers don't support parallel tool calls; the pipeline doesn't need them (calls are sequential).
- Reasoning models: add `--reasoning-parser <name>` so thinking is kept out of `content`, or turn thinking off
  per request with `extra_body: {chat_template_kwargs: {enable_thinking: false}}` (Qwen3).
- Structured outputs are on by default (`--structured-outputs-config.backend auto`); no flag needed.
- `--api-key <key>` makes the server require a key; then set `api_key_env` for the role.
- `--served-model-name` changes the name to put in `model:`.

**Embeddings** (query-pool dedup, verifier query similarity) — serve an embedding model on another port; vLLM
detects pooling models automatically (`--runner pooling --convert embed` forces it):
```bash
vllm serve BAAI/bge-small-en-v1.5 --port 8001
```
Check flags against `vllm serve --help` for your vLLM version; parser names change between releases.

## End-to-end training run (export → render check → LoRA → eval)
Code is set up and dry-checked; training and serving run on a CUDA machine. Pipeline test, not a tuned model.

```bash
# 1. export verified v2 + r2 episodes, split by held-out persona       (config/export.yaml)
python -m export.build_export          # -> data/export/e2e_v1/{train,eval}.jsonl, eval_ids_*.txt, manifest.json
# 2. render every assistant turn with the Qwen3.5 template (non-thinking) and check it round-trips
python -m export.render_check          # -> sft_{train,eval}.jsonl, render_report.json   (tokenizer only)
# 3. LoRA SFT, loss on completions only                                   (config/train.yaml)
python -m train.sft_lora --check       # data + masking only; safe anywhere
python -m train.sft_lora               # CUDA: adapter -> runs/e2e_v1_lora/adapter
# 4. serve base + adapter with tool calling (text only, non-thinking by default for 0.8B)
vllm serve Qwen/Qwen3.5-0.8B --language-model-only --max-model-len 8192 \
  --enable-lora --lora-modules gallery-lora=runs/e2e_v1_lora/adapter --max-lora-rank 16 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --port 8000
# 5. the trained model plays the assistant on the held-out specs (no teacher guidance), then the verifier scores it
for t in v2 r2; do
  python -m realize.run --tag $t --all --only-file data/export/e2e_v1/eval_ids_$t.txt \
         --assistant-role student --out-tag student_$t
  python -m verify.run --tag student_$t
done
```
- The `student` role in `config/llm.yaml` points at `http://localhost:8000/v1`, model `gallery-lora`, greedy
  (`temperature: 0`), `enable_thinking: false` — the same rendering as training. Set `model:
  Qwen/Qwen3.5-0.8B` to evaluate the untrained base as a baseline.
- `--tool-call-parser qwen3_coder` is from the Qwen3.5 model card; the template emits XML-style calls
  (`<function=...><parameter=...>`), which that parser converts by the tools' JSON schema (strings stay strings).
- The user simulator still runs on Gemini during eval; its calls miss the cache once the student's replies differ
  from the teacher's (~3 calls per held-out episode).
