"""Shared geometry source history; draft collection does not require robot trials.

Programs still execute only through the offline geometry sandbox. Episodes use
local source snapshots, so publication cannot change an active episode's code.
"""
import ast
from copy import deepcopy
import hashlib
import json
import re

from .program_library import ProgramLibrary
from .records import now, write_json


def checked_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
        raise ValueError('Use a lowercase geometry program identifier')
    return name


def checked_source(source):
    if not isinstance(source, str) or len(source.encode()) > 100000:
        raise ValueError('Python source must fit 100000 bytes')
    tree = ast.parse(source)
    if not any(isinstance(node, ast.FunctionDef) and node.name == 'generate' for node in tree.body):
        raise ValueError('Define generate(task, oracle, parameters)')
    return hashlib.sha256(source.encode()).hexdigest()


class GeometryProgramLibrary:
    def __init__(self, root):
        self.library = ProgramLibrary(root)
        self.root = self.library.root / 'geometry_programs'

    def folder(self, name):
        path = self.root / checked_name(name)
        if any(p.is_symlink() for p in (self.library.repository, self.library.root, self.root, path)):
            raise ValueError('Geometry library paths cannot be symlinks')
        return path

    def _read(self, name):
        folder = self.folder(name)
        metadata = folder / 'metadata.json'
        if not metadata.exists():
            return None
        if metadata.is_symlink() or (folder / 'source.py').is_symlink():
            raise ValueError('Geometry sources cannot be symlinks')
        row = json.loads(metadata.read_text())
        source = (folder / 'source.py').read_text()
        if (row['program'] != name or checked_source(source) != row['source_sha256']
                or type(row['version']) is not int or row['version'] < 1):
            raise ValueError('Geometry library identity/source mismatch')
        evidence = folder / 'evidence/runs.jsonl'
        if evidence.exists():
            runs = [json.loads(line) for line in evidence.read_text().splitlines() if line.strip()]
            runs = [run for run in runs if run.get('source_sha256') == row['source_sha256']]
            passed = sum(run.get('fixed_program_native_passed') is True for run in runs)
            row['recorded_validation'] = {'runs': len(runs), 'native_passed_runs': passed}
            if passed:
                row['validation'] = f"{passed} recorded native passes; certification status: {row['status']}"
        return {**row, 'source': source}

    def _release(self, entry, commit):
        return {**entry, 'library_release': {
            'program': entry['program'], 'version': entry['version'],
            'source_sha256': entry['source_sha256'],
            'library_root': str(self.library.repository), 'library_commit': commit,
            'release_tag': entry['release_tag']}}

    def snapshot(self):
        """Read one clean, locked Git revision, including usable drafts."""
        with self.library.locked():
            if not self.library.git.head():
                return []
            self.library.git.require_clean()
            commit = self.library.git.head()
            entries = [self._read(p.name) for p in sorted(self.root.iterdir()) if p.is_dir()] if self.root.exists() else []
            return [self._release(row, commit) for row in entries
                    if row is not None and row.get('status') != 'deprecated']

    def _index(self):
        rows = ['# Reusable geometry programs', '',
                'Read and reuse applicable drafts; improve the existing program when needed.', '',
                '| Program | Version | Status | Description |', '| --- | --- | --- | --- |']
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir():
                continue
            row = self._read(folder.name)
            if row is not None:
                description = ' '.join(row.get('description', '').split()).replace('|', '/')
                rows.append(f"| [{folder.name}](geometry_programs/{folder.name}/SKILL.md) | "
                            f"{row['version']} | {row['status']} | {description} |")
        (self.library.root / 'GEOMETRY_INDEX.md').write_text('\n'.join(rows)+'\n')

    def publish(self, entry, expected_sha256):
        """Collect syntax-valid code immediately, preserving authorship and history."""
        name = checked_name(entry['program'])
        sha = checked_source(entry['source'])
        if entry['source_sha256'] != sha:
            raise ValueError('Geometry source hash mismatch')
        with self.library.locked():
            self.library.git.initialize()
            if not self.library.git.head():
                self.library.git.commit('Initialize local executable program library')
            self.library.git.require_clean()
            previous = self._read(name)
            if previous is not None and previous['source_sha256'] == sha:
                return self._release(previous, self.library.git.head())
            if previous is not None and previous['source_sha256'] != expected_sha256:
                raise ValueError('Shared program changed; preserve the local draft and reconcile its current hash')
            version = previous['version']+1 if previous else 1
            tag_name = 'geometry-'+name
            tag = f'oracle_waypoints/{tag_name}/v{version:06d}'
            if self.library.git.command('tag', '--list', tag).stdout.strip():
                raise ValueError('Geometry release tag already exists')
            row = {k: deepcopy(entry[k]) for k in ('program', 'source_sha256', 'description', 'author', 'provenance')
                   if k in entry}
            row.update(schema_version=1, kind='geometry_program', version=version,
                status='draft', interface='generate(task, oracle, parameters) -> Pose phases (EEF or whole-body joints) or EEF candidates',
                validation='Collected for reuse; physical validation is recorded separately',
                parent_source_sha256=expected_sha256, published_at=now(), release_tag=tag,
                library_parent_commit=self.library.git.head())
            folder = self.folder(name)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'source.py').write_text(entry['source'])
            (folder / 'SKILL.md').write_text(f"# {name}\n\n{row.get('description', '')}\n\n"
                'Read this source before selecting it. Recompute geometry from the current observation. '
                'Draft code is available for reuse and iteration; collection does not certify physical success. '
                'Keep failures and source hashes when adapting this program.\n')
            # A model edit replaces a source package; an old task-flow hash must
            # not appear to describe this new source. Git keeps the old package.
            (folder / 'flow.json').unlink(missing_ok=True)
            write_json(folder / 'metadata.json', row)
            write_json(folder / 'source_manifest.json', {filename:
                hashlib.sha256((folder / filename).read_bytes()).hexdigest()
                for filename in ('source.py', 'SKILL.md', 'metadata.json')})
            self._index()
            commit = self.library.git.commit(f'Collect geometry {name} v{version} for reuse')
            self.library.git.tag(tag_name, version)
            self.library.git.require_clean()
            return self._release({**row, 'source': entry['source']}, commit)
