"""Launch a durable batch with explicitly selected network variables, never logs their values."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--unit',required=True);p.add_argument('--bootstrap-first',action='store_true');a=p.parse_args()
if not re.fullmatch(r'mas-b100-[a-z0-9-]+\.service',a.unit):raise ValueError('Expected a project batch unit name')
c=json.loads(a.config.read_text());source=Path(__file__).resolve().parents[1]
cmd=['systemd-run','--user','--unit='+a.unit,'--description=BEHAVIOR100 durable batch supervisor',
     '-p','MemoryMax=8G','-p','CPUQuota=600%','-p','RuntimeMaxSec=172800','-p','TimeoutStopSec=45','-p','KillMode=control-group',
     '-p','WorkingDirectory='+str(source),'--setenv=MAS_UNIT='+a.unit,'--setenv=PYTHONPATH='+str(source/'src')]
for key in c.get('required_controller_env',[]):
    if key not in {'http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','no_proxy','SSL_CERT_FILE','REQUESTS_CA_BUNDLE'}:
        raise ValueError('Only explicit network configuration may be inherited')
    if not os.environ.get(key):raise RuntimeError('Required network variable absent: '+key)
    cmd.append('--setenv='+key+'='+os.environ[key])
cmd += [sys.executable,str(source/'scripts/run_behavior100.py'),'--config',str(a.config.resolve())]
if a.bootstrap_first:cmd.append('--bootstrap-first')
subprocess.run(cmd,check=True)
