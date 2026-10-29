"""Check the publication tree for credential literals without printing their values."""
import argparse
import gzip
import json
from pathlib import Path
import re
from archive import ROOT

PATTERNS={
 'secret_token':re.compile(rb'\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b'),
 'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'literal_credential':re.compile(rb'(?:api[_-]?key|access_token|refresh_token|client_secret|password)[\"\x27]?\s*[:=]\s*[\"\x27]([^\"\x27\r\n]{18,})[\"\x27]',re.I),
 'url_credentials':re.compile(rb'https?://[^/\s:@]+:[^/\s@]{8,}@'),
 'literal_bearer':re.compile(rb'Bearer [A-Za-z0-9_.-]{30,}'),
}

def scan(root):
 findings=[];count=0
 for p in sorted(root.rglob('*')):
  if not p.is_file() or '__pycache__' in p.parts:continue
  data=p.read_bytes()
  if p.suffix=='.gz':data=gzip.decompress(data)
  count+=1
  for name,pattern in PATTERNS.items():
   for match in pattern.finditer(data):
    findings.append({'path':str(p.relative_to(root)),'kind':name,'line':data[:match.start()].count(b'\n')+1})
 return {'files_checked':count,'findings':findings,'passed':not findings,'scope':'Static literal patterns; credential directories and raw client sessions are excluded by collection policy.'}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive-root',type=Path,default=ROOT);p.add_argument('--output',type=Path);a=p.parse_args();report=scan(a.archive_root.resolve());text=json.dumps(report,indent=2)+'\n'
 if a.output:a.output.write_text(text)
 print(text);raise SystemExit(0 if report['passed'] else 1)
if __name__=='__main__':main()
