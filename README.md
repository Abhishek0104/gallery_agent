# Gallery Agent — synthetic data pipeline

Synthetic training/eval data for an on-device (< 1B, English-only) tool-calling gallery assistant.
Meaning first, wording last: specs fix tool calls, arguments and outcomes before any dialogue is written;
LLMs only make it natural; a code verifier checks every conversation against its spec.
Design docs are in `docs/` (start with `CLAUDE.md`).

## Setup
```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
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
| `anthropic` | JSON only | `ANTHROPIC_API_KEY` |

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
