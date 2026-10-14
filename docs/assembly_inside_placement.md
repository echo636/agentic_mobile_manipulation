# Placing a carried container and its contents

`place_inside` identifies the destination from the model's current RGB pixel.
For an object carrying rigid contents, the executor searches that destination's
fillable volumes using the entire assembly bounds. It does not change the selected
destination or use task-goal knowledge. The selected point orders nearby volumes;
it is not interpreted as the exact origin of the placed object.

Candidate centers account for the offset between object origin and geometry
center. Asset-supported resting poses within 45 degrees of the carried local
gravity direction can correct carry-induced tilt without flipping the payload.
Four world-up yaw rotations of these poses and the carried pose preserve all
carried relative poses. Candidates are distributed across volumes and orientations
instead of letting the first volume consume the complete search. Single objects
without a rigid payload retain the official OmniGibson sampler.

Candidate acceptance checks initial external collision, support contact, physical
settling, container root/joint motion and the official `Inside` relation. The
outer placement transaction verifies existing residents and original payload
relations after settling, and rolls back failures, including grasp ownership.
Private diagnostics record candidate volume, pose, rejection stage, contact bodies
where available, counts and elapsed time. These do not become model observations.

The assembly search has a 108-candidate / cooperative 30-second budget per action.
This is separate from the 50-agent-step episode policy. A limit returns a recoverable
placement error rather than consuming an unbounded tool call. Physical diagnostic
probes and CPU tests do not establish autonomous whole-task success.
