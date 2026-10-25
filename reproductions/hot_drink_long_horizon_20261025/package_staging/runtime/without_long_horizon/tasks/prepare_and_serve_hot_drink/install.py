"""Install or verify the task files in an external BEHAVIOR-1K checkout."""

import argparse
from pathlib import Path
import shutil

import yaml


HERE = Path(__file__).resolve().parent
TASK = "prepare_and_serve_hot_drink"
SCENE_JSON = Path("datasets/2026-challenge-task-instances/scene_test/public/house_single_floor/json")
METADATA = Path("datasets/2026-challenge-task-instances/metadata/available_tasks.yaml")


def install(behavior_root: Path, *, check: bool, update: bool = False) -> None:
    if not (behavior_root / "OmniGibson").is_dir():
        raise ValueError(f"Expected a BEHAVIOR-1K checkout: {behavior_root}")

    files = {
        HERE / "problem0.bddl": behavior_root / "bddl3/bddl/activity_definitions" / TASK / "problem0.bddl",
    }
    for source in (HERE / "scene").rglob("*.json"):
        files[source] = behavior_root / SCENE_JSON / source.relative_to(HERE / "scene/house_single_floor/json")

    for source, destination in files.items():
        if destination.exists():
            if source.read_bytes() != destination.read_bytes():
                if not update or check:
                    raise ValueError(f"Existing file differs; inspect before replacing: {destination}")
                shutil.copy2(source, destination)
                print(f"UPDATE  {destination}")
                continue
            print(f"OK  {destination}")
        elif check:
            raise FileNotFoundError(f"Missing: {destination}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            print(f"ADD {destination}")

    manifest = behavior_root / METADATA
    entry_text = (HERE / "available_tasks_entry.yaml").read_text()
    entry = yaml.safe_load(entry_text)[TASK]
    catalog = yaml.safe_load(manifest.read_text())
    if TASK in catalog:
        if catalog[TASK] != entry:
            if not update or check:
                raise ValueError(f"Existing task entry differs; inspect before replacing: {manifest}")
            catalog[TASK] = entry
            manifest.write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False))
            print(f"UPDATE  {manifest}: {TASK}")
            return
        print(f"OK  {manifest}: {TASK}")
    elif check:
        raise ValueError(f"Missing task entry in {manifest}: {TASK}")
    else:
        ends_with_newline = manifest.read_bytes().endswith(b"\n")
        with manifest.open("a") as stream:
            if not ends_with_newline:
                stream.write("\n")
            stream.write(entry_text)
        print(f"ADD {manifest}: {TASK}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--behavior-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Verify without changing files")
    parser.add_argument("--update", action="store_true", help="Replace this task's existing files and catalog entry")
    args = parser.parse_args()
    install(args.behavior_root.resolve(), check=args.check, update=args.update)


if __name__ == "__main__":
    main()
