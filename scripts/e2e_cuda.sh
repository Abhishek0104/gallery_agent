#!/usr/bin/env bash
# End-to-end pipeline test on a CUDA machine: export -> render check -> LoRA -> serve -> student eval -> verify.
# The goal is to exercise the pipeline, not to tune the model (README "End-to-end training run").
#
#   bash scripts/e2e_cuda.sh              # every stage
#   bash scripts/e2e_cuda.sh prep         # preflight + export + render check + train --check   (no GPU)
#   bash scripts/e2e_cuda.sh baseline     # teacher-forced eval of the UNTRAINED base  (run before training)
#   bash scripts/e2e_cuda.sh train        # LoRA SFT
#   bash scripts/e2e_cuda.sh eval         # teacher-forced eval of the adapter, then vLLM + interactive eval
#
# Needs: pip install -r requirements-train.txt, vllm on PATH. No API key: the eval's user simulator and embedding
# model are local by default (LLM_OVERLAY=config/llm_local.yaml — a second vLLM server + in-process embeddings).
#   LLM_OVERLAY= bash scripts/e2e_cuda.sh eval    # user simulator + embeddings on Gemini (needs GEMINI_API_KEY)
#   LIMIT=5 bash scripts/e2e_cuda.sh eval         # only the first 5 held-out episodes per tag — try this first
# Every value below comes from the configs — nothing is hardcoded here.
#
# Note: `prep` re-runs the export, which restamps `git_commit` in the export manifest.json. That one-line
# diff on a clean checkout is expected; the rest of the export reproduces byte for byte.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-python}                                   # e.g. PY=.venv/bin/python
STAGES=${1:-all}
PORT=${PORT:-8000}
export LLM_OVERLAY=${LLM_OVERLAY-config/llm_local.yaml}   # set but empty = config/llm.yaml only (Gemini)
LIMIT=${LIMIT:-}

read -r VERSION BASE_MODEL ADAPTER PARSER MAXLEN < <(
  "$PY" - <<'EOF'
import yaml
exp = yaml.safe_load(open("config/export.yaml"))
trn = yaml.safe_load(open("config/train.yaml"))
print(exp["version"], trn["base_model"], trn["output_dir"],
      trn["tool_call_parser"], trn["serve_max_model_len"])
EOF
)
EXPORT_DIR="data/export/$VERSION"
echo "== $VERSION | base $BASE_MODEL | adapter $ADAPTER | parser $PARSER | stages: $STAGES"
echo "== LLM overlay: ${LLM_OVERLAY:-none (config/llm.yaml)}"

stage() { [[ "$STAGES" == "all" || "$STAGES" == "$1" ]]; }

PIDS=()
trap 'for p in ${PIDS[@]+"${PIDS[@]}"}; do kill "$p" 2>/dev/null || true; done' EXIT

wait_for() {  # port pid name
  echo "-- waiting for $3 on :$1 (up to 10 min)"
  for _ in $(seq 120); do
    curl -sf "http://localhost:$1/v1/models" >/dev/null && return 0
    kill -0 "$2" 2>/dev/null || { echo "$3: vllm exited before serving"; exit 1; }
    sleep 5
  done
  echo "$3: not serving after 10 min"; exit 1
}

if stage prep; then
  echo "== 0. preflight"
  "$PY" -m scripts.preflight_cuda
  echo "== 1. export"
  "$PY" -m export.build_export
  echo "== 2. render check"
  "$PY" -m export.render_check
  echo "== 3a. training data + masking (no model)"
  "$PY" -m train.sft_lora --check
fi

if stage baseline; then
  # Cheap, no server, no API. Without it a trained number has nothing to be compared against.
  echo "== 3a'. teacher-forced eval of the untrained base"
  "$PY" -m export.eval_forced
fi

if stage train; then
  echo "== 3b. LoRA SFT"
  "$PY" -m train.sft_lora
  test -d "$ADAPTER/adapter" || { echo "no adapter at $ADAPTER/adapter"; exit 1; }
fi

if stage eval; then
  echo "== 3c. teacher-forced eval of the adapter (no server, no API)"
  "$PY" -m export.eval_forced --adapter "$ADAPTER/adapter"

  # Servers the overlay asks for, "role model port gpu_fraction max_len devices" per line (preflight --serve-plan).
  PLAN=()
  while IFS= read -r l; do PLAN+=("$l"); done < <("$PY" -m scripts.preflight_cuda --serve-plan)
  STUDENT_GPU=$(printf '%s\n' ${PLAN[@]+"${PLAN[@]}"} | awk '$1=="student" && $4!="-" {print $4}')

  echo "== 4. serve $BASE_MODEL + adapter on :$PORT (tool-call parser $PARSER)"
  vllm serve "$BASE_MODEL" --language-model-only --max-model-len "$MAXLEN" \
    --enable-lora --lora-modules "gallery-lora=$ADAPTER/adapter" --max-lora-rank 16 \
    --enable-auto-tool-choice --tool-call-parser "$PARSER" --port "$PORT" \
    ${STUDENT_GPU:+--gpu-memory-utilization "$STUDENT_GPU"} < /dev/null &
  PIDS+=($!)
  wait_for "$PORT" "$!" student

  # One at a time: two vLLM processes profiling memory on the same GPU at once can both fail.
  for line in ${PLAN[@]+"${PLAN[@]}"}; do
    read -r role model port frac maxlen devices <<< "$line"
    [[ "$role" == "student" ]] && continue
    args=(--port "$port")
    [[ "$frac" != "-" ]] && args+=(--gpu-memory-utilization "$frac")
    [[ "$maxlen" != "-" ]] && args+=(--max-model-len "$maxlen")
    echo "== 4b. serve $model for $role on :$port"
    if [[ "$devices" != "-" ]]; then
      CUDA_VISIBLE_DEVICES="$devices" vllm serve "$model" "${args[@]}" < /dev/null &
    else
      vllm serve "$model" "${args[@]}" < /dev/null &
    fi
    PIDS+=($!)
    wait_for "$port" "$!" "$role"
  done
  "$PY" -m scripts.preflight_cuda --serving

  echo "== 5. student plays the assistant on the held-out specs, then the verifier scores it"
  for f in "$EXPORT_DIR"/eval_ids_*.txt; do
    tag=$(basename "$f" .txt); tag=${tag#eval_ids_}
    echo "-- tag $tag ($(wc -l < "$f") episodes)"
    "$PY" -m realize.run --tag "$tag" --all --only-file "$f" \
          --assistant-role student --out-tag "student_$tag" ${LIMIT:+--limit "$LIMIT"}
    "$PY" -m verify.run --tag "student_$tag"
  done
  echo "== done"
  echo "   teacher-forced: $EXPORT_DIR/forced_eval_{base,$(basename "$ADAPTER")}.{json,jsonl,md}  (.md = the misses)"
  echo "   interactive:    data/episodes/verified_student_*.jsonl, reviews in review_student_*.md"
  if [[ -n "$LLM_OVERLAY" ]]; then
    echo "   overlay: user simulator + embeddings from $LLM_OVERLAY — not comparable with runs that used Gemini, and"
    echo "         the 0.75 query/question thresholds were tuned on gemini-embedding-2 (README \"Local models for eval\")"
  fi
  echo "   note: verify.run's accept flag is a data filter — every hard check must pass. For a model, read"
  echo "         the mean score and the per-group means (README / docs/verifier_design.md)."
fi
