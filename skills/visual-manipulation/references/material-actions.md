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
| `pour` | Held vessel, board or ready dispenser; select the open receiving vessel |
| `scoop` | Held open vessel; select a source vessel or surface supporting loose material |
| `cut` | Compatible slicer; select the sliceable object or piece |

Toggle a tool before grasping or select its visible body while carrying it. If the held tool obscures its own selectable body, place it on a support, toggle it with an empty hand, then regrasp. An empty, off or incompatible tool needs a state/tool change; retrying other target pixels will not prepare it.

For tiny or cropped tools, obtain a view of the body instead of guessing from a cap or tip. A `fixed_object` result suggests a different object was selected. `particle_target` means loose material cannot be grasped directly; use a suitable tool or container.

Material actions affect the selected object's simulator material state, not just a patch around the pixel. Successful spray feedback supports treatment even when the coating is difficult to see. Track distinct treated objects and complete any remaining goals.

Choose each cut from the selected item's current form and required final form. One successful cut performs one transition; read the returned form and update the item ledger. Pieces already in the required form need no further cutting; further cutting may irreversibly change them into loose material. Replacement invalidates the old selection: identify the next target in current RGB, resolving an uncertain form or identity before cutting. Plan how to transfer the resulting pieces or material and preserve processing, delivery and cleanup dependencies.

`pour` and `scoop` move a bounded portion and retain the held object. Existing material keeps its identity and quantity; an authored dispenser supplies only its own material while ready. Read whether source material remains: one call need not empty a source or fill a destination. Closed containers must be opened first. These actions transfer loose material; rigid objects use grasp and placement.
