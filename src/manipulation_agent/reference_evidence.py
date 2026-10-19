"""Persist reference results before native simulator shutdown can exit Python."""
import json
from pathlib import Path
import time

from .records import write_json


def close_reference(backend, record, output):
    output = Path(output)
    record['cleanup_status'] = 'running' if backend is not None else 'failed'
    record['cleanup_started_at_unix'] = time.time()
    # Isaac Sim can exit the process inside close(). A parent must verify exit
    # separately; a completed task alone is never accepted as clean shutdown.
    for name in ('validation.json', 'run_record.json'):
        write_json(output / name, record)
    if backend is not None:
        try:
            backend.close()
            record['cleanup_status'] = 'passed'
        except BaseException as exc:
            record.update(status='failed', cleanup_status='failed', cleanup_error=str(exc))
    record['finished_at_unix'] = time.time()
    for name in ('validation.json', 'run_record.json'):
        write_json(output / name, record)


def complete_reference_exit(output, exit_code):
    """Called by the waiting parent after the simulator process is reaped."""
    output = Path(output)
    if not output.is_dir():
        return False
    termination = {'exit_code': exit_code, 'observed_at_unix': time.time(),
                   'validation_level': 'parent_observed_simulator_process_exit'}
    path = output / 'validation.json'
    if not path.is_file():
        termination.update(status='failed', reason='Simulator exited without final evaluator evidence')
        write_json(output / 'process_exit.json', termination)
        return False
    record = json.loads(path.read_text())
    completed = record.get('status') in ('passed', 'failed') and 'evaluation' in record
    if exit_code == 0 and completed and record.get('cleanup_status') == 'running':
        record['cleanup_status'] = 'passed'
    elif exit_code != 0 or not completed:
        record.update(status='failed', cleanup_status='failed')
    record.update(finished_at_unix=termination['observed_at_unix'], process_exit_code=exit_code,
                  cleanup_verification='parent_observed_simulator_process_exit')
    passed = record.get('status') == record.get('cleanup_status') == 'passed' and exit_code == 0
    termination['status'] = 'passed' if passed else 'failed'
    for name in ('validation.json', 'run_record.json'):
        write_json(output / name, record)
    write_json(output / 'process_exit.json', termination)
    return passed
