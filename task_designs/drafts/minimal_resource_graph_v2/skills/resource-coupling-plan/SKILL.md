---
name: resource-coupling-plan
description: Plan manipulation tasks whose goals share movable supports, carriers or tools, comparing how temporary resource arrangements affect complete delivery and restoration cost.
---

# Plan with shared resources

Separate required final relations from temporary useful relations. A deliverable
can also carry another deliverable before reaching its own destination. Record
current support or containment edges, legal alternative edges, destination
locations, hand availability and any required resource return.

Build candidate organizations before committing to a goal order. Include direct
delivery using existing relations and rearrangements that combine items sharing
part of a route. For a small set, compare the legal support arrangements and
destination orders explicitly. Nested support can preserve a smaller assembly
after its outer carrier is delivered. A carrier with an early destination may
therefore be useful without carrying every item to its final destination.

For each candidate, follow the changing resource graph: picking a child detaches
it from its parent; picking a support moves its whole retained subtree. Decide
which subtree travels on each leg and where it is split or recombined. Repacking
at the source or at a delivery point is worthwhile only when the remaining travel
savings pay for its handling. Some items should travel directly even when
combining them is possible.

Compare complete executable plans, including every pick, placement, temporary
staging operation, empty return, and required resource restoration. Carrying a
loaded support to its own final destination can finish that support's goal with
one placement; count only the subsequent detachments that are actually needed.
Keep one unbatched or unchanged-arrangement candidate as a check on packing cost.

Select the lowest-cost complete candidate you have verified. Replay hand state,
locations and support relations through the chosen sequence, checking that all
terminal conditions hold simultaneously. In an observed environment, use only
confirmed support and transport capabilities, and update the remaining plan
when execution changes those relations. Describe the result as the best compared
plan unless a valid lower bound establishes optimality.
