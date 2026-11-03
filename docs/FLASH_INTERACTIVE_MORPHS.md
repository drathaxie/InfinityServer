# Interactive Flash item conversion

The requested morph behavior is a click that changes the equipped artwork, as in
Elemental Dracomancer. Replacing the player's head is a separate feature.
`armor.py --source-head` preserves an authored head (`--morph` remains an alias).
`--interactive-morph --rgb-cc --faithful-effects` compiles supported click controls.

## Elemental Dracomancer source contract

The captured public sources are `classes/M/DracoMancer2.swf` (48,552 bytes) and
`classes/F/DracoMancer2.swf` (48,080 bytes). Their URLs and SHA256 hashes are in
`outputs/flash_converter_trial/interactive_morph/source_manifest.json`.
Existing item records 11272, 11313, 29030, and 29031 share native bundle 5091,
`armors/5091_DracoMancer2.unity3d`.

Both sources contain three independent clickable timelines:

| Part | Source frames | Stopped poses | Click behavior |
| --- | --- | --- | --- |
| Head | 8 | 1 and 8 | Resume parent, transform head, click again to return |
| Chest | 8 | 1 and 8 | Resume parent, unfold wings, click again to return |
| Hand | 8 | 1 and 8 | Resume parent, transform hand, click again to return |

The table uses Flash's one-based frame numbers. Conversion metadata uses 0 and 7.
Unity has separate front/back hand slots, giving four controls per gender.
The local source button installs a `MouseEvent.CLICK` listener whose handler is
exactly `MovieClip(parent).play()`. Generated inspector properties such as
`strFrame="Dogear"` do not execute an avatar action: this button never reads them.
The compiler validates those setters rather than inventing an external command.

## Conversion and runtime

`interactive_morph.py` recognizes a bounded AS3 subset. It reads frame callbacks,
explicit stops, the parent-play button, and the button's HitTest-state geometry.
Unknown executable callbacks fail conversion. Hit regions currently use the
transformed bounds of each HitTest shape; irregular shapes have conservative
bounds rather than pixel-perfect hit testing.

Every controlled parent pose is exported with independent child animation layers.
The hand's effect has a 40-frame clock; the source eyes have an 87-frame clock.
Stopping the morph selector does not stop these clocks. The separate wing timeline
starts at its authored `stop()` and is held there; an unused `Move` label does not
by itself authorize playing that timeline.

The FlashRig shader selects pose-specific layers using `_MorphFrame` and each
layer's `_LnMorph`. Unchanged prefix artwork can be shared between poses. Static color-custom clips stay intact inside an uncolored wrapper, preserving
the inherited parent color transform around the source color callback. Flattening
that transform had made an invisible hand visible and whitened the transformed
effect. Source comparisons caught that before publication; the wrapper preserves
the child clock without baking its complete animation into every parent pose.

The bundle retains native `ArmorBones_m` / `ArmorBones_f`, `ItemSlots`,
`CustomizableSlot`, and `NoFlashWhenHit` contracts. Declarative interaction metadata
travels in the native AnimatorController name with a `FlashMorph:v1:` prefix.
There is no new serialized runtime MonoBehaviour in the item bundle.

`InfinityLoader/FlashRigMorph.cs` attaches the controller after native slot
reparenting. Clicks use the exported local hit regions, account for sprite flips,
and select one overlapping control by sorting layer/order. UI clicks are excluded.
Only the selector property changes; existing color property blocks, shared
materials, body animation, and child Animators are retained. Palette handling now
also hooks `LoadArmorSlots`, supporting direct Base/Trim/Accessory/Skin/Hair/Eye
response colors on these armors.

Color probes whose location is absent from a rendered subtree reuse its black
render. This reduces repeated rasterization without removing any color response.
The release uses a BC7 constant coefficient atlas and DXT1 RGB slope coefficient
atlases. Uniform zero coefficient atlases collapse to 4x4. Direct palettes read
all six colors from runtime properties, so their native compositor masks use
DXT1; probe-decoded palettes retain exact RGB24 masks. Color interpolation between source probes remains an
approximation and is subject to the existing source and GPU fidelity gates.

## Verification artifacts

Artifacts are under `outputs/flash_converter_trial/interactive_morph`:

- `control_programs.json`: source symbols, independent controls, and hit regions.
- `script_semantics_validation.json`: both genders advance through poses 1-7,
  hold at 7, and return to stopped pose 0 after the next click.
- `ElementalDracomancerRelease/conversion/validation.json`: every exported parent
  pose compared with original source display lists at sampled child times.
- `run_morph_validation.py` exercises the exact loader component on a loaded
  bundle after native-style slot reparenting, including hit regions, return
  clicks, clone isolation, child clocks, palette retention, and shared materials.
- `morph_gpu_validation.json` compares activated-pose GPU captures with CPU
  reconstruction separately from the ordinary initial-pose bundle validation.

All other ElementalDracomancer conversion folders are diagnostic iterations and
must not be published. The release folder is `ElementalDracomancerRelease`. Passing editor tests
does not constitute observed player input or appearance in the running client.
Publication and real-client proof must be recorded separately.

## Native publication and test grant

Native10 published bundle 5091 version 5 for existing records 11272, 11313,
29030, and 29031. File: `armors/5091_DracoMancer2.unity3d`, CRC 880979046,
SHA256 `35fa5df33ee720bdc3a3f586bf63514550d0e65c557d4f80fd8c5e0136f548da`.
The file is 5,369,596 bytes; serialized textures total 65,765,264 bytes, below
64 MiB. The independent native-component and dependency audit passed.
All 320 activated GPU poses passed (worst mean 1.333/255, worst p99 12.038/255).
The exact loader component passed 8 controls, 56 transitions and 8 return cycles
on this final bundle. Public file hash, API version, native wire records, service
health and unchanged game PID passed. Inventory and existing item fields were
preserved during publication. Backup: `/opt/infinity/backups/native10-20261013T062456Z`.

At the user's subsequent request, existing armor 11313 was granted unequipped to
Redux (character/account 8), inventory row 10003029. Inventory grew from 224 to
225; all prior inventory rows were preserved, and committed readback passed.
Grant backup: `/opt/infinity/backups/native10-redux-grant-20261013T062833Z`.
Publication, verification and grant evidence are under
`outputs/flash_converter_trial/native_replacements/native10/`.
Running-client clicks and appearance remain awaiting user observation.

## Observed client metadata failure and loader repair

The first player test did not transform. The installed player log repeatedly
reported `Invalid Flash morph: Invalid Flash morph program`. Bundle metadata
independently decoded to the expected 8 frames, 24 fps, stops 0/7 and 8 hit
frames. The existing editor test had compiled the DTO into Assembly-CSharp;
that did not prove Unity's native JSON deserializer handled the Doorstop-loaded
DTO in the shipped player. The precise native serializer failure remains an
inference; the live rejection itself is observed.

The loader now uses the game's managed Newtonsoft.Json reader for its own
metadata. It also restricts the UI input gate to GraphicRaycaster results:
physics raycasts on the equipped character must not block its source button.
Bad metadata reports field counts and is rejected once per controller, avoiding
repeated log spam. Clock validation rejects nonfinite fps and supports sources
that initially play until their first explicit stop.

`managed_json_runtime_validation.json` passes all 8 released controls, 56 poses,
8 return cycles, native slot reparenting, source hit geometry, palette and
material retention, independent instances, continuing child clocks, initial
four-form playback, and deterministic physics/UI input routing. Loader build
passed with 0 warnings and 0 errors. The Euler rotation repair source is
unchanged. Loader SHA256:
`9c9d8bc213e9ea4a8da03813ebf83f06283b32f36f361fd43ab5f8d28670bb50`.
Public DLL/hash and installed DLL match. Server services stayed active and game
PID stayed unchanged. A full client restart is required; running-player clicks
with the repaired loader remain unobserved.

Loader backup: `/opt/infinity/backups/flash-rig-preview-loader-20261013T120730Z`.
Local backup: `C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest/InfinityServer-backup-preview-20261013T120744Z`.

Native09 has also completed: 54 audited replacements published from 64 prepared
sources. Total verified native replacements: 737 bundles and 771 existing item
records. Native09 backup: `/opt/infinity/backups/native09-20261013T083217Z`.

## Further conversion work

Native11 is the next universal batch, targeting 64 sources. Its completion must
be read from `native_replacements/native11/pipeline_status.json`; starting the
job is not publication proof.

Flame Usurper Blades is an unpublished interactive-weapon prototype. The source
has 17 parent frames, initial playback and stops at zero-based frames 1/5/9/13.
Dense effect poses would expand to 3,058 layers. The compiler can now rasterize
each complete pose over its uncapped 50-frame child clock, retaining filters and
painter order in 17 layers. Source validation of all 17 poses at 5 child times
passed (85 comparisons, worst mean 11.74/255, no clipped alpha). The resulting
4096x10028 atlas still requires memory/layout reduction, independent bundle and
GPU audits, and actual weapon click-cycle testing before any publication.
These prototype changes do not alter the released Dracomancer bundle.

## Random-initial timelines

The compiler now recognizes the exact bounded random-initial callback used by
Axe of the EbilCorp Knight. Metadata marks all parent frames stopped and assigns
an initial rounded-uniform selector. Click regions are empty; no click behavior
is invented. The loader chooses independently for each equip/preview instance
and retains the result while child effects animate. The managed parser, input
gate and Euler repair remain. See `FLASH_CONVERSION_EBILCORP_KNIGHT_AXE.md` for
source, runtime, publication and Redux grant proof. Current distributed loader
is `9a42a2fe`; a full client restart is required for its random selector support.
