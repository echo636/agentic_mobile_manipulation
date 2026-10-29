"""Real external GDB integration; no GPU, simulator or model is started."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class NativeFaultCaptureTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('gdb'), 'GDB is unavailable')
    def test_exec_exit_codes_signals_and_owned_child_cleanup(self):
        script=Path(__file__).resolve().parents[1]/'scripts/capture_native_fault.gdb'
        cases={
            'exit0': ('raise SystemExit(0)',0,None),
            'exit2': ('raise SystemExit(2)',2,None),
            'usr1': ("signal.signal(signal.SIGUSR1,lambda *_:marker.write_text('handled'))\n"
                     "os.kill(os.getpid(),signal.SIGUSR1)\nraise SystemExit(2)",2,None),
            'segv': ('import ctypes\nctypes.memset(0,0,1)',139,'SIGSEGV'),
            'bus': ('os.kill(os.getpid(),signal.SIGBUS)',135,'SIGBUS'),
            'abort': ('os.abort()',134,'SIGABRT'),
        }
        artifact_root=os.environ.get('MAS_NATIVE_CAPTURE_TEST_ARTIFACTS')
        for name,(action,expected_exit,expected_signal) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='mas-gdb-test-') as folder:
                root=Path(folder);output=root/'not_created_before_fault'
                pid_file=root/'child.pid';marker=root/'usr1.txt'
                body=("import os,signal,sys\nfrom pathlib import Path\n"
                      "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                      "marker=Path(sys.argv[2])\n"+action)
                # Match behavior100_remote.simulate's execv into a new Python
                # image, so following the helper's exec is tested, not assumed.
                child=root/'child.py';child.write_text(body)
                helper=root/'helper.py'
                helper.write_text('import os,sys\nos.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
                env=dict(os.environ,MAS_NATIVE_FAULT_DIR=str(output))
                command=['/usr/bin/gdb','--batch','-q','-nx','-x',str(script),'--args',
                         sys.executable,str(helper),str(child),str(pid_file),str(marker)]
                result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=40)
                pid=int(pid_file.read_text()) if pid_file.exists() else None
                record={'case':name,'expected_exit':expected_exit,'actual_exit':result.returncode,
                        'expected_signal':expected_signal,'inferior_pid':pid,
                        'child_gone':pid is not None and not Path('/proc',str(pid)).exists(),
                        'fault_directory_exists':output.exists(),
                        'usr1_handled':marker.exists() and marker.read_text()=='handled'}
                if artifact_root:
                    destination=Path(artifact_root)/name
                    destination.mkdir(parents=True,exist_ok=False)
                    (destination/'process.json').write_text(json.dumps(record,indent=2)+'\n')
                    (destination/'stdout.log').write_text(result.stdout)
                    (destination/'stderr.log').write_text(result.stderr)
                    if output.exists():shutil.copytree(output,destination/'fault')
                self.assertEqual(result.returncode,expected_exit,result.stdout+result.stderr)
                self.assertTrue(record['child_gone'])
                if expected_signal is None:
                    self.assertFalse(output.exists(),'Normal startup must leave Recorder output nonexistent')
                    if name=='usr1':self.assertTrue(record['usr1_handled'])
                else:
                    self.assertEqual({p.name for p in output.iterdir()},{'native_fault.log','native_fault.json'})
                    fault=json.loads((output/'native_fault.json').read_text())
                    self.assertEqual(fault['signal'],expected_signal)
                    self.assertEqual(fault['exit_code'],expected_exit)
                    self.assertEqual(fault['inferior_pid'],pid)
                    self.assertIn('#0',(output/'native_fault.log').read_text())


if __name__=='__main__':unittest.main()
