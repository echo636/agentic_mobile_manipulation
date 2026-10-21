# Host memory admission

The default legacy scheduler reserves each simulator's full memory limit for its whole lifetime. With a 56 GiB workload budget and 28 GiB per unit, this allows two instances even when each uses only 8 GiB.

New batch configurations can enable `adaptive_memory_admission: true` and set `adaptive_host_slot_count: 4`. Slots then provide a candidate-count ceiling; GPU leases and measured memory admission still decide whether a candidate can start. All coordinators must use the same shared lease directory. Existing frozen batches retain their original behavior.

New coordinators hold a shared per-host startup lock from resource admission until bridge readiness. Initializing instances, legacy peers, and peers without readable peak telemetry retain the full hard-limit reservation. For ready instances, the reservation is the larger of 12 GiB and `1.2 * memory.peak + 2 GiB`, capped at the unit's hard limit and never below its current working memory. The account's other working memory is included; clean file cache is considered reclaimable. A candidate still reserves its full 28 GiB initialization budget.

Admission checks the projected total against both the configured host workload budget and the actual account cgroup limit. It runs before launch and again inside the allocated unit. The unit's `MemoryMax` remains unchanged. Unknown telemetry falls back to full reservations, and an unfinished initialization blocks another new startup until its owning coordinator adopts or cleans it up.

For example, two ready 8 GiB instances reserve 12 GiB each. A third 28 GiB startup plus 1 GiB of other working memory projects to 53 GiB and fits a 56 GiB budget. Two instances with much larger observed peaks can still block a third. This is measured overcommit: subsequent growth beyond observed peaks can exhaust aggregate memory. It does not guarantee that all candidate slots can run simultaneously, and it never changes another batch's unit limits or stops its jobs.

`preflight/*_in_unit.json` and worker preflight records include the peer telemetry, reservation basis, projected total, and admission decision. Simulator task success remains a separate validation level.
