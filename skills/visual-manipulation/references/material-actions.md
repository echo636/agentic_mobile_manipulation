# Materials, cleaning and cutting

Identify the held tool and its readiness before selecting the target. A successful toggle identifies only a switchable object; confirm it is the same tool you grasp.

| Action | Required tool and target |
|---|---|
| `wipe` | Compatible contact cleaner; select the dirty surface |
| `sweep` | Broom; select the floor to clean |
| `vacuum` | Switched-on vacuum; select the dirty target |
| `spray` | Switched-on atomizer/sprayer containing compatible material; select the object to treat |
| `soak` | Absorbent tool; select a visible fluid source or filled container |
| `spread` | Tool already containing absorbed material; select the surface to coat |
| `pour` | Held vessel, board or ready dispenser; select an open vessel or a receiving surface |
| `scoop` | Held open vessel; select a source vessel or surface supporting loose material |
| `cut` | Compatible slicer; select the sliceable object or piece |

Toggle a tool before grasping or select its visible body while carrying it. If the held tool obscures its own selectable body, place it on a support, toggle it with an empty hand, then regrasp. An empty, off or incompatible tool needs a state/tool change; retrying other target pixels will not prepare it.

For tiny or cropped tools, obtain a view of the body instead of guessing from a cap or tip. A `fixed_object` result suggests a different object was selected. `particle_target` means loose material cannot be grasped directly; use a suitable tool or container.

Material actions affect the selected object's simulator material state, not just a patch around the pixel. Successful spray feedback supports treatment even when the coating is difficult to see. Track distinct treated objects and complete any remaining goals.

For cutting, prefer a cutting board or clear, stable work surface. Keep unprocessed items separate from finished pieces. Cut one identifiable item at a time; accumulate finished pieces in a separate region or carrier for a batch transfer when practical. A board or vessel can be carried and poured into the receiver. A vessel can also pour loose objects onto the work surface before processing. Do not cut a mixed pile whose forms you cannot distinguish.

Choose each cut from the item's current and required final form. One successful cut performs one transition; read the returned form and update the item ledger. Finished pieces need no further cutting, which may irreversibly turn them into loose material. Identify each next target in current RGB. If its identity or form is uncertain, change the view or separate it before cutting. Plan the transfer of resulting pieces or material and preserve processing, delivery and cleanup dependencies.

`pour` transfers loose objects and material from the held vessel or board into the selected open receiver. Objects and loose material can also be poured onto a stable receiving surface; inspect the result after physical settling. It attempts the loose objects together and may leave a remainder when space is insufficient. `scoop` transfers a material portion into the held vessel. Both retain the held object. Existing contents keep their identities; a ready dispenser supplies only its authored material. Read transferred and retained object counts, remaining objects and `source_has_more_material`: one call need not empty the source. Nested vessels keep their contents; attached parts are not poured. Open closed containers first.
