---
name: failure-recovery
description: Recover from incorrect visual targets, rejected actions and partial execution without oracle state or endless retries.
---
# Failure recovery

1. Inspect the error category and the returned fresh RGB. A failed operation may have moved or released an object.
2. Separate your hypotheses: wrong pixel/view, wrong object, insufficient approach, hand occupied, inaccessible opening, placement sampling failure, or simulator failure. Do not treat a hypothesis as a reported object state.
3. Read the current plan/notes. Record what was tried and the visible result with remember, then revise the affected subgoals.
4. Select a bounded alternative: a fresh point on the same visible object, a closer approach, another viewpoint via look, opening a visibly closed container with an empty hand, or regrasping a visibly dropped item.
5. Never repeat an unchanged failed action more than once. After two alternatives fail, reassess the target and scene from RGB. Finish blocked with the actual limitation if no supported path remains.
6. Stale image/revision errors mean you must use the newest observation. Never replay an old pixel after movement.
7. Do not use evaluator output, hidden object names, arbitrary shell/Python or task reset to solve a failure.
