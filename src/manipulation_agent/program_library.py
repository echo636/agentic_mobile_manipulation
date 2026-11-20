"""Versioned executable experience. Code is curated locally, never supplied by a tool.

This is an integrity-checked library of trusted Python, not a Python security sandbox.
Demonstrations do not certify that a compiled program worked unattended.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from functools import wraps

from .records import now, write_json
from .experience_skills import configured_root, file_hash, source_facts
from .skill_git import SkillGit


def configured_program_root():
    value = os.environ.get('MAS_PROGRAM_LIBRARY_ROOT')
    experience = configured_root()
    return Path(value) if value else experience.parent / 'skill_library' if experience else None


def checked_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
        raise ValueError('Use an executable skill name, not a path')
    return name


def source_files(folder):
    paths = [folder / 'SKILL.md', folder / 'spec.json', *sorted((folder / 'code').rglob('*.py'))]
    if not (folder / 'code/run.py').is_file():
        raise ValueError('Executable skill needs code/run.py')
    result = {}
    for path in paths:
        if any(p.is_symlink() for p in [path, *path.parents] if p != folder.parent):
            raise ValueError('Executable skill sources cannot use symlinks')
        if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
            raise ValueError('Invalid executable skill file')
        data = path.read_bytes()
        if len(data) > 100000:
            raise ValueError('Executable skill source file exceeds 100 KB')
        if path.suffix == '.py':
            compile(data, str(path), 'exec')
        result[str(path.relative_to(folder))] = hashlib.sha256(data).hexdigest()
    return result


def digest(files, *, code_only=False):
    selected = {k: v for k, v in files.items() if not code_only or k == 'spec.json' or k.startswith('code/')}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def runtime_identity():
    """Conservatively invalidate verification when any Harness Python changes.

    Skill-local hashes alone miss imported geometry, tool validation and executors.
    Simulator/assets/environment provenance remains a separate per-run record.
    """
    root = Path(__file__).resolve().parent
    files = {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob('*.py'))}
    return {'runtime_sha256': digest(files), 'runtime_files': files}


def rendered_doc(folder, status, version=None):
    text = (folder / 'SKILL.md').read_text()
    text = re.sub(r'^status: (draft|verified|deprecated)$', 'status: ' + status, text, count=1, flags=re.M)
    return re.sub(r'^version: [0-9]+$', 'version: ' + str(version), text, count=1, flags=re.M) if version else text


def library_locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.locked():
            return method(self, *args, **kwargs)
    return call


class ProgramLibrary:
    def __init__(self, root):
        self.repository = Path(root).resolve()
        self.root = self.repository / 'oracle_waypoints'
        self.git = SkillGit(self.repository)
        self._thread_lock = threading.RLock()
        self._lock_depth = 0

    def folder(self, name):
        folder = self.root / 'skills' / checked_name(name)
        if any(p.is_symlink() for p in [folder, folder.parent, self.root]):
            raise ValueError('Executable library paths cannot use symlinks')
        return folder

    @contextlib.contextmanager
    def locked(self):
        with self._thread_lock:
            if self._lock_depth:
                yield
                return
            self.repository.mkdir(parents=True, exist_ok=True)
            with (self.repository / '.library.lock').open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                    fcntl.flock(stream, fcntl.LOCK_UN)

    @library_locked
    def current(self, name):
        folder = self.folder(name)
        pointer = folder / 'current.json'
        if not pointer.exists():
            return None
        if pointer.is_symlink():
            raise ValueError('Invalid program pointer')
        meta = json.loads(pointer.read_text())
        if meta.get('name') != name or type(meta.get('version')) is not int or meta['version'] < 1:
            raise ValueError('Invalid executable skill identity/version')
        version = folder if meta.get('storage') == 'git' else folder / 'revisions' / f"{meta['version']:06d}"
        files = source_files(version)
        if digest(files) != meta['source_sha256'] or digest(files, code_only=True) != meta['code_sha256']:
            raise ValueError('Executable skill integrity mismatch')
        if meta['status'] not in {'draft', 'verified', 'deprecated'}:
            raise ValueError('Invalid executable skill status')
        return {**meta, 'directory': version}

    @library_locked
    def catalog(self):
        folder = self.root / 'skills'
        if folder.is_symlink():
            raise ValueError('Invalid executable skills directory')
        return [self.current(p.name) for p in sorted(folder.iterdir()) if p.is_dir()] if folder.exists() else []

    def _index(self):
        rows = ['# Executable Oracle skills', '', '| Skill | Status | Version | When to use |', '| --- | --- | --- | --- |']
        for entry in self.catalog():
            rows.append(f"| [{entry['name']}](skills/{entry['name']}/SKILL.md) | {entry['status']} | {entry['version']} | {entry['description']} |")
        (self.root / 'INDEX.md').write_text('\n'.join(rows) + '\n')

    @library_locked
    def initialize_git(self):
        """Import legacy history before flattening; original NAS evidence stays intact."""
        self.git.initialize()
        if not self.git.head():
            self.git.commit('Import existing executable skill records')
        self.git.require_clean()
        legacy = [e for e in self.catalog() if e and e.get('storage') != 'git']
        baseline = self.git.head()
        # Validate all active sources before altering any published directory.
        for entry in legacy:
            source_files(entry['directory'])
        for entry in legacy:
            folder = self.folder(entry['name'])
            old = entry['directory']
            versions = []
            for revision in sorted((folder / 'revisions').iterdir()):
                if not revision.is_dir():
                    continue
                manifest = json.loads((revision / 'source_manifest.json').read_text())
                versions.append({'version': manifest['version'], 'code_sha256': manifest['code_sha256'],
                    'commit': baseline, 'path': str(revision.relative_to(self.repository))})
                demo = json.loads((revision / 'evidence/demonstration.json').read_text())
                # Preserve ignored image bytes if the original NAS source vanished.
                original = Path(demo['image'])
                for image in (revision / 'evidence/images').iterdir():
                    if original.is_file() and file_hash(original) == file_hash(image):
                        continue
                    media = self.repository.parent / 'skill_library_media'
                    media.mkdir(exist_ok=True)
                    target = media / (file_hash(image) + image.suffix.lower())
                    if not target.exists():
                        shutil.copyfile(image, target)
                    versions[-1].setdefault('media', []).append(str(target))
            for relative in source_files(old):
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(old / relative, target)
            (folder / 'SKILL.md').write_text(rendered_doc(old, entry['status'], entry['version']))
            shutil.copyfile(old / 'evidence/demonstration.json', folder / 'evidence/demonstration.json')
            demo = json.loads((folder / 'evidence/demonstration.json').read_text())
            if not Path(demo['image']).is_file():
                media = next(v.get('media', []) for v in versions if v['version'] == entry['version'])
                if media:
                    demo['original_image'] = demo['image']
                    demo['image'] = media[0]
                    write_json(folder / 'evidence/demonstration.json', demo)
            write_json(folder / 'pre_git_versions.json', {'versions': versions})
            entry.pop('directory')
            entry['storage'] = 'git'
            files = source_files(folder)
            entry['source_sha256'] = digest(files)
            write_json(folder / 'source_manifest.json', {'files': files, **entry})
            write_json(folder / 'current.json', entry)
            # All old text/code is now recoverable in baseline; media was checked above.
            shutil.rmtree(folder / 'revisions')
        if legacy:
            self._index()
            self.git.commit('Store current skills in worktree; retain earlier versions in Git')
            for entry in legacy:
                self.git.tag(entry['name'], entry['version'])
        return {'repository': str(self.repository), 'commit': self.git.head(),
                'migrated_skills': [e['name'] for e in legacy],
                'remotes': self.git.command('remote').stdout.splitlines()}

    @library_locked
    def history(self, name=None, limit=20):
        return self.git.history(checked_name(name) if name else None, limit)

    def publish(self, source, expected_version, demonstration_run=None, image=None):
        source = Path(source).resolve()
        files = source_files(source)
        spec = json.loads((source / 'spec.json').read_text())
        name = checked_name(spec['name'])
        if spec['protocol'] != 'oracle_waypoints' or spec['robot'] != 'R1Pro' or spec['parameters'].get('type') != 'object':
            raise ValueError('Supply an R1Pro Oracle parameter contract')
        for dep in spec.get('uses', []):
            checked_name(dep)
        if image is None:
            raise ValueError('A draft needs a recorded reference image')
        facts = source_facts(demonstration_run)[0] if demonstration_run else None
        if not facts or facts['protocol'] != 'oracle_waypoints' or not (
                facts['engineering_success'] is True or facts['official_task_success'] is True):
            raise ValueError('Supply an independently reviewed Oracle demonstration')
        image = Path(image).resolve()
        env = Path(demonstration_run).resolve() / 'env'
        if not image.is_relative_to(env) or not image.is_file() or image.suffix.lower() not in {'.jpg', '.png'}:
            raise ValueError('Use an image in the recorded demonstration')
        with self.locked():
            self.initialize_git()
            self.git.require_clean()
            current = self.current(name)
            if type(expected_version) is not int or expected_version != (current['version'] if current else 0):
                raise ValueError('Stale executable skill version')
            dependency_hashes = {}
            def dependency(dep, stack):
                if dep == name or dep in stack:
                    raise ValueError('Cyclic executable dependency')
                current_dep = self.current(dep)
                if not current_dep or current_dep['status'] == 'deprecated':
                    raise ValueError('Missing or deprecated executable dependency')
                for child in current_dep['uses']: dependency(child, stack | {dep})
                return current_dep['code_sha256']
            for dep in spec.get('uses', []): dependency_hashes[dep] = dependency(dep, set())
            tag = f'oracle_waypoints/{name}/v{expected_version + 1:06d}'
            if self.git.command('show-ref', '--verify', '--quiet', 'refs/tags/' + tag, check=False).returncode == 0:
                raise ValueError('This release tag already exists; do not reuse published version numbers')
            # Prepare a stable copy before replacing the one current source tree.
            with tempfile.TemporaryDirectory(prefix='.skill-publish-', dir=self.repository.parent) as temporary:
                staged = Path(temporary)
                for relative in files:
                    target = staged / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / relative, target)
                if source_files(staged) != files:
                    raise ValueError('Source changed during publication; keep the existing active version')
                (staged / 'SKILL.md').write_text(rendered_doc(staged, 'draft', expected_version + 1))
                files = source_files(staged)
                folder = self.folder(name)
                folder.mkdir(parents=True, exist_ok=True)
                if (folder / 'code').exists():
                    shutil.rmtree(folder / 'code')
                for relative in files:
                    target = folder / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(staged / relative, target)
                evidence = folder / 'evidence'
                evidence.mkdir(exist_ok=True)
                write_json(evidence / 'demonstration.json', {'type': 'model_authored_demonstration', 'source': facts,
                    'image': str(image), 'image_sha256': file_hash(image), 'proves_unattended_code_success': False})
                meta = {'name': name, 'version': expected_version + 1, 'status': 'draft', 'storage': 'git',
                        'description': spec['description'], 'protocol': 'oracle_waypoints', 'robot': 'R1Pro',
                        'entry': 'code/run.py', 'uses': spec.get('uses', []),
                        'source_sha256': digest(files), 'code_sha256': digest(files, code_only=True),
                        'dependency_code_sha256': dependency_hashes, **runtime_identity(), 'updated_at': now()}
                write_json(folder / 'source_manifest.json', {'files': files, **meta})
                write_json(folder / 'current.json', meta)
                self._status_event(name, meta, 'published a new draft from reviewed demonstration')
                self._index()
                self.git.commit(f'Publish {name} v{meta["version"]} as draft')
                self.git.tag(name, meta['version'])
        return meta

    def _status_event(self, name, entry, reason):
        path = self.folder(name) / 'evidence/status.jsonl'
        path.parent.mkdir(exist_ok=True)
        with path.open('a') as stream:
            stream.write(json.dumps({'date': now(), 'version': entry['version'], 'status': entry['status'],
                'code_sha256': entry['code_sha256'], 'runtime_sha256': entry['runtime_sha256'], 'reason': reason}) + '\n')

    def record_run(self, run, invocation_id):
        """Import a finished code invocation; independent review is a separate file."""
        run = Path(run).resolve()
        checked_name(invocation_id)
        facts, episode, env = source_facts(run)
        if episode.get('config', {}).get('agent_profile') != 'oracle_compiled':
            raise ValueError('Code evidence must come from an oracle_compiled episode')
        invocation = json.loads((env / 'program_runs' / (invocation_id + '.json')).read_text())
        name = checked_name(invocation['name'])
        current = self.current(name)
        review_file = run / 'program_validation.json'
        reviews = json.loads(review_file.read_text()).get('invocations', []) if review_file.exists() else []
        review = next((r for r in reviews if r.get('invocation_id') == invocation_id and
                       r.get('code_sha256') == invocation['code_sha256']), {})
        invocation_hash = file_hash(env / 'program_runs' / (invocation_id + '.json'))
        independent = (review.get('passed') is True and review.get('scope') == 'program_invocation' and
                       review.get('invocation_sha256') == invocation_hash and
                       review.get('runtime_sha256') == invocation.get('runtime_sha256'))
        frames = invocation['result'].get('frames', [])
        frames_verified = bool(frames)
        for frame in frames:
            ref = frame.get('image_ref', '') if isinstance(frame, dict) else ''
            path = env / 'frames' / (ref + '.jpg')
            valid = bool(re.fullmatch(r'[A-Za-z0-9_-]+', ref))
            frames_verified = frames_verified and valid and path.is_file() and file_hash(path) == frame.get('sha256')
        success = bool(invocation['result']['ok'] and independent and frames_verified and
                       (facts['engineering_success'] is True or facts['official_task_success'] is True))
        if (not current or invocation['code_sha256'] != current['code_sha256'] or
                invocation['version'] != current['version'] or
                invocation.get('runtime_sha256') != current['runtime_sha256'] or
                invocation.get('dependency_code_sha256') != current['dependency_code_sha256']):
            raise ValueError('Record evidence against the current exact code version')
        commit = invocation.get('library_commit', '')
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40,64}', commit):
            raise ValueError('Invocation must identify its frozen skill Git commit')
        published = json.loads(self.git.command('show',
            f'{commit}:oracle_waypoints/skills/{name}/current.json').stdout)
        if any(published.get(k) != invocation.get(k) for k in
               ('version', 'code_sha256', 'runtime_sha256', 'dependency_code_sha256')):
            raise ValueError('Invocation differs from the recorded Git publication')
        evidence = {'run': str(run), 'invocation_id': invocation_id, 'date': now(),
                    'version': invocation['version'], 'code_sha256': invocation['code_sha256'],
                    'runtime_sha256': invocation['runtime_sha256'],
                    'library_commit': commit,
                    'outcome': 'success' if success else 'failure_or_unreviewed',
                    'unattended': invocation.get('unattended') is True,
                    'independently_reviewed': independent, 'source': facts,
                    'invocation_sha256': invocation_hash,
                    'review_sha256': file_hash(review_file) if review_file.exists() else None,
                    'frames': frames, 'frames_verified': bool(frames_verified),
                    'measured': invocation['result'].get('measured', {}),
                    'why': invocation['result'].get('why')}
        with self.locked():
            self.git.require_clean()
            if self.current(name)['version'] != current['version']:
                raise ValueError('Executable version changed while importing evidence')
            path = self.folder(name) / 'evidence' / 'runs.jsonl'
            path.parent.mkdir(exist_ok=True)
            existing = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
            if any(e['run'] == str(run) and e['invocation_id'] == invocation_id and
                   e['review_sha256'] == evidence['review_sha256'] for e in existing):
                raise ValueError('Invocation already recorded; evidence is append-only')
            with path.open('a') as stream:
                stream.write(json.dumps(evidence, ensure_ascii=False) + '\n')
            self.git.commit(f'Record {name} {invocation_id}: {evidence["outcome"]}')
        return evidence

    def set_status(self, name, status, expected_version, reason=''):
        if status not in {'verified', 'deprecated'}:
            raise ValueError('Use verified or deprecated')
        with self.locked():
            self.git.require_clean()
            entry = self.current(name)
            if not entry or entry['version'] != expected_version:
                raise ValueError('Stale executable skill version')
            if status == 'verified':
                if entry['status'] == 'deprecated':
                    raise ValueError('Publish a new draft to replace a deprecated skill')
                if entry['runtime_sha256'] != runtime_identity()['runtime_sha256']:
                    raise ValueError('Runtime changed; publish a new draft and revalidate')
                for dep in entry['uses']:
                    item = self.current(dep)
                    if not item or item['status'] != 'verified' or item['code_sha256'] != entry['dependency_code_sha256'][dep]:
                        raise ValueError('Verify the exact pinned dependencies first')
                path = self.folder(name) / 'evidence/runs.jsonl'
                rows = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
                latest = {(r['run'], r['invocation_id']): r for r in rows}
                successes = {r['run'] for r in latest.values() if r['code_sha256'] == entry['code_sha256'] and
                    r['version'] == entry['version'] and r['runtime_sha256'] == entry['runtime_sha256'] and
                    r['outcome'] == 'success' and r['unattended'] and r['independently_reviewed'] and r['frames_verified']}
                if len(successes) < 2:
                    raise ValueError('Verification needs two independent unattended code runs with frames; demonstrations do not count')
            elif not reason.strip():
                raise ValueError('Deprecation needs a reason')
            entry.pop('directory')
            entry.update(status=status, status_reason=reason, updated_at=now())
            folder = self.folder(name)
            (folder / 'SKILL.md').write_text(rendered_doc(folder, status, entry['version']))
            files = source_files(folder)
            entry['source_sha256'] = digest(files)
            write_json(folder / 'source_manifest.json', {'files': files, **entry})
            write_json(self.folder(name) / 'current.json', entry)
            self._status_event(name, entry, reason or 'two independently reviewed unattended code runs passed')
            self._index()
            self.git.commit(f'Mark {name} v{entry["version"]} {status}')
        return entry

    @library_locked
    def freeze(self, output):
        Path(output).mkdir(parents=True, exist_ok=True)
        selected = [e for e in self.catalog() if e and e['status'] != 'deprecated']
        if selected:
            self.git.require_clean()
        commit = self.git.head()
        runtime = runtime_identity()['runtime_sha256']
        if any(e['runtime_sha256'] != runtime for e in selected):
            raise ValueError('Executable runtime changed; republish skills as draft before a new episode')
        names = {e['name'] for e in selected}
        lookup = {e['name']: e for e in selected}
        def visit(name, stack):
            if name in stack:
                raise ValueError('Cyclic executable dependency')
            entry = lookup[name]
            for dep in entry['uses']:
                if dep not in lookup or lookup[dep]['code_sha256'] != entry['dependency_code_sha256'].get(dep):
                    raise ValueError('Executable dependency changed; republish the dependent skill')
                visit(dep, stack | {name})
        for name in names: visit(name, set())
        entries = {}
        for entry in selected:
            if not set(entry['uses']) <= names:
                raise ValueError('Missing/deprecated executable dependency')
            target = Path(output) / 'program_snapshot' / entry['name']
            target.mkdir(parents=True, exist_ok=False)
            for relative in source_files(entry['directory']):
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry['directory'] / relative, destination)
            (target / 'SKILL.md').write_text(rendered_doc(entry['directory'], entry['status'], entry['version']))
            metadata = {k: v for k, v in entry.items() if k != 'directory'}
            metadata['library_commit'] = commit
            metadata['snapshot_sha256'] = digest(source_files(target))
            write_json(target / 'program.json', metadata)
            entries[entry['name']] = {**metadata, 'directory': target, 'parameters': json.loads((target / 'spec.json').read_text())['parameters']}
        write_json(Path(output) / 'program_manifest.json', {'protocol': 'oracle_waypoints', 'library_commit': commit,
            'programs': [{k: v for k, v in e.items() if k != 'directory'} for e in entries.values()]})
        return entries
