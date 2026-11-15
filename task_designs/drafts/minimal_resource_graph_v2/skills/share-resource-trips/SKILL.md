---
name: share-resource-trips
description: Plan processing and delivery when tools and carriers must also be returned, comparing material routes with shared resource trips and temporary reuse.
---

# Share material and resource trips

Separate terminal requirements from prerequisites. A tool or empty carrier may
reach its final place before the products do. A final-place requirement also
permits temporarily using that resource again, unless the task states otherwise.

Compare processing locations together with the resulting material form, loading
capacity, tool availability and return work. A plan that transports fewer pieces
can still lose by retrieving its resources on separate empty trips.

At each planned empty relocation, examine useful payloads already available:

- Complete a tool or empty-carrier return whose destination lies on that leg.
- Carry an empty carrier with a returning tool when both will be useful at the
  next work area, even when their ultimate destinations differ.
- Fetch a carrier together with its nearby tool, accounting for the tool's
  capacity usage and the later action needed to obtain it.

Treat these as candidate choices. Early return can create another retrieval;
extra loading can cost more than the saved travel. Keep a candidate without each
optional sharing choice, and compare complete travel and manipulation costs.

Expand the promising choices into legal actions from the actual initial state.
Track occupied hands, current carrier contents and remaining material. Apply
the task's exact cutting, retention, capacity and unloading rules. Include every
temporary placement and all final product, tool, carrier and hand conditions.
Select the cheapest complete plan actually checked; retain a better existing
plan when a new candidate is worse. Use “best compared plan” unless a lower
bound independently establishes optimality.
