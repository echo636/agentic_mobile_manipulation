# Evidence rules

- Current pixels outrank earlier notes. Do not infer a hidden container's contents from task wording.
- Normalized pixel coordinates have x=0 left/x=1 right and y=0 top/y=1 bottom. image_ref must refer to the view you actually inspected.
- A successful navigation call only means the selected visual point was approached. Reinspect and mark the object itself before manipulating it.
- Raw RGB may leave state uncertain. Action feedback describes the requested operation, not other objects' states or final task success.
- A completed plan step is an agent claim supported by evidence, not a BDDL label.
- There is no oracle inventory or automatic object recognition service. Describe perceived objects and maintain your own visual notes.
