# Pressing a toggle region

Read the device's current `local_states` before approaching. If already in the desired
state, do not issue a toggle merely to demonstrate motion. Identify its observed
`togglebutton` functional region and eligible finger links. A semantic device label
alone does not specify a precise contact pose.

Choose the approach station from current surround RGB; after navigation, recompute
region and finger positions. Define a safe pre-contact pose, inspect the active wrist
RGB, and convert the intended fingertip point into an EEF-link pose using its offset.
Author the press approach, dwell and retreat yourself. The native trigger requires
actual finger/device contact together with activation-region overlap and dwell.
EEF proximity or a closed gripper does not establish that condition.

Read both `reached` and `verified`, `state_before/state_after` and contact diagnostics.
A partially unreached path can nevertheless activate the device; re-read state before
retrying to avoid switching it off. Conversely, reported contact can occur away from
the activation region and leave the device off. Retreat and wait; require the desired
state to persist and let the independent evaluator score the full task.

Observed precedent: public `turning_on_radio`, instance 301, seed 0, Astra/xhigh,
OmniGibson IK, four surround views (before wrist cameras were added). Five press
attempts were needed; the final one returned `reached=false, verified=true` with
state changing false to true. Official task success was true. This is a demonstrated
recovery pattern, not a portable coordinate sequence or a statistical success rate.
