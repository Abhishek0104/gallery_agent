#!/usr/bin/env bash
# End-to-end pipeline test on a CUDA machine: export -> render check -> LoRA -> serve -> student eval -> verify.
# The goal is to exercise the pipeline, not to tune the model (README "End-to-end training run").
#
#   bash scripts/e2e_cuda.sh              # every stage
#   bash scripts/e2e_cuda.sh prep         # preflight + export + render check + train --check   (no GPU)
#   bash scripts/e2e_cuda.sh train        # LoRA SFT
#   bash scripts/e2e_cuda.sh eval         # start vLLM, run the student on the held-out specs, verify, stop vLLM
#
# Needs: pip install -r requirements-train.txt, vllm on PATH, GEMINI_API_KEY (the user simulator still
# runs on Gemini during eval). Every value below comes from the configs — nothing is hardcoded here.
#
# Note: `prep` re-runs the export, which restamps `git_commit` in the export manifest.json. That one-line
# diff on a clean checkout is expected; the rest of the export reproduces byte for byte.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-python}                                   # e.g. PY=.venv/bin/python
STAGES=${1:-all}
PORT=${PORT:-8000}

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

stage() { [[ "$STAGES" == "all" || "$STAGES" == "$1" ]]; }

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

if stage train; then
  echo "== 3b. LoRA SFT"
  "$PY" -m train.sft_lora
  test -d "$ADAPTER/adapter" || { echo "no adapter at $ADAPTER/adapter"; exit 1; }
fi

if stage eval; then
  echo "== 4. serve $BASE_MODEL + adapter on :$PORT (tool-call parser $PARSER)"
  vllm serve "$BASE_MODEL" --language-model-only --max-model-len "$MAXLEN" \
    --enable-lora --lora-modules "gallery-lora=$ADAPTER/adapter" --max-lora-rank 16 \
    --enable-auto-tool-choice --tool-call-parser "$PARSER" --port "$PORT" &
  VLLM_PID=$!
  trap 'kill $VLLM_PID 2>/dev/null || true' EXIT

  echo "-- waiting for the server (up to 10 min)"
  for _ in $(seq 120); do
    curl -sf "http://localhost:$PORT/v1/models" >/dev/null && break
    kill -0 $VLLM_PID 2>/dev/null || { echo "vllm exited before serving"; exit 1; }
    sleep 5
  done
  "$PY" -m scripts.preflight_cuda --serving

  echo "== 5. student plays the assistant on the held-out specs, then the verifier scores it"
  for f in "$EXPORT_DIR"/eval_ids_*.txt; do
    tag=$(basename "$f" .txt); tag=${tag#eval_ids_}
    echo "-- tag $tag ($(wc -l < "$f") episodes)"
    "$PY" -m realize.run --tag "$tag" --all --only-file "$f" \
          --assistant-role student --out-tag "student_$tag"
    "$PY" -m verify.run --tag "student_$tag"
  done
  echo "== done: data/episodes/verified_student_*.jsonl, reviews in data/episodes/review_student_*.md"
fi
