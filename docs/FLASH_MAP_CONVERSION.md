# Flash map conversion: native structure inspection

Inspected November 2, 2026. This is the first map investigation, not a finished
map converter. No server, live catalog, client mod, or shared Unity project was changed.
That statement describes this initial native inspection. The later user-requested
`shadowtest` publication is recorded in the Shadowfall prototype document.

The selected pilot is now **Shadowfall**. Its first local two-room structural
prototype and exact proof boundaries are documented in
[Shadowfall map prototype](SHADOWFALL_MAP_PROTOTYPE.md). Greenguard remains native
format reference material, not the selected conversion target.

## Verified native layout

Two actual Unity AssetBundles were decoded with UnityPy. Every GameObject,
transform, relevant MonoBehaviour, collider and sprite renderer was inspected;
both reports have zero read failures. Bounds below use each MapCell's referenced
ConfinerCollider transformed through its full parent hierarchy into world space.

| Bundle | Cells | Camera bounds | Internal transitions |
| --- | --- | --- | --- |
| Battleon, local version 12 snapshot | Enter, R2, R3 | Enter: 143.828 x 24.581 units; R2/R3 about 46.6 x 23.95 | Enter goes to R2/Down and R3/Down; return pads are Up and Tent |
| Greenguard West, version 8 downloaded from the public live CDN | Enter, R2 through R13 | Enter: 34.482 x 17.692; other cells: 46.012 x 23.608 units | 24 directed MapGoToCell records, including horizontal and vertical connections |

Battleon's wide main cell is consistent with continuous sideways travel. It has
45 SpriteRenderers within that cell, layered background/parallax objects, a
walkable polygon, blockers, and spawn pads. It is not one flattened background.

Greenguard West demonstrates that horizontal travel can still change cells:

* Enter's left trigger is at world x=-13.365 and targets R2/Right.
* R2's right trigger is at world x=22.557 and targets Enter/Left.
* R2's left trigger targets R6/Right, with a reverse R6-to-R2/Left trigger.
* R2 also targets R3/Down, with R3 returning to R2/Up.

Each of those destinations is a distinct MapCell with its own camera confiner,
walkable polygon, blockers, and spawn pads. TargetSpawnPad describes the arrival
pad, not necessarily the direction of the source exit. For example, R9's
R6/Left trigger is near the bottom, despite the arrival pad being named Left.
Do not classify or stitch a connection from its pad name alone.

The earlier proposed rule of joining all horizontal Flash rooms was premature.
The observed native structure supports both wide continuous cells and explicit
horizontal transitions. These two sampled versions do not establish a universal
rule for every map, nor that either version is the newest available.

## Client contract checked in the local decompilation

* MapPrefab is a state machine; named direct children carry MapCell components.
* MapPrefab.changeCell finds the named child and changes state.
* MapCell uses its own ConfinerCollider, entityScale, entitySpeed, and spawn pads.
* ClickableWalk raycasts the Walkable layer; artwork alone cannot enable walking.
* MapGoToCell sends a cell transition with TargetCell and TargetSpawnPad.
* MapChangeArea transfers to another map, separately from cell transitions.
* BGParallax preserves separate front, middle and back layers.

The local decompilation and serialized bundles are separate snapshots. Some
serialized fields differ, so the eventual builder must target the installed
client's contract explicitly rather than copying every field blindly.

## Flash pilot and resulting conversion approach

The preserved Greenguard West SWF parses successfully at 24 fps, 960 x 495 stage
pixels. It has 14 candidate gameplay labels (Enter and 13 labels containing West),
plus Wait and Blank. Its decompiled inspector methods contain literal cell/pad
destinations, monster pad IDs, NPC linkages, and scale/speed settings. This is not
a one-to-one naming/count match with the inspected native map.

The source also has a dedicated walking-area SimpleButton whose hit state points
to shape 886. A selected JPEXS export recovered that shape as SVG. Source Pad,
Plate, Setup, and shadow components hide their authoring graphics in frame
scripts; a renderer that ignores those scripts will draw debugging artwork.

The conversion approach is to extract a source cell into explicit visual and
gameplay data, preserving its full affine transforms, draw order, source hit
shapes, spawn positions, and declared destinations. Keep scripts and unresolved
behaviors in an audit. Build one cell locally before connecting the graph.

Preserve source cell boundaries initially. If we choose to join horizontal rooms
into a continuous cell later, that is an authored redesign requiring seam,
camera, collision, spawn, monster placement, and server-coordinate validation.
It must not happen implicitly because an exit pad is named Left or Right.

## Reproduce the native inspection

```powershell
python scripts/swf2unity/inspect_map_bundle.py <map.unity3d> --out <report.json>
```

The inspector is read-only with respect to the input bundle. It emits source
SHA-256, per-cell component data, full collider type trees, transform hierarchy,
world-space box bounds, parallax references, and explicit transition fields.
It rejects duplicate object IDs across serialized files instead of resolving
them ambiguously. Required Python packages: UnityPy and numpy.

Local evidence is under `outputs/map_converter_trial/` (gitignored):

* `native_inspection/battleon.json` and `battleon-hierarchy.txt`
* `native_inspection/greenguardwest.json`
* `native_inspection/5_map-greenguardwest-v8.unity3d`
* `greenguardwest/source.swf`, exported ActionScript, and walking shape SVG

Native source SHA-256:

* Battleon v12: `f8af0f116fdb8f974cd707780d2caa7d0d46e60b785a0b6765b989008908aeb2`
* Greenguard West v8: `7e3debcd9d94819ad6eaf23439542c0a90fabd8763c60f5a4dfdb3049dcc8b0d`

Validation performed: successful structural inspection of both real bundles;
successful Flash parse and ActionScript/walking-shape export. No converted map
bundle, Unity preview, client traversal, or live gameplay has been demonstrated.
