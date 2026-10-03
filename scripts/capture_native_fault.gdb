# Optional external native-fault capture. Never creates a core or dumps locals,
# frame arguments, environment variables, or in-process Python trace threads.
set pagination off
set confirm off
set startup-with-shell off
set disable-randomization off
set debuginfod enabled off
set follow-exec-mode same
set follow-fork-mode parent
set detach-on-fork on
set print thread-events off
set print frame-arguments none
handle SIGPIPE nostop noprint pass
# The runner uses SIGUSR1 for its existing explicit Python diagnostic handler.
handle SIGUSR1 nostop noprint pass
handle SIGSEGV stop print nopass
handle SIGBUS stop print nopass
handle SIGABRT stop print nopass
python
import gdb
import json
import os
from pathlib import Path
import time

_fault_directory = Path(os.environ['MAS_NATIVE_FAULT_DIR'])
_fault_captured = False

def capture_first_native_fault(event):
    global _fault_captured
    codes = {'SIGSEGV': 139, 'SIGBUS': 135, 'SIGABRT': 134}
    if not isinstance(event, gdb.SignalEvent) or event.stop_signal not in codes or _fault_captured:
        return
    _fault_captured = True
    exit_code = codes[event.stop_signal]
    record = {'schema_version': 1, 'status': 'native_fault',
              'signal': event.stop_signal, 'exit_code': exit_code,
              'at_unix': time.time(), 'inferior_pid': gdb.selected_inferior().pid,
              'gdb_pid': os.getpid(), 'gdb_version': gdb.VERSION,
              'scope': 'external_first_native_signal_no_core',
              'stack_file': 'native_fault.log'}
    try:
        # Only the fatal-signal path may create output. Recorder otherwise owns
        # the new episode directory and rejects any pre-existing directory.
        _fault_directory.mkdir(parents=True, exist_ok=True)
        with (_fault_directory / 'native_fault.log').open('x') as stream:
            stream.write(json.dumps(record) + '\n')
            for command in ('bt 64', 'thread apply all bt 12', 'info sharedlibrary'):
                stream.write('\nGDB: ' + command + '\n')
                try:
                    stream.write(gdb.execute(command, to_string=True))
                except gdb.error as exc:
                    stream.write('Unavailable: ' + str(exc) + '\n')
        with (_fault_directory / 'native_fault.json').open('x') as stream:
            json.dump(record, stream, indent=2)
            stream.write('\n')
    except Exception as exc:
        # Capture failure must not hide the fatal signal or leave the stopped
        # inferior behind. No retries, larger dumps, or queue changes.
        print('Native fault capture unavailable: ' + type(exc).__name__ + ': ' + str(exc))
    finally:
        # confirm off: quitting kills this stopped inferior, never other jobs.
        gdb.execute('quit ' + str(exit_code))

gdb.events.stop.connect(capture_first_native_fault)
end
run
python
code = gdb.convenience_variable('_exitcode')
gdb.execute('quit ' + str(int(code) if code is not None else 2))
end
