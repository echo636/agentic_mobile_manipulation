---
name: time-space-scheduling
description: Schedule timed processes, cross-room carrier batches, and area passes to minimize robot time without sacrificing completion.
---
# Time and space scheduling

After initial orientation, map out the instruction's dependencies, unattended timed processes, cross-room item transfers, and per-area local work before executing local tidying.

## 1. Unattended timed processes first

- When an appliance or device runs unattended after items are placed, access is closed as required, and power is switched on, start that process **before** doing independent local preparation, surface cleaning, cutting, or room tidying.
- Never call `wait` while any independent preparation, transport, cleaning, assembly, or tool storage remains anywhere in the scene. Travel and manipulation advance elapsed robot time automatically while the process runs in the background.
- For multi-stage processes where an item moves from stage 1 to stage 2, return to advance the item into stage 2 once independent work has covered stage 1, then use stage 2's run window for the remaining deliveries and cleanup.
- Call `wait` only when all independent work is already done and an active process still needs additional robot time to finish.

## 2. Bidirectional cross-room carrier batching

- One trip with an empty hand carries only one loose item, whereas grasping an open tray, basket, box, or board transports all items resting stably on or inside it together.
- Whenever 3 or more items or tools share a cross-room route (either outbound from storage/prep to a destination area, or inbound when returning used tools and containers), compare loading an available carrier at the source against individual round trips:
  - Each extra item loaded onto and unloaded from a carrier adds 2 manipulations (~20 s), so batching saves robot time whenever the avoided round-trip travel exceeds ~20 s (~10 m round trip).
- Remember to account for the carrier's own required final location and closed/open state after unloading.
- Open any closed destination container while your hand is still empty before picking up an item or loaded carrier.

## 3. Single-pass area execution and state tracking

- Group local tasks by room or workstation so you finish all ready pickups, placements, cleanings, and switches in the current area before walking to a distant area.
- Keep a concise mental or written ledger of:
  1. Which timed processes are currently running or already completed;
  2. Which storage doors, drawers, or lids have already been stocked and verified closed.
- Once a storage compartment has all its required contents inside and you have confirmed it closed in RGB, do not reopen or re-operate unrelated or already verified doors across the building during final review.
