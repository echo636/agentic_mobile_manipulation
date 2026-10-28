---
name: visual-manipulation
description: Choose manipulation skills and verify progress from RGB and action feedback.
---
# Manipulation workflow

Initialize, inspect the four returned views, then choose the next action. Read `initialize.control` for the active protocol. In `target_locked_approach_v2`, directly request the operation when its target is clearly selectable; the executor handles approach and stance. Use macro navigation for exploration, another task area, or an approach change justified by feedback. Check the other current views before moving to recover a cropped target.

Read each relevant resource once; reread only if its instructions are no longer available in context:

- `visual-exploration`: finding objects and traveling between areas.
- `pick-and-place`: carrying, placing and attaching objects.
- [references/material-actions.md](references/material-actions.md): cleaning, treating, soaking or cutting, before using those actions.
- `failure-recovery`: diagnosing a rejected or partially executed action.
- [references/evidence.md](references/evidence.md): deciding whether uncertain results support completion.

After each action, inspect returned RGB and feedback. Confirm the intended object and effect; preserve useful progress in task notes. For heating, washing or drying, allow simulation time using `wait` with a null target. A successful toggle alone does not establish a finished process.

Before `finish`, reconcile the instruction's quantities, destinations, relations and final device states with the latest relevant evidence. Continue unresolved work when possible. Report achieved only when all required results are supported; otherwise state what remains. Receive `closed=true` before final text.
