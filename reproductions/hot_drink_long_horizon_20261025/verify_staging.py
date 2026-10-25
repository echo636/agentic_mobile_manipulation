"""Verify the complete staged file set; release readiness is a separate condition."""
from pathlib import Path
import hashlib
import json
import sys

root = Path(__file__).resolve().parent
stage = root / "package_staging"
manifest = json.loads((root / "STAGING_MANIFEST.json").read_text())
actual = {str(path.relative_to(stage)) for path in stage.rglob("*") if path.is_file()}
expected = set(manifest["files"])
missing, extra = sorted(expected - actual), sorted(actual - expected)
bad = []
for relative in sorted(expected & actual):
    path = stage / relative
    record = manifest["files"][relative]
    if path.stat().st_size != record["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        bad.append(relative)
if missing or extra or bad:
    print(json.dumps({"missing": missing, "extra": extra, "changed": bad}, ensure_ascii=False))
    raise SystemExit(1)
print(f"PASS: exactly {len(expected)} staged files match their sizes and hashes")
inventory = json.loads((root / "INVENTORY.json").read_text())
print("publication_ready:", inventory["publication_ready"])
if "--require-publish-ready" in sys.argv and not inventory["publication_ready"]:
    raise SystemExit(2)
