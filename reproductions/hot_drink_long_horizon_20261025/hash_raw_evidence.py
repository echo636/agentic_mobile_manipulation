"""Freeze hashes of inventoried six-run raw evidence; never modify original runs."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import sys

root = Path(__file__).resolve().parent
inventory_path = root / "RAW_EVIDENCE_INVENTORY.json"
inventory = json.loads(inventory_path.read_text())
expected_total = sum(row["bytes"] for row in inventory["files"])
for number, row in enumerate(inventory["files"], 1):
    path = Path(row["source"])
    before = path.stat()
    if before.st_size != row["bytes"]:
        raise RuntimeError(f"Evidence size changed: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise RuntimeError(f"Evidence changed while hashing: {path}")
    value = digest.hexdigest()
    if row.get("sha256") and row["sha256"] != value:
        raise RuntimeError(f"Evidence differs from previous hash: {path}")
    row["sha256"] = value
    if number % 2000 == 0:
        print(f"Hashed {number}/{len(inventory['files'])} files", flush=True)
inventory["hash_capture_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
inventory["hash_scope"] = "Current six finished runs; source files read-only, size and mtime stable while each file was read. Hash capture is not a historical attestation."
inventory["total_bytes"] = expected_total
temporary = inventory_path.with_suffix(".tmp")
temporary.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
os.replace(temporary, inventory_path)
print(f"PASS: {len(inventory['files'])} files, {expected_total} bytes hashed; originals unchanged")
