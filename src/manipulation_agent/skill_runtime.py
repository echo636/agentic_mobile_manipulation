"""Freeze human-authored workflow documents; expose only an explicit resource allowlist."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
from .contracts import SkillError
from .records import write_json

class SkillLibrary:
    def __init__(self, output: Path):
        root = Path(__file__).resolve().parents[2] / "skills"
        if not root.is_dir():
            root = Path(sys.prefix) / "share/manipulation-agentic-system/skills"
        self.resources = {}; self.entries = []
        digest = hashlib.sha256()
        for main in sorted(root.glob("*/SKILL.md")):
            name = main.parent.name
            files = [main, *sorted((main.parent / "references").glob("*.md"))]
            text = main.read_text()
            desc = next((line.split(":",1)[1].strip() for line in text.splitlines() if line.startswith("description:")), "")
            names = []
            for path in files:
                if path.is_symlink(): raise ValueError("Skill symlinks are not allowed")
                relative = str(path.relative_to(main.parent)); data = path.read_bytes()
                if len(data) > 100000: raise ValueError("Skill document too large")
                self.resources[(name, relative)] = data.decode()
                key = name + "/" + relative
                digest.update(key.encode()); digest.update(data)
                names.append(relative)
                destination = output / "skill_snapshot" / key
                destination.parent.mkdir(parents=True,exist_ok=True); destination.write_bytes(data)
            self.entries.append({"name":name,"description":desc,"resources":names})
        if not self.entries: raise RuntimeError("No packaged workflow skills found")
        self.digest = digest.hexdigest()
        write_json(output / "skill_manifest.json", {"bundle_sha256":self.digest,"skills":self.entries})

    def catalog(self):
        return self.entries

    def read(self, name, resource):
        if (name, resource) not in self.resources:
            raise SkillError("unknown_skill_resource", "Choose a name/resource from list_skills")
        return {"name":name,"resource":resource,"text":self.resources[(name,resource)],"bundle_sha256":self.digest}
