# Step log format (`.msteps`)

> **This file is optional, and most people should not bother with it.**
> Without a step log, Mousestorm works out the operations from the finished geometry, and that is enough for sharing, fun and everyday showcases. A log is only worth the effort for a professional demonstration, where the film has to show exactly what was done, in the order it was done.
>
> **这个文件是可选的，绝大多数人不需要。** 不提供时，Mousestorm 会根据模型几何自动推断操作，日常分享和展示完全够用。只有专业演示、需要影片如实呈现“做了什么、按什么顺序做”时，才值得记录。

## Using it

1. Before modelling, give this document to the AI and say:

   > While you build this model, keep a Mousestorm step log as described in this document. Save it next to the `.blend` with the same name and the extension `.msteps`. Record only what you really did.

2. Give both files to Mousestorm: drop the `.msteps` file on the app together with the `.blend`, or run:

   ```bash
   mousestorm model.blend --steps model.msteps
   ```

3. Optionally check the log first:

   ```bash
   mousestorm check-steps model.msteps
   ```

## Rules for whoever writes the log

- Write UTF-8 JSON. Name the file after the `.blend`, with the extension `.msteps`.
- Add a step when you finish an operation that changes the model. One step per meaningful operation; do not record individual vertices.
- Record only what you really did. If you do not know a value, leave it out. Do not invent steps to make the log look busy.
- Use each object's **final** name, as it appears in the saved `.blend`. If you rename an object, update its earlier steps.
- List an object's steps in the order you performed them, starting with `add` or `duplicate`.
- Do not record materials, lights, cameras or rendering. Those are read from the `.blend`.

If you build through a Python script, this is enough:

```python
import json

STEPS = []

def step(op, obj, **fields):
    STEPS.append(dict(op=op, object=obj, **fields))

# after each operation, e.g.
# bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.5, depth=4)
step("add", "Tower", primitive="cylinder")

# at the end
with open("model.msteps", "w", encoding="utf-8") as f:
    json.dump({"format": "mousestorm.steps", "version": 1, "steps": STEPS}, f, ensure_ascii=False, indent=1)
```

## The file

```json
{
  "format": "mousestorm.steps",
  "version": 1,
  "file": "lighthouse.blend",
  "generator": "the tool or model that wrote this",
  "steps": [
    {"op": "add", "object": "Tower", "primitive": "cylinder", "call": "bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.5, depth=4)"},
    {"op": "move", "object": "Tower", "value": 2.0, "value_name": "Z"},
    {"op": "loop_cut", "object": "Tower", "value": 6},
    {"op": "extrude", "object": "Tower", "value": 0.35, "where": {"bbox": [[-1, -1, 3.5], [1, 1, 4.5]]}},
    {"op": "bevel", "object": "Tower", "value": 0.02},
    {"op": "sculpt", "object": "Rock", "brush": "Clay Strips", "value": 0.4, "value_name": "Strength"},
    {"op": "edit", "object": "Rock", "label": "Decimate Planar", "value": 0.1, "value_name": "Ratio"},
    {"op": "duplicate", "object": "Rock.001", "from": "Rock"},
    {"op": "modifier", "object": "Roof", "modifier": "Bevel"}
  ]
}
```

`format`, `version` and `steps` are required. `file` and `generator` are informational.

### Fields of a step

| Field | Required | Meaning |
|---|---|---|
| `op` | yes | One of the operations below. |
| `object` | yes | The object's final name in the `.blend`. |
| `label` | only for `edit` | The name shown in the film, up to 60 characters. Replaces the default name. |
| `value` | no | A number shown next to the name: a distance, a count, a strength. |
| `value_name` | no | What the value is, such as `Width`. Each operation has a default. |
| `call` | no | One line shown in the film's log, up to 240 characters, such as the Python call you ran. |
| `where` | no | `{"bbox": [[x, y, z], [x, y, z]]}` in world coordinates: the region this step worked on. |

Only for some operations:

| Field | With | Meaning |
|---|---|---|
| `primitive` | `add` | `cube`, `uv_sphere`, `ico_sphere`, `cylinder`, `cone`, `torus`, `plane`, `grid`, `circle`, `monkey`, `curve`, `text`, `metaball`, `empty_mesh` |
| `from` | `duplicate` | The object it was copied from. |
| `brush` | `sculpt` | The brush name. |
| `modifier` | `modifier` | The modifier's name or type. |

### Operations

| `op` | Shown as |
|---|---|
| `add` | Add Cube, Add Cylinder and so on |
| `duplicate` | Duplicate Objects, or Duplicate Linked when the mesh is shared |
| `move`, `rotate`, `scale` | Move, Rotate, Resize |
| `extrude` | Extrude Region |
| `inset` | Inset Faces |
| `bevel` | Bevel |
| `loop_cut` | Loop Cut |
| `subdivide` | Subdivide |
| `knife` | Knife Topology |
| `bridge` | Bridge Edge Loops |
| `merge` | Merge by Distance |
| `shrink_fatten` | Shrink/Fatten |
| `smooth` | Smooth Vertices |
| `sculpt` | The brush name |
| `modifier` | Add Modifier: name |
| `edit` | Anything else: give it a `label` |

## What the film does with it

- **Names, values and `call` lines** come from the log.
- **The order objects start in** follows the order they first appear in the log, and each object's steps play in the recorded order. Different objects still overlap: that is the swarm.
- **The geometry always comes from the `.blend`.** A log can never change the model. An object's visible change is shared among its edit and sculpt steps: a step with `where` takes the part inside it, the rest goes first to steps of the matching kind, then evenly in step order.
- **A step with nothing visible to play**, such as a loop cut, still appears: the mouse does it and its name and value are shown.
- **Anything the log leaves out still comes from inference.** An object it does not mention is inferred as usual, and placement the log omits is still shown.
- **Automatic pacing still applies**, measured from the real change in the geometry. Small adjustments run faster than large ones.
- **Names that match no object** are ignored and listed under `step_log` in the film's `_report.json`.

An error in the file stops the film and names the step at fault, so a mistake cannot pass unnoticed.
