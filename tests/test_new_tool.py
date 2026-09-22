"""Acceptance test: a new tool defined only in YAML flows through every stage with no Python edits."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_yaml_only_tool_flows_through_the_pipeline(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    for f in (ROOT / "registry").glob("*.yaml"):
        shutil.copy(f, registry / f.name)
    shutil.copy(ROOT / "tests" / "fixtures" / "favorite_images.yaml", registry / "favorite_images.yaml")
    env = {**os.environ, "GALLERY_AGENT_REGISTRY": str(registry), "GALLERY_AGENT_CATALOG": str(tmp_path / "paths.yaml")}
    out = subprocess.run([sys.executable, "-m", "tests.acceptance_new_tool"], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-3000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["paths_with_tool"] > 0 and r["paths_total"] > 86          # the catalog grew by the tool's paths
    assert r["accepted"] == r["specs"] == r["paths_with_tool"]          # every spec with it realized and verified
    assert r["declaration"]["name"] == "favorite_images" and "images" in r["declaration"]["parameters"]["required"]
    assert r["guidance_has_tool"]
    assert any("as favorites" in x for x in r["requests"])
