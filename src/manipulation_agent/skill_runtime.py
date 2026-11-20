"""Freeze workflow documents and matching reviewed experience before the episode."""
import hashlib
from pathlib import Path
import sys
from .contracts import SkillError
from .experience_skills import ExperienceStore, configured_root
from .records import write_json

class SkillLibrary:
    def __init__(self, output: Path, *, exclude=(), include=None, task=None,
                 protocol=None, experience_root=None):
        root = Path(__file__).resolve().parents[2] / "skills"
        if not root.is_dir():
            root = Path(sys.prefix) / "share/manipulation-agentic-system/skills"
        self.resources = {}; self.entries = []; self.task = task
        digest = hashlib.sha256()

        def freeze(name, path, resource, *, public=True):
            if path.is_symlink(): raise ValueError("Skill symlinks are not allowed")
            data = path.read_bytes()
            if len(data) > (100000 if public else 1000000):
                raise ValueError("Skill resource too large")
            if public: self.resources[(name, resource)] = data.decode()
            key = name + "/" + resource
            digest.update(key.encode()); digest.update(data)
            destination = output / "skill_snapshot" / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

        for main in sorted(root.glob("*/SKILL.md")):
            name = main.parent.name
            if name in exclude or (include is not None and name not in include): continue
            files = [main, *sorted((main.parent / "references").glob("*.md"))]
            desc = next((line.split(":",1)[1].strip() for line in main.read_text().splitlines()
                         if line.startswith("description:")), "")
            names = []
            for path in files:
                relative = str(path.relative_to(main.parent))
                freeze(name, path, relative); names.append(relative)
            self.entries.append({"name":name,"description":desc,"resources":names})

        selected = []
        experience_root = experience_root if experience_root is not None else configured_root()
        if protocol is not None and experience_root is not None:
            selected = ExperienceStore(experience_root).select(task, protocol)
            for entry in selected:
                name = entry['name']
                if any(row['name'] == name for row in self.entries):
                    raise ValueError("Experience name collides with a packaged workflow skill")
                freeze(name, entry['directory'] / 'SKILL.md', 'SKILL.md')
                freeze(name, entry['directory'] / 'record.json', 'provenance.json', public=False)
                self.entries.append({k:entry[k] for k in
                    ('name','description','kind','key','protocol','revision','action_keys')})
                self.entries[-1]['resources'] = ['SKILL.md']
        if not self.entries: raise RuntimeError("No packaged workflow skills found")
        self.digest = digest.hexdigest()
        self.selection = {'task':task,'protocol':protocol,
                          'task_skill':next((e['name'] for e in selected if e['kind']=='task'),None),
                          'action_skills':[e['name'] for e in selected if e['kind']=='action'],
                          'status':'available' if any(e['kind']=='task' for e in selected) else 'no_task_experience'}
        write_json(output / "skill_manifest.json", {"bundle_sha256":self.digest,
                   "skills":self.entries,"experience_selection":self.selection})

    def catalog(self):
        return self.entries

    def recommendations(self):
        return dict(self.selection)

    def read(self, name, resource):
        if (name, resource) not in self.resources:
            raise SkillError("unknown_skill_resource", "Choose a name/resource from list_skills")
        return {"name":name,"resource":resource,"text":self.resources[(name,resource)],"bundle_sha256":self.digest}
