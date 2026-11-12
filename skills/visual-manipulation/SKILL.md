---
name: visual-manipulation
description: Choose manipulation skills and verify progress from RGB and action feedback.
---
# Manipulation workflow

Initialize and inspect the four returned views. When the intended object is visible, request the operation directly; the robot can approach it if needed. Use navigation for exploration, travel or a different approach after failure. Check the other current views before moving to find an object cropped out of one view.

Read each relevant resource once; reread only if its instructions are no longer available in context:

- `visual-exploration`: finding objects and traveling between areas.
- `pick-and-place`: carrying, placing and attaching objects.
- [references/material-actions.md](references/material-actions.md): cleaning, treating, soaking or cutting, before using those actions.
- `failure-recovery`: diagnosing a rejected or partially executed action.
- [references/evidence.md](references/evidence.md): deciding whether uncertain results support completion.

After each action, inspect returned RGB and feedback. Confirm the intended object and effect, and track completed and remaining work. Use `wait` with a null target to let heating, washing or drying progress; switching a device on does not finish its process.

Before cooking, plan the method and its required inputs, such as liquid for boiling; heating longer cannot replace a missing prerequisite. Check each ingredient's required state separately: one component changing appearance does not establish that the whole mixture is cooked.

Before `finish`, reconcile the instruction's quantities, destinations, relations and final device states with the latest relevant evidence. Continue unresolved work when possible. Report achieved only when all required results are supported; otherwise state what remains. Receive `closed=true` before final text.
