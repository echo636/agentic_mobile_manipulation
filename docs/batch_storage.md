# Supervisor storage recovery

`run_behavior100.py` checkpoints progress, task records and supervisor journal
entries to local disk before a background thread publishes them to the batch
directory and dashboard. A shared-storage write or dashboard-template read
failure no longer propagates into `run_one` or requests `finish(aborted)`.

The default checkpoint directory is
`/var/tmp/mas-batch-state-<uid>/<hash-of-batch-path>`. Set
`supervisor_spool_dir` to a private **local disk** directory to override it.
Do not place it on the shared filesystem being protected. Check local free
space/quota before launching a batch. Initialization verifies a durable local
write before launching any simulator.

`checkpoint.json` contains the latest state and journal events;
`sync_status.json` reports publication progress; `storage_errors.jsonl` retains
storage errors. A snapshot's `storage_sync` field reports the last observed
sync state, separately from robot task success. Failed writes retry every
`publication_retry_seconds` (default 5). Pending updates coalesce into the
latest state, and a single publisher prevents stale writes overtaking new ones.
Journal entries have stable IDs for retry deduplication and append without
replacing external audit entries.

On restart on the same host, the runner verifies the manifest identity and
prefers newer local per-task records over stale shared records. Existing
inflight adoption rules still apply; a finished local attempt is not rerun.
After a host loss, recover its checkpoint directory before starting a new
supervisor against stale shared records. Never run two supervisors for one
batch. The existing batch lock and GPU leases remain required.

Shutdown waits at most `publication_flush_seconds` (default 30) for replication.
An unsynced checkpoint is retained and its path is printed; it is not a task
failure or a successful archive. Rerun the coordinator (or `--publish-only`)
with the same batch and local spool to retry publication. Do not delete the
spool until synchronization and batch archival have been verified.

If the local disk also fails during an episode, the current state remains in
memory, persistence retries, and admission of new episodes is held. This does
not guarantee durability after a simultaneous host crash. Resource lease
failures, simulator/controller artifact failures, and bulk video/archive
failures retain their existing checks; this change does not repair the NAS or
turn incomplete evidence into successful experiments.

Validation includes injected `EIO` at each affected publication destination,
template read failure, slow publication, automatic recovery, checkpoint restart,
journal preservation, local disk exhaustion, and a real CPU controller process
that exits normally while heartbeat publication repeatedly fails. These are
supervisor diagnostics, not autonomous simulator task successes.
