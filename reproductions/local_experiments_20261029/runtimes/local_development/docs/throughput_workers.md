# Parallel evaluation and RGB rendering

The coordinator owns one shared queue. Each worker uses an explicit SSH target,
physical GPU (optionally pinned by UUID), bridge port, interpreter, data root and
resource budget. Worker configuration is thread-local; the model-controller and
archive commands use the same lane. Each immutable task attempt is claimed once.
The initial implementation remains compatible with a single-host `gpus` list.

Example worker entries in the existing site-specific batch config:

```json
{
  "memory_budget_gib": 14,
  "simulator_memory_max": "14G",
  "host_worker_memory_budget_gib": {"researcher@simulation-host": 28},
  "simulator_env": {"MAS_VIDEO_RENDER_STRIDE": "2", "MAS_VIDEO_RENDER_FLUSHES": "4"},
  "workers": [
    {"id": "host-gpu1", "gpu": 1},
    {"id": "host-gpu2", "gpu": 2}
  ]
}
```

The SSH/data/interpreter/base-port settings are inherited from the existing
config; each lane can override these for a different host. Unknown environment
overrides, duplicate host/GPU or host/port pairs, mismatched memory admission and
hard limits, and overcommitted configured host allowances are rejected.
Preflight checks driver, GPU capacity, cgroup headroom, disk, quota and encoder
before each attempt. No unrelated process is stopped. Results include worker,
actual simulator host/PID/unit/interpreter/UUID and per-run source provenance.

Create `drain_requested.json` in the batch directory to stop claiming new tasks
after active attempts finish. It does not abort their model or simulator. Delete
that control file before a deliberate later resume.

For the earlier coordinator lacking drain support, `adopt_inflight` explicitly
maps an existing run ID to its original worker. The old coordinator must first
be retired while preserving its controller process. This is a site operation,
not an automatic action: verify process ownership and use a tested handoff,
then acquire the existing batch lock with the new coordinator. Adoption verifies
the original simulator unit/PID and controller command/output, retains its
model timeout/start time/source, and archives that same attempt. It does not
reset the simulation, restart a model, or create an extra attempt. A vanished
controller is finalized from its recorded outcome; failures remain failures.

## Render work removed

All policy observations still use four fixed 512×512 RGB cameras and four
render-only barrier ticks at a single simulation state. The spectator remains
private to replay. One rendered packet now supplies both the policy observation
and its video boundary; the previous second render pass was redundant. Camera
poses are all updated before a shared barrier, removing the separate spectator
render. CPU video composition caches fonts and labeled camera tiles/hashes for
explicit held frames. Every control step and observation boundary remains in
the CFR video ledger; no motion interpolation is introduced.

`MAS_VIDEO_RENDER_FLUSHES` optionally selects 2, 3 or 4 render ticks for continuous
video only (default 4). It never changes the policy-observation barrier or
control-step/frame sampling rate. Lower values must pass the real diagnostic:

```
python scripts/probe_render_throughput.py --output /owned/new/probe-directory
```

The probe changes camera poses with physics frozen, compares all four depth/RGB
render products against an eight-tick reference, then alternates four-tick and
candidate-tick captures on the same ideal turn from the same saved state. It
records frame evidence, final-pose/step equivalence and wall-time measurements.
This is a privileged component diagnostic, not an autonomous benchmark score or
a claim about end-to-end speedup. CPU tests alone cannot establish render
freshness. Keep four ticks if the candidate does not validate.

The deployment, resource changes, original failures, paired measurements and
mixed-version cohort provenance are recorded under
`operations/throughput_20261001` in the shared project root. Already-running or
completed episodes retain their old source; new rendering versions are visible
per row and must not be represented as a uniform-version controlled experiment.
