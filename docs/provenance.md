# Provenance

This repository starts from an empty Git history. No Python module is imported from the three authors' projects and no fork history is inherited.

|Reference|Reviewed revision|Ideas used|
|---|---|---|
|dadwadw233/habitat-gs, jinkai/harness|0815cf234ee591bacd8017e9b1def4fac13e649b|Shared tool contracts, fresh observations, evidence-bearing memory, isolated executor, episode artifacts|
|llz3724/agentic_mobile_manipulation, my_mm|619605e362cecf0e54eedd4dbe34636671d5cf15|Single simulator owner, frozen reads, practical official challenge lifecycle and robot/template compatibility|
|Zhouwenb0/iGibson_Agentic_System, main|6c1edd605619ee30db43533baa9295f67e714bb2|Explicit requirements, stateful memory, iterative feedback|
|StanfordVL/BEHAVIOR-1K, v3.9.2|b1979916ec1549b10a4e65e630bc6504a9af1b00|Runtime dependency: official Evaluator, TaskMetric, symbolic primitives, BDDL and assets|

The OmniGibson adapter uses the public library APIs plus version-pinned internal interfaces (`_get_obj_in_hand`, task goal evaluation, traversal map erosion). It independently constructs configuration and the external-policy evaluator adapter. These internals are intentionally confined to one module. All source repositories and simulator/data licenses continue to apply to their respective dependencies; no upstream assets, models, decryption keys, or vendor source are redistributed here.

Protocol references: [MCP server guide](https://modelcontextprotocol.io/docs/develop/build-server), [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling), [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode), [BEHAVIOR evaluation](https://behavior.stanford.edu/challenge/evaluation.html).

Known upstream gaps handled explicitly: symbolic navigation depends on an absent CuRobo planner and a mismatched inherited method signature; this adapter supplies a separate ideal navigator. Symbolic manipulation does not require distance or container access by default; the harness adds near-target and closed-container checks. The first tested API is 3.9.2; 3.9.3 compatibility is a distinct future validation item.

Additional source inspection on the exploration branch: [lvzhang primitive audit](lvzhang_primitive_audit_20261001.md), `my_mm` at `c00938f8ccdd59974ecaddd28cfd7ce86fa6ba0f` (2026-10-01). This is a later review of its code-composition interface, not a claim that the current executor imports that implementation or has reproduced its task results.
