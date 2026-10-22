# Simulator memory admission

GPU leases still prevent two project workers from taking the same device.
Host admission uses account working memory (excluding clean file cache and
reclaimable slab), plus only the growth reserved above each peer's working
memory. It does not sum full process limits on top of current account usage.

Ready peers explicitly using `observed_peak` admission use sampled working-memory high-water marks, with 10% plus 1 GiB
growth allowance and a 12 GiB floor. Samples are shared by cgroup identity in
`/tmp/mas-memory-admission-<uid>`. Kernel `memory.peak` remains diagnostic only:
it includes cache and cannot represent a working-memory peak. Unknown or
initializing peers and legacy/full-reservation peers retain their hard-limit reservation. Admissions serialize
initialization on each host, so the next decision can measure the previous job.

`memory_budget_gib` matches the per-simulator hard limit (currently 28 GiB).
`startup_memory_gib` is an independent admission estimate. Omitting it preserves
the full-limit startup reservation. Long-horizon runs retain that default;
an explicit smaller estimate in another batch is not a maximum or an OOM guarantee. The account's kernel limit wins
over configured budgets, with one 2 GiB account margin. Raw account usage near
its limit does not by itself block admission when clean cache is reclaimable.

The scheduler may run from a separate frozen checkout. `source_path` and
`source_commit` pin the executor and model-controller code; the new scheduler
is recorded separately. The scheduler's remote helper rechecks resources inside
the allocated unit, then execs the pinned executor through its original
`PYTHONPATH`. This allows resource fixes without changing benchmark behavior or
restarting an existing model or simulator. Coordinator handoff must preserve
controller descendants and verify adoption of their original PIDs.
