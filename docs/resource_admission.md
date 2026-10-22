# Simulator memory admission

New workspace batches use `memory_admission_profile: throughput` following the
2026-10-22 user request to maximize compute utilization. A shared host lock
protects resource inspection and publication of a starting unit, then releases
after systemd accepts the launch. Multiple instances may initialize together;
the previous instance does not have to become ready first. GPU leases still
prevent duplicate allocation, and every starting unit remains visible to peers.

This profile uses a 12 GiB startup estimate, independent of the 28 GiB per-unit
hard limit. Readable ready and initializing peers, including older batches, use
their sampled working-memory peak plus 10% and 1 GiB. Initializing peers also
reserve at least their startup estimate. Unreadable telemetry keeps the full
hard-limit fallback. Existing units are not modified or restarted.

Null host budgets remove the additional fixed 56/112 GiB workload ceilings.
Admission still checks the actual account cgroup limit minus 2 GiB and physical
host memory available for the candidate and peer growth. Clean file cache is
reclaimable. New batches require 14 GiB free GPU memory and allow up to 16 host
slots; available GPU leases, live memory and the batch's own concurrency limit
determine actual concurrency. These estimates permit overcommit and are not
proof that a future task cannot grow beyond its observed peak.

V05's two local 4090s are also candidates. Its renderer has passed on driver
560.35.05; a worker may declare that exact `validated_driver_version` together
with a passed `driver_validation_evidence` record. The default remote driver
floor stays unchanged. Local transport preserves runner/MCP/rsync streaming and
uses the same `local-v05` host identity and GPU UUID leases as earlier probes.
The shared GPU lower bound is 12 GiB, used on V05 GPU 1; remote workers keep
14 GiB. V05's actual 32 GiB account limit includes local model controllers.

The remaining description documents the default legacy `observed_peak` profile,
retained for frozen batches that have not opted into throughput admission.

GPU leases still prevent two project workers from taking the same device.
Host admission uses account working memory (excluding clean file cache and
reclaimable slab), plus only the growth reserved above each peer's working
memory. It does not sum full process limits on top of current account usage.

Ready peers use sampled working-memory high-water marks, with 10% plus 1 GiB
growth allowance and a 12 GiB floor. Samples are shared by cgroup identity in
`/tmp/mas-memory-admission-<uid>`. Kernel `memory.peak` remains diagnostic only:
it includes cache and cannot represent a working-memory peak. Unknown or
initializing peers retain their hard-limit reservation. Admissions serialize
initialization on each host, so the next decision can measure the previous job.

`memory_budget_gib` matches the per-simulator hard limit (currently 28 GiB).
`startup_memory_gib` is an independent admission estimate. Omitting it preserves
the full-limit startup reservation. The current rollout uses 16 GiB, which is
an estimate, not a maximum or an OOM guarantee. The account's kernel limit wins
over configured budgets, with one 2 GiB account margin. Raw account usage near
its limit does not by itself block admission when clean cache is reclaimable.

The scheduler may run from a separate frozen checkout. `source_path` and
`source_commit` pin the executor and model-controller code; the new scheduler
is recorded separately. The scheduler's remote helper rechecks resources inside
the allocated unit, then execs the pinned executor through its original
`PYTHONPATH`. This allows resource fixes without changing benchmark behavior or
restarting an existing model or simulator. Coordinator handoff must preserve
controller descendants and verify adoption of their original PIDs.
