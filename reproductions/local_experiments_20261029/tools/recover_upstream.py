"""Recover an exact frozen upstream subset from a pinned Git base plus file overrides."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from archive import ROOT, digest, read_object, safe_relative

class GitObjects:
    def __init__(self, repo):
        self.process=subprocess.Popen(['git','cat-file','--batch'],cwd=repo,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def read(self, ref):
        if '\n' in ref or '\r' in ref:raise ValueError('Invalid Git object reference')
        self.process.stdin.write((ref+'\n').encode());self.process.stdin.flush()
        header=self.process.stdout.readline().decode().rstrip('\n')
        if header.endswith(' missing'):return None
        fields=header.split()
        if len(fields)!=3 or fields[1]!='blob':raise ValueError('Expected Git blob: '+header)
        data=self.process.stdout.read(int(fields[2]));self.process.stdout.read(1)
        return data
    def close(self):
        self.process.stdin.close();self.process.wait(timeout=10)
        if self.process.returncode:raise RuntimeError('git cat-file failed')

def recover(root, bundle, repo, output):
    config=json.loads((root/'upstream'/bundle/'recovery.json').read_text())
    subprocess.run(['git','cat-file','-e',config['base_commit']+'^{commit}'],cwd=repo,check=True,capture_output=True)
    if output.exists() or output.is_symlink():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True);staging=Path(tempfile.mkdtemp(prefix='.upstream-',dir=output.parent));git=GitObjects(repo)
    try:
        for name,expected in config['files'].items():
            rel=safe_relative(name)
            data=(read_object(root,config['overrides'][name]) if name in config['overrides'] else git.read(config['base_commit']+':'+name))
            if data is None or digest(data)!=expected:raise ValueError('Upstream checksum mismatch: '+name)
            target=staging/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        git.close();git=None
        if output.exists() or output.is_symlink():raise FileExistsError(output)
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True);raise
    finally:
        if git is not None:git.close()
    return {'passed':True,'files':len(config['files']),'overrides':len(config['overrides']),'output':str(output)}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive-root',type=Path,default=ROOT);p.add_argument('--bundle',choices=['hot_drink_20261026','wild_party_20261026'],required=True);p.add_argument('--git-checkout',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(recover(a.archive_root.resolve(),a.bundle,a.git_checkout.resolve(),a.output.absolute()),indent=2))
if __name__=='__main__':main()
