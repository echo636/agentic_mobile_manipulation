"""Local Git history for our published skills. No network operations."""
import os
import json
from pathlib import Path
import subprocess


IGNORE = '''.library.lock
**/.write.lock
**/__pycache__/
*.py[cod]
*.tmp
**/evidence/images/
*.jpg
*.jpeg
*.png
*.mp4
*.webm
*.npz
'''

README = '''# Robot Harness executable skills

Independent local skill repository for this project. It has no RAX upstream.
Only current skill sources, parameters and evidence indexes live in the worktree.
Git commits and release tags preserve previous versions. Raw images, videos,
simulation records and environments stay outside Git on NAS.

Maintain through scripts/manage_program_skills.py in agentic_mobile_manipulation.
Every publish, evidence import and status change creates a local commit.
Version tags use oracle_waypoints/<skill>/v000001. Active episodes retain a frozen
copy and the exact library commit; publication cannot change their code.
Do not edit the published worktree directly: edit a source proposal and publish it.
Uncommitted changes block publication and new episode snapshots.

History: git log --oneline --all
Inspect: git show <commit>:oracle_waypoints/skills/<skill>/code/run.py
Republish an older recipe as a new draft to recover it; do not reset shared HEAD
under running experiments. pre_git_versions.json identifies pre-migration paths.
'''


class SkillGit:
    def __init__(self, root):
        self.root = Path(root)

    def command(self, *arguments, check=True):
        env = os.environ.copy()
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            env.pop(key, None)
        result = subprocess.run(['git', '-C', str(self.root), *arguments],
                                capture_output=True, text=True, env=env, timeout=60)
        if check and result.returncode:
            raise ValueError('Skill Git operation failed: ' + result.stderr.strip()[:1200])
        return result

    def exists(self):
        return (self.root / '.git').is_dir() and not (self.root / '.git').is_symlink()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / '.git').exists():
            self.command('init', '--initial-branch=main')
            (self.root / '.gitignore').write_text(IGNORE)
            (self.root / 'README.md').write_text(README)
            (self.root / '.skill-library.json').write_text(json.dumps({
                'schema_version': 1, 'kind': 'robot-harness-executable-skills'})+'\n')
        if not self.exists() or Path(self.command('rev-parse', '--show-toplevel').stdout.strip()) != self.root:
            raise ValueError('Skill history needs its own ordinary Git repository')
        marker = self.root / '.skill-library.json'
        if (not marker.is_file() or marker.is_symlink() or
                json.loads(marker.read_text()).get('kind') != 'robot-harness-executable-skills'):
            raise ValueError('Refusing an unrelated repository; choose our independent skill-library directory')

    def head(self):
        if not self.exists():
            return None
        result = self.command('rev-parse', '--verify', 'HEAD', check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def require_clean(self):
        if not self.head():
            raise ValueError('Initialize the independent skill Git repository first')
        if self.command('status', '--porcelain', '--untracked-files=normal').stdout.strip():
            raise ValueError('Skill Git worktree has uncommitted changes; preserve/review them before publishing or freezing')

    def commit(self, message):
        paths = [p for p in ['.gitignore', '.skill-library.json', 'README.md', 'oracle_waypoints'] if (self.root / p).exists()]
        self.command('add', '--', *paths)
        changed = self.command('diff', '--cached', '--quiet', check=False)
        if changed.returncode not in {0, 1}:
            raise ValueError('Cannot inspect staged skill changes')
        if changed.returncode:
            self.command('-c', 'user.name=Robot Harness Skills', '-c', 'user.email=robot-harness@localhost',
                         '-c', 'commit.gpgsign=false', 'commit', '-m', message)
        return self.head()

    def tag(self, name, version):
        self.command('tag', f'oracle_waypoints/{name}/v{version:06d}')

    def history(self, name=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('History limit must be 1..200')
        if not self.head():
            return []
        path = 'oracle_waypoints/skills/' + name if name else 'oracle_waypoints'
        result = self.command('log', f'-{limit}', '--format=%H%x00%aI%x00%s', '--', path)
        return [dict(zip(('commit', 'date', 'message'), row.split('\0', 2)))
                for row in result.stdout.splitlines()]
