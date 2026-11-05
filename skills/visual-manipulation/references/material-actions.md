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

For cutting, keep unprocessed inputs, the working batch and finished outputs in separate places. Use a clear, stable work surface with room for the tool; remove obstructing packaging. Load only a batch whose individual items will remain distinguishable after cutting. Process that batch to the requested form, transfer it together using a board or vessel, and clear the work surface before loading more. If pieces become indistinguishable, put down the tool and separate them before the next cut; reduce the next batch if needed. Bring supplies and the receiver nearby to avoid extra trips.

One successful cut performs one transformation. Read the returned form and stop cutting each item when it reaches the required form; another cut may irreversibly turn a piece into loose material. Select the next unprocessed item from current RGB, not the previous cut's position. If its form is unclear, inspect another view or isolate it before cutting. Track how much has reached the required form and destination.

`pour` transfers loose objects and material from the held vessel or board into the selected open receiver. For a vessel, select its exposed rim or wall, clear of contents and neighboring supports. Read `receiver_kind`: a surface transfer does not establish that contents reached the intended vessel. Objects and loose material can also be poured onto a stable receiving surface; inspect the result after physical settling. It attempts the loose objects together and may leave a remainder when space is insufficient. `scoop` transfers a material portion into the held vessel. Both retain the held object. Existing contents keep their identities; a ready dispenser supplies only its authored material. Read transferred and retained object counts, remaining objects and `source_has_more_material`: one call need not empty the source. Nested vessels keep their contents; attached parts are not poured. Open closed containers first.
