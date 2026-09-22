"""Golden tests: the refactor gate. Every artifact the code produces from the committed data must be unchanged,
and independent of Python's hash seed (set iteration order). Regenerate golden.json only for an intended change:
    python -m tests.golden.snapshot --write
"""
import json
import os
import subprocess
import sys
from pathlib import Path

GOLDEN = json.loads((Path(__file__).parent / "golden" / "golden.json").read_text())


def run_snapshot(hash_seed):
    out = subprocess.run([sys.executable, "-m", "tests.golden.snapshot"], capture_output=True, text=True,
                         env={**os.environ, "PYTHONHASHSEED": str(hash_seed)},
                         cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_golden_snapshot_unchanged_across_hash_seeds():
    for seed in (0, 1, 2):
        snap = run_snapshot(seed)
        changed = sorted(k for k in GOLDEN if snap.get(k) != GOLDEN[k])
        assert not changed, f"PYTHONHASHSEED={seed}: changed artifacts {changed}"
        assert set(snap) == set(GOLDEN), f"artifacts added/removed: {set(snap) ^ set(GOLDEN)}"
