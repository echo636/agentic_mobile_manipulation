---
name: support-delivery-planner
description: Compare complete delivery plans for two or three co-located objects with confirmed support relationships and a public travel-cost model, using bounded executable candidate enumeration.
---

# Compare complete support-delivery plans

Use the helper for the supported abstract model: two or three objects start at
location 0 with an empty hand; each lower-index object can support higher-index
objects; every location permits staging; retained subtrees move with their root.
Final objects must rest separately on their assigned surfaces. The supplied
distance matrix is symmetric and metric; other actions cost one unit. A final
destination may be the source, or shared with another object's destination.

Build `parents`, `goals`, and `distances` from the public task. A parent of -1
means direct surface support. Keep the task's object indices. Confirm physical
support and retention before applying this model to observed objects; uncertain
capabilities or travel costs remain uncertainty, rather than invented inputs.

Call [scripts/plan_support_delivery.py](scripts/plan_support_delivery.py) with
the public model JSON. It enumerates at most 62 complete method candidates,
including changes to source support relationships. It charges loading, temporary
placement, return travel and delivery; an already delivered independent object
needs no extra action. It does not search for a globally optimal path.

For the structured two-round diagnostic, submit the model and a provisional plan
in the first response. The controller executes `plan_support_delivery` and returns
its actual result. In the final response, use a complete returned candidate if it
improves the provisional plan. Preserve the original goal and available actions;
check that no needed information was omitted or misrepresented. State “best
compared plan” unless an independently justified lower bound proves optimality.

The helper is an experimental planning capability, with additional candidate
computation. It is separate from the original text-only planning skill. It does
not operate the robot or establish native physical feasibility.
