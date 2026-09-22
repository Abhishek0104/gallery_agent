"""
Teacher-forced eval scoring (export/eval_forced.py), with no model: the gold completion fed back in as the
prediction must score 1.0 on every metric. That exercises parse -> normalize -> compare, so a bug there is
never mistaken for a weak model. Mutations then check each metric actually fails when it should.
"""
import json

from export.eval_forced import aggregate, review, run, score_row

ROWS = [json.loads(l) for l in open("data/export/e2e_v2/sft_eval.jsonl")]
TOOLS = {json.loads(l)["id"]: json.loads(l)["tools"] for l in open("data/export/e2e_v2/eval.jsonl")}


def gold_predictor(row):
    return row["completion"], None


def test_gold_scores_perfectly():
    """Every row of the real eval split, scored against itself."""
    report, scored, preds = run(ROWS, TOOLS, lambda prompt, gold: (gold, None))
    for metric, v in report["overall"].items():
        expect = 0.0 if metric in ("truncated", "malformed") else 1.0
        assert v["rate"] == expect, f"{metric}: {v}"
    assert report["overall"]["structural"]["n"] == sum(r["kind"] == "call" for r in ROWS)
    assert "completion_loss" not in report          # no model, no loss
    assert len(preds) == len(ROWS) and all(p["ok"] for p in preds)
    assert all(p["gold"] == p["pred"] for p in preds)


def test_metrics_fail_on_the_right_mutations():
    call = next(r for r in ROWS if r["kind"] == "call" and "<parameter=images>" in r["completion"])
    tools = TOOLS[call["id"]]

    wrong_tool = call["completion"].replace("<function=", "<function=x_", 1)
    assert not score_row(call, wrong_tool, tools)["name"]

    wrong_handle = call["completion"].replace("\nr0\n", "\nr9\n")
    if wrong_handle != call["completion"]:
        m = score_row(call, wrong_handle, tools)
        assert m["name"] and not m["images"] and not m["structural"]

    reply = next(r for r in ROWS if r["kind"] == "reply")
    assert not score_row(reply, call["completion"], tools)["kind"]          # called instead of replying
    assert not score_row(reply, "<|im_end|>", tools)["reply_nonempty"]
    assert not score_row(reply, "here they are: r3<|im_end|>", tools)["reply_no_handles"]
    assert score_row(reply, "here they are<|im_end|>", tools)["reply_no_handles"]
    assert score_row(reply, "here they are", tools)["truncated"]            # no <|im_end|>


def test_normalized_args_are_compared_like_the_verifier():
    """An exactly-compared arg matches under the arg-type normalizer (config/arg_types.yaml)."""
    row = next((r for r in ROWS if "<parameter=location>" in r["completion"]), None)
    if row is None:
        return
    pred = row["completion"].replace("<parameter=location>\n", "<parameter=location>\nThe ", 1)
    assert score_row(row, pred, TOOLS[row["id"]])["args"]    # strip_leading_the


def test_aggregate_groups_by_kind_and_tool():
    _, scored, _ = run(ROWS[:40], TOOLS, lambda prompt, gold: (gold, None))
    report = aggregate(scored)
    assert set(report["by_kind"]) <= {"call", "reply"}
    assert all(t["kind"]["rate"] == 1.0 for t in report["by_tool"].values())


def test_review_lists_every_miss_with_gold_and_prediction():
    """A wrong prediction on every row: the review names each one and shows both sides."""
    _, _, preds = run(ROWS[:12], TOOLS, lambda prompt, gold: ("<|im_end|>", None))
    meta = {"base_model": "m", "adapter": None, "version": "v", "split": "eval",
            "device": "cpu", "dtype": "float32"}
    md = review(preds, meta)
    assert f"{len(preds)} of {len(preds)} turns missed" in md
    for p in preds:
        assert f"{p['id']} turn {p['turn']}" in md
        assert p["gold"].strip() in md
