# Four fixed cameras and asynchronous acquisition

User requirement: front/back/left/right at the same simulation state; no body rotation, no wrist views. Native robot sensors are excluded and replaced by four explicit render products attached kinematically to the base frame. The calibrated local camera basis uses X forward and Z up; yaw offsets are 0°, 180°, +90°, -90°. See observations/rig.py.

The async job state machine is planned → running → passed/failed/cancelled. Submission returns before rendering. The owner-thread scheduler services queued tools between job phases. Capture performs render-only ticks and reads all four sensors at one frozen physical state; image writing and publication happen as one batch. Per-capture private audits verify unchanged base pose, joint state and env.step. Actual MCP ImageContent is hash-checked. Pending jobs consume no motor-action budget.

An ordinary motion can execute before or after a pending capture. A cached result is marked stale after another capture or motion. No private depth or pose enters model results. A failed job releases its active slot; finish cancels any pending job. Cancellation cannot interrupt a render already in progress.

Jinkai reference: dadwadw233/habitat-gs, branch jinkai/harness, commit 0815cf234ee591bacd8017e9b1def4fac13e649b. Its four-direction image_ref organization informed this interface. The inspected _get_panorama implementation turns and restores the agent; that mechanism is not used here. This repository implements the user's explicit four-fixed-camera requirement and does not change the navigation repository.

Validation is recorded in the project batch operations/async_surround_20260930. CPU tests cover nonblocking submission, interleaving, cancellation, stale refs, side-camera targets, failures, owner-thread enforcement and camera axes. Real simulator and model results must be recorded separately; passing these tests is not a task success claim.
