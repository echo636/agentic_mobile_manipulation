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
handle SIGTRAP stop print nopass
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
    codes = {'SIGSEGV': 139, 'SIGBUS': 135, 'SIGABRT': 134, 'SIGTRAP': 133}
    signal = getattr(event, 'stop_signal', None)
    # GDB 13 reports SIGTRAP as a generic StopEvent. There are no user
    # breakpoints in this launcher; confirm the kernel signal before recording.
    if signal is None and isinstance(event, gdb.StopEvent):
        try:
            if int(gdb.parse_and_eval('$_siginfo.si_signo')) == 5:
                signal = 'SIGTRAP'
        except gdb.error:
            pass
    if signal not in codes or _fault_captured:
        return
    _fault_captured = True
    exit_code = codes[signal]
    record = {'schema_version': 1, 'status': 'native_fault',
              'signal': signal, 'exit_code': exit_code,
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
