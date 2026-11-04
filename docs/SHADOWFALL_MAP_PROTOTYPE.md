# Shadowfall map conversion: first structural prototype

November 2, 2026. The selected conversion pilot is Shadowfall, replacing the
earlier Greenguard proposal. Native map inspection remains reference material.

## Version 8 published: lore effects for the fallen dracolich fortress

November 4. Lore anchor: Shadowfall is the seat of the Shadowscythe (Sepulchure's
undead army), now ruled by Gravelyn; the skeleton crashed into the entrance is
Sepulchure's fallen flying dracolich fortress. Effects are positioned in
`map_configs/shadowfall-layout-v8.json` (`effects`, source px, optional `after`
layer and parallax `group`); banners are `sway` groups.

* **Dracolich eye** (Enter): AE `npcs/78866_dracolich_bone` v2 eye glow, ported,
  visible only through a `SpriteMask` cut from the socket's own dark interior
  (`build_map_prototype.socket_mask`), so it comes from inside the skull. AE's eye
  smoke is one 999 s particle dissolved by a custom erosion shader, a permanent
  streak on stock shaders, so it is rebuilt as faint red wisps (same texture and
  colour) that appear only once they rise out. One eye: the second dark hole
  is the nostril (user correction).
* **Bats** (Enter sky): AQW Flash `mon/Bat2` (skeletal, red eyes), its "Walk" flap
  loop (sprite 54, frames 17-29) baked from vectors by `bake_flipbook.py` into a
  4x4 sheet; occasional groups of 1-3 cross in the Sky parallax layer, behind
  the castle.
* **Torch light**: a flickering warm glow (Fire Mage `Glow1`) behind each flame.
* **Lanterns** (Enter): flicker glow on the two wall lamps, riding the
  DistantCastle parallax.
* **Ash + cinders**: grey ash drifting down across both rooms (lighter in
  Garden1); cinders rising from the Enter rubble.
* **Banners** (Garden1): all 7 Shadowscythe banners are split into their own
  layers and sway +/-1.4 degrees from their top edge (3.6 s, phased), still on
  the CastleWall parallax speed.
* Deliberately not done: a green window glow (clashes with the red palette).

Validation: 104 effect checks (16 kinds: live, fit, upright, socket mask,
sway bindings, bundle bindings), 22 art comparisons (worst mean 0.396/255),
902 Animator checks; 46 particle systems and the socket mask present on both
platforms. Previews `revisions/v8/unity_build/*-effects*.png`.

* Windows SHA-256: `e9a34a7d6fc8986b53e2f4b4e9bccff077cd1d3bcedeab2dfb9bebfd24d25723`; CRC `2202065946`.
* Android SHA-256: `4b75ca897575f058452c5391cf1f30119ac1059aadae6c2a32fca2b5954efb5c`; CRC `875256300`.
* Backup: `/opt/infinity/backups/shadowtest-v8-20261104T151731Z`. Game PID preserved.

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
$E='outputs/map_converter_trial/shadowfall/effect_sources'
python scripts/swf2unity/bake_flipbook.py "$E/Bat2.swf" 54 17 29 "$E/BatFlap.png" --zoom 0.6
python scripts/swf2unity/map_visuals.py outputs/map_converter_trial/shadowfall outputs/map_converter_trial/shadowfall/revisions/v8/visuals --layout scripts/swf2unity/map_configs/shadowfall-layout-v8.json --reuse outputs/map_converter_trial/shadowfall/revisions/v4/visuals --raster-scale 3
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v8 --project outputs/map_converter_trial/shadowfall/UnityProject --visuals outputs/map_converter_trial/shadowfall/revisions/v8/visuals --calibration scripts/swf2unity/map_configs/shadowfall-calibration-v4.json --gpu-compression --effects-bundle "$E/46573_skeletalfiremage_v10.unity3d" --effect-source "dracolich=$E/78866_dracolich_bone_v2.unity3d" --bat-sheet "$E/BatFlap.png" --build
```

`Bat2.swf` is `aqw_rig_dataset/monsters/mon_Bat2_3fe0b59532/original.swf` from the
XAMPP preservation dataset (SHA-256 `3fe0b595…`).

## Version 7 published: torch-sized flames

November 4. In-client, the user found v6's flames "a liiiil too big": torches,
not bonfires. v6 fit only height (1.15x the Flash flame), but the monster's
fire is far wider than the thin Flash flame. v7 fits both axes of the main
flame's own particles (soft glow excluded): at most 0.6x the Flash flame's
height and 2.2x its width, whichever binds. Entrance flames are now ~1.9 x 2.5
units, Garden1 ~3 x 4. Everything else is v6 (visuals copied unchanged).

* Windows SHA-256: `72bbe1a6977882a441dcc1eb5a7aac383a35b379d76e7ea317b9b08a286582e1`; CRC `326660873`.
* Android SHA-256: `bf0eedd72e2be0ceecb37b9a2ad232caf5c761b0a5681c5f292f0efe474701fb`; CRC `893794832`.
* Backup: `/opt/infinity/backups/shadowtest-v7-20261104T144208Z`. Game PID preserved.

## Version 6 published: baked gameplay art removed, native fire and smoke

November 4. The user asked to drop art that a native Infinity map doesn't bake
in, and to upgrade the old Flash torch flames (suggesting the fire-mage
monster's), plus smoke rolling across the crashed-dragon courtyard.

Removed by exact root placement (`remove` in
`map_configs/shadowfall-layout-v6.json`), never by symbol: char 367 (the
skeleton guard) is reused by skeleton NPCs in other rooms.

* Enter: NSoD (598), Doomy (604), the armored guard + its "!" popup (498/500),
  and the Flash "Set Background" UI button (642).
* Garden1: both skeleton spear guards (752/850) and the "!" popup (1102), the
  six collectible dogs (878-1058) and their three blue markers (1096-1100).
  The gold ground arrows are base art and stay.
* NPCs belong to the server's map NPC placements; none are configured for
  `shadowtest` yet.

Fire (`replace.fire`): the 7 Flash flames (char 1373) become the Skeletal Fire
Mage's `Animation/FX_Fire Med_wGlow/ps_fire med` effect (live AE bundle
`npcs/46573_skeletalfiremage` v10, SHA-256 `199431e6…dbe762`): flame flipbook,
glow, embers and fireball core. Particle settings are copied verbatim
(`EditorUtility.CopySerialized`); materials are rebuilt on the same stock
`Mobile/Particles/*` shaders with the bundle's textures (extracted by
`build_map_prototype.py --effects-bundle`). The URP sprite-shader core maps to
`Mobile/Particles/Alpha Blended` (identical unlit). Each fire keeps the
orientation its parents give it in the monster, is turned so the plume rises
straight up (all 7 within 7 degrees), and is scaled to 1.15x the height of the
flame it replaces. Static bands now break at replaced depths, so each fire
paints between the art below it and its own brazier, which Flash draws over the
flame's base.

Smoke (`smoke: true`): a new looping emitter with AE's `simpleSmoke2` texture.
Soft puffs drift left to right at ~1.1 units/s across the floor band (60 px
above the highest spawn pad to the stage bottom), prewarmed, faded in and out,
on BGFront above the ground art and behind players and depth props. This is
new presentation, not a source behaviour.

The isolated project now enables `com.unity.modules.particlesystem`; without
it, bundles silently drop every ParticleSystem (the first v6 build had 0).

Validation: 18 Python tests; 22 art comparisons with effects excluded (worst
mean 0.396/255, p99 9); 902 Animator checks; 7 depth checks; 62 effect checks
(fire count, live, upright, fitted size; smoke live; all 30 particle systems
keep material, shader and texture when loaded from the bundle); Android carries
all 30, and ASTC pages hold to the source (worst mean 0.261). GPU atlas memory
is 18.4 MiB on PC. Previews: `revisions/v6/unity_build/*-effects-unity.png`.
Not yet observed in the installed client or on an Android device.

* Windows SHA-256: `25d9bf47182c256e333c564155051ae2bddee3ac0aea6aa9cc71b84b36450b7a`; CRC `1382493032`.
* Android SHA-256: `eebb39e438f2d40b0f79d4f0803d42dc3b540db9fb0aba5a43301235b12a99bc`; CRC `49459675`.
* Backup: `/opt/infinity/backups/shadowtest-v6-20261104T143414Z`. Game PID preserved.

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
python scripts/swf2unity/map_visuals.py outputs/map_converter_trial/shadowfall outputs/map_converter_trial/shadowfall/revisions/v6/visuals --layout scripts/swf2unity/map_configs/shadowfall-layout-v6.json --reuse outputs/map_converter_trial/shadowfall/revisions/v4/visuals --raster-scale 3
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v6 --project outputs/map_converter_trial/shadowfall/UnityProject --visuals outputs/map_converter_trial/shadowfall/revisions/v6/visuals --calibration scripts/swf2unity/map_configs/shadowfall-calibration-v4.json --gpu-compression --effects-bundle outputs/map_converter_trial/shadowfall/effect_sources/46573_skeletalfiremage_v10.unity3d --build
python scripts/swf2unity/verify_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v6
```

The effect source bundle is the live one at
`content.130-162-189-229.sslip.io/game/assetbundles/windows/npcs/46573_skeletalfiremage/10/`.

## Historical version 5: sparse tiles and GPU compression

November 3. After v4 the user called its 160 MiB of uncompressed atlas memory
inefficient. Measurement: only 34% of the v4 atlas held visible art; static
layers (98% of pixels) were stored as whole rectangles, mostly transparent.

Same pixels, stored differently (no re-rasterization: `--repack` crops v4's
verified frames back out and re-stores them; a fresh export uses the same
`pack_pages`):

* Static layers become 130 px payload tiles in 132 px cells. Each cell keeps a
  one-texel border of the *real* neighbouring pixels, so bilinear sampling at a
  tile edge reads what the whole image would; tiles meet without seams. Empty
  tiles are dropped and identical tiles stored once (1,168 refs -> 1,070 tiles).
  Each tile is an ordinary SpriteRenderer; no custom shader.
* Every cell/slot is a multiple of 12 texels (lcm of BC7 4x4 and ASTC 6x6), so no
  compression block mixes two images. Pages are block-rounded, not power-of-two
  (no mipmaps, fixed zoom): 4096 x 4020 + 4096 x 1116.
* `--gpu-compression`: BC7 on Standalone, ASTC 6x6 on Android, quality 100.

| | v4 | v5 |
|---|---|---|
| GPU memory, PC | 160 MiB (RGBA32) | **20.1 MiB** (BC7) |
| GPU memory, Android | 160 MiB | **8.9 MiB** (ASTC 6x6) |
| Bundle, Windows / Android | 5.25 / 5.26 MB | 3.32 / 2.22 MB |

Validation: repack lossless for all 378 frames, with exact borders and
transparent gutters; 17 Python tests; 22 source comparisons (worst mean
0.437/255, p99 10/255); tile-edge error equals interior error (Enter 0.88 vs 0.79
mean max-channel), i.e. no seams; 1,155 Animator, 12 depth, NPC-over-dragon,
parallax and camera checks. Platform check now holds each platform's decoded
page to the source atlas (Windows BC7 worst mean 0.195, Android ASTC 0.323)
instead of requiring identical bytes across formats. Android device rendering
remains unverified.

* Windows SHA-256: `d697629a37e89f8c650e422c38cf9e1fbbbfaa0ee46b5725ce1fea629a958761`; CRC `61218360`.
* Android SHA-256: `e4801c2c20e359b76bae0685fc70040fbb2106a66f085d1d8cf4ee5d627966da`; CRC `3271946664`.
* Backup: `/opt/infinity/backups/shadowtest-v5-20261103T170205Z`. Game PID preserved.

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
python scripts/swf2unity/map_visuals.py outputs/map_converter_trial/shadowfall/revisions/v4/visuals outputs/map_converter_trial/shadowfall/revisions/v5/visuals --repack
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v5 --project outputs/map_converter_trial/shadowfall/UnityProject --visuals outputs/map_converter_trial/shadowfall/revisions/v5/visuals --calibration scripts/swf2unity/map_configs/shadowfall-calibration-v4.json --gpu-compression --build
python scripts/swf2unity/verify_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v5
```

(`--android-only` / `--android` for the Android build and check; publish with
`shadowtest_update_host.py ... --revision 5`.)

## Historical version 4: vector density and NPC ordering

Published November 3. Density math: 10 px/unit world, camera ortho size 16
shows 320 source px vertically, so 1080p needs 3.375x; 3x leaves 1.125x bilinear
magnification (was 3.375x at 1x). The 48 px `clip_to_stage` overscan only trims
off-stage Ground (speed 0) and the DistantCastle top (speed 8, worst-case
reveal ~27 px). Windows SHA-256 `13f9f579…`, CRC `3175276437`; Android
`b3fe7064…`, CRC `641746224`; backup `shadowtest-v4-20261103T154535Z`.

November 3: the user confirmed live parallax in version 3, but showed the dragon
structure covering the NPCs, visibly magnified texture pixels, and an undersized
player. Version 4 corrects these separate causes. The entire dragon assembly was
incorrectly treated as one Y-sorted foreground prop; its full bounds are not a
valid shared ground-contact pivot. Its original source painter placement is
behind the NPCs. It now uses a fixed BGFront group; NPC bodies and associated
quest popups use native Default-layer ZOffset/SortingGroup actors at body feet.

Artwork is re-rasterized directly from the vector definitions at **3x** density,
with bilinear filtering. Sprite pixels-per-unit increases by the same factor,
preserving world geometry and source layout. Atlas pages retain full density
within 4096 texture dimensions rather than silently downscaling a larger atlas.
CPU-readable texture copies are disabled. Geometry, camera zoom 16 and parallax
settings remain unchanged. Player scales increase 30 percent from version 3:
Enter 0.65, Garden1 0.845; entitySpeed is divided by 1.3, preserving the rounded
native walking speeds 14/21. Installed-client feel remains a separate check.

The loaded-bundle render probe now checks the NPCs against the actual dragon
art: fully opaque NPC pixels overlapping opaque dragon pixels must remain
visible and retain their colors. It requires nontrivial overlap coverage. The
generic front/behind marker checks remain separate. Original painter-order
comparisons run at 2880 x 1482 against independent source captures, rather than
comparing an enlarged old texture to itself. Versioned settings are in
`scripts/swf2unity/map_configs/shadowfall-layout-v4.json` and
`shadowfall-calibration-v4.json`. Export uses `--raster-scale 3` and must not
reuse a lower-density atlas.

## Historical version 3: scale, camera and scenery depth correction

The user confirmed version-2 animation in the installed client, but reported
that the enlarged avatar was the wrong adjustment and that scenery layers were
not apparent. They clarified that they wanted both walk-behind depth and camera
parallax. Version **3** is now published under `/join shadowtest`; restart the
client before joining to release the already loaded version-2 map asset.

Character scales return to the source/initial-prototype values: Enter **0.5**,
Garden1 **0.65**. World geometry now uses **10 pixels per unit**, making each
room **96 x 49.4 world units**, with native camera zoom **16**. The camera has
about **34 world units** of horizontal travel at the source capture aspect ratio.
Speed is independent of the avatar correction: source room speed multiplied by
four, yielding Enter **28** and Garden1 **32** (native scaled walk speeds 14/21).
These settings require installed-client acceptance, not an assertion of exact
Flash movement units or screenshot-equivalent character proportions.

The source artwork audit identified separate sky/moon and distant castle at the
entrance, architecture and banners in the courtyard, and original tree, rock,
horn and skull-pile subtrees. The export now retains their logical groups:
Enter has 20 visual layers; Garden1 has 176. Eight scenery groups bind to native
`ZOffset` plus `SortingGroup`. Native `BGParallax` keeps Ground first at speed 0;
sky uses 20 and distant architecture uses 8. These are new Infinity presentation
settings applied to original art, not authored Flash parallax values. Geometry,
spawns, blockers and exits retain source coordinates under the uniform scale.

Native behavior was read from the installed `MapPrefab`, `MapCell`, `ZOffset`,
`ZOffsetUpdater`, `BGParallax`, `ParallaxLayer` and `CameraFollow` classes. The
BGFront sorting-layer ID `1256558175` was read from the installed TagManager.
Local serialization stubs do not execute the installed game's MonoBehaviours.
Separate local camera and depth probes use the inspected algorithms and actual
Unity rendering, while serialized readback checks native component bindings.

Validation passed: 15 Python tests; 22 source-painter-order artwork comparisons
(worst mean 0.1508/255, p99 4/255); 1,155 loaded-Animator frame checks; eight
front/behind scenery comparisons; fixed-ground and relative-parallax checks;
camera travel in both rooms; source polygon/spawn/exit readback; and three
point-probe round trips. Artwork-reference captures temporarily flatten sorting
groups to compare original painter order. Separate depth probes render a
synthetic magenta stand-in before and behind each prop; these do not prove that
an avatar can stand at the exact diagnostic coordinates in the real client.
Camera-left/right and depth diagnostic PNGs are under `revisions/v3/unity_build/`.

Windows/Android geometry, native bindings, animation curves and decoded atlas
pixels matched. The 373-image atlas remains 2048 x 4096 RGBA (32 MiB). Public
bundle downloads, SHA-256 values, version/CRC API fields, cached-map version
normalization and service health passed after guarded deployment. The API
restarted; game PID `1139973` was preserved. Installed-client version-3 visual
depth, camera/parallax, movement feel and scale remain unobserved. NPC/quest
behavior, conditional visibility, audio and the other 14 rooms remain unfinished.

* Windows SHA-256: `2d6750db7ed94f54d3f102fc8576ebd4585cdafdd01e6bb8ccf84ecdfbc0ae67`; CRC `705511530`.
* Android SHA-256: `af2936415b974b979f3a28d21e8f9502d20177e2d537d1ee483a91e03de6885b`; CRC `784221737`.
* Backup: `/opt/infinity/backups/shadowtest-v3-20261103T040214Z`.
* Local receipts: `outputs/map_converter_trial/shadowfall/revisions/v3/live_shadowtest/`.
* Repeatable source-scoped settings: `scripts/swf2unity/map_configs/shadowfall-layout.json`
  and `shadowfall-calibration.json`. Layout anchors use the original subtree's
  ground/shadow bounds and require live traversal review around each prop.

Reproduce the current export and build (stage `map_source.json` in the revision
directory first):

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
python scripts/swf2unity/map_visuals.py outputs/map_converter_trial/shadowfall outputs/map_converter_trial/shadowfall/revisions/v3/visuals --layout scripts/swf2unity/map_configs/shadowfall-layout.json --reuse outputs/map_converter_trial/shadowfall/revisions/v2/visuals
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v3 --project outputs/map_converter_trial/shadowfall/UnityProject --visuals outputs/map_converter_trial/shadowfall/revisions/v3/visuals --calibration scripts/swf2unity/map_configs/shadowfall-calibration.json --build
python scripts/swf2unity/verify_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v3
```

For a fresh export without a prior atlas, omit `--reuse`; the same source
animation subtrees are rasterized again. Android build uses `--android-only`
in place of `--build`, followed by verification with `--android`. Deployment
helpers take `--revision 3`; stale live preflight snapshots are rejected.

## Historical version 2

The user confirmed the version-1 route and requested continuing with scale,
movement and animation. Revision-2 artifacts are isolated under
`outputs/map_converter_trial/shadowfall/revisions/v2/`; version-1 receipts remain
unchanged. `map_visuals.py` splits authored painter order into static bands and
independent animated subtrees. It preserves 24 fps and separate 11-, 86-, 87-
and 280-frame clocks, including stopped parents with live children. Enter has
14 layers (8 animated); Garden1 has 152 (97 animated). Repeated frames reduce
to 343 images in one 2048 x 4096 RGBA atlas, 32 MiB uncompressed. Crops retain
the original stage pixel grid; Unity clip duration is explicitly set to each
source period rather than its automatically rounded length.

Independent whole-room source captures at 11 frames per room are the rendering
reference. Native Animator controllers are checked separately from clip-sampled
captures by advancing an instance loaded from the built bundle. Geometry and
three return trips retain their independent validation gates.

Calibration uses inspected native GreenguardWest/R2 as a baseline: 46.0117 world
units across, 20.86426 source pixels per unit, entrance entity scale 0.8 and
speed 14. Garden1 retains the source's relative scale/speed differences at 1.04
and 16. This is a reference calibration requiring installed-client acceptance.

Quest/NPC interaction, state-dependent visibility, collectible behavior, audio,
player-relative foreground occlusion and the other 14 rooms remain uncompiled.
Rendered NPCs and quest markers currently show authored preview states.

Version **2** was published on November 2 local time (November 3 UTC). Use
`/join shadowtest` after restarting the client to load fresh map assets.
All 22 rendered source comparisons passed: worst mean error 0.1111/255, p99
3/255. The bundle-loaded Animator test passed 1,155 frame checks, geometry
readback passed, the point probe completed three round trips, and all 14 Python
regression tests passed. Windows and Android geometry/bindings, five animation
clips and decoded atlas pixels matched. Android device rendering is unverified.

The guarded live dry run, backup/apply and public hash/API verification passed.
Both public bundles return version 2. Existing cached version-1 area metadata
normalizes to version 2 through the unchanged server serializer. The API was
restarted; game PID `1139973` was preserved. Live version-2 visual playback,
movement feel and character scale still require installed-client observation.

* Windows SHA-256: `10cc04c717dc69f7aed791045154e832e41776a6f68775f3a4bdb2a3a64d737e`; CRC `1180659892`.
* Android SHA-256: `8e551938dbb510125d2b33bd422be4480e1b12ef2f52b4b2535a8cb33314d51e`; CRC `382323598`.
* Backup: `/opt/infinity/backups/shadowtest-v2-20261103T033041Z`.
* Receipts: `revisions/v2/live_shadowtest/` beneath the generated Shadowfall output.
* Publication helpers: `shadowtest_update_host.py` and `shadowtest_update_live.py`.

Reproduce export and build:

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
python scripts/swf2unity/map_visuals.py outputs/map_converter_trial/shadowfall outputs/map_converter_trial/shadowfall/revisions/v2/visuals
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v2 --project outputs/map_converter_trial/shadowfall/UnityProject --visuals outputs/map_converter_trial/shadowfall/revisions/v2/visuals --calibration outputs/map_converter_trial/shadowfall/revisions/v2/calibration.json --build
python scripts/swf2unity/verify_map_prototype.py outputs/map_converter_trial/shadowfall/revisions/v2
```

## Initial version-1 live test deployment

On November 2, the user explicitly requested publishing the current prototype
under the separate map name **shadowtest**. It is now registered on the live
server; use `/join shadowtest`. This publication does not mean the unfinished
animation or gameplay conversion has been completed.

* New map ID and bundle ID: `2100000`; version `1`; Town map type.
* Prefab: `map-shadowfall`; cells: `Enter` and `Garden1`.
* Logical bundle filename: `maps/custom/2100000_map-shadowtest.unity3d`.
* Windows SHA-256: `46391928fdeffcf3660c1076ba81cc8dcf60bcd619f5fd5a18433087eecafdf4`; CRC `3143941814`.
* Android SHA-256: `82df2e797a895ceacb57831364c393ae296ed97f9d272a3b36bd84cc9379f60a`; CRC `3581101769`.
* Live backup: `/opt/infinity/backups/shadowtest-20261103T000821Z` (UTC filename).

The guarded dry run and apply passed. Both public platform downloads matched
their hashes; the public bundle API returned the registered filenames, version
and CRCs. The server map service resolved the entrance and both directions of
the prototype cell route. Windows and Android serialized geometry/script
bindings matched. Android rendering on a device is unverified.

The API restarted to load the added registry entry. The game service remained
active with the same PID (`1065129`); no existing map, item, inventory, client mod,
or server source file was replaced. The user subsequently confirmed installed-client
joining, walking, moving into the other room and returning. They reported slow
walking and incorrect map scale. This confirms the two-room live route for version
1; animation, full-map behavior and precise scale remain separate work.

Deployment payload, before snapshot, dry-run, apply and verification receipts
are under `outputs/map_converter_trial/shadowfall/live_shadowtest/`. The map seed
is `data/maps/shadowtest.json`. Deployment scripts are `shadowtest_host.py` and
`shadowtest_live.py` under `scripts/swf2unity/`.

The registry backup is historical evidence, not a file to overwrite later
catalog changes with. Any future rollback must remove only this map/bundle ID
and its new version files with current-state guards.

## Result and proof boundary

A local Unity prototype and AssetBundle contain the source **Enter** and
**Garden1** rooms, their source walking polygons, 14 blocker polygons, six spawn
pads, and the two unconditional exits joining those rooms. An independent
UnityPy readback checked the serialized bundle against the extracted source
coordinates. There were no serialized read failures.

Unity's Physics2D checks exercised a point navigation probe from spawn to exit
and back, for **three round trips**. Spawn positions were on free ground, blocker
centers rejected the probe, and destination pads resolved after each transition.
These checks run through the local prototype adapter, not the installed game
client or its network/server movement handlers. They do not prove avatar
footprint collision or production gameplay.

The displayed artwork is deliberately a **still source snapshot** for each room.
It is a structural prototype, not a completed animated map conversion. Its
separate live test publication was explicitly requested above. Animation, separate background/foreground layers around
players, NPC/dialogue/shop interactions, quest/level gates, audio, player-state
visibility, movement calibration, and actual client traversal remain unfinished.
The native component classes in the isolated project are serialization stubs;
successful loading in that project does not prove installed-client compatibility.

Both Unity captures passed comparison with the extracted stills over the same
opaque black background used by the capture camera. Mean error was below
0.00024/255 in each room, and p99 error was zero. This comparison establishes
snapshot delivery/rendering only, not Flash animation or conditional visibility.
The original source stills contain translucent pixels; comparing straight RGBA
against an opaque camera capture would be an invalid comparison.

## Source preserved

Selected preserved source: `maps/shadowfall/town-shadowfall-23aug24.swf`.

* Source SHA-256: `6a2f4ae43ac1c718d897719b3e419e65f64165444066981f6defda3470b08570`
* Source size: 561,348 bytes; 24 fps; stage 960 x 494 pixels.
* Original location: the local `XAMPP/aqw_full_dataset_phase2` preservation dataset.
* Work copy: `outputs/map_converter_trial/shadowfall/source.swf`.

This is a preserved version, not a claim about today's public AQW source.
No network capture was needed for it, and the original dataset was not changed.

The extractor inventories **16 gameplay room labels, 32 named spawn pads, 32
exits, 93 root-level solid regions, and 16 walking areas**, plus NPC/monster
inspector records and 132 decompiled script files. Every internal cell/pad
destination resolved in that source graph. External map destinations and
script-driven alternate behaviors are not covered by that check.

Collision shapes come from the original button hit states and named `shadow`
children. SVG fill contours are transformed with the full nested affine SWF
matrices, including rotation and reflection. Curved or multi-contour shapes are
held rather than replaced with rectangles. All inspected geometry for this
source fit the supported straight-contour subset.

Initial script-hidden symbols are hidden in the snapshot, including nested
collision helpers. Conditional visibility remains uncompiled: for example,
level-dependent NPCs and quest-dependent entrance objects are not validated by
these renders. No arbitrary ActionScript is executed by the extractor.

## Pilot route

```text
Enter / Spawn
  -> source exit __id0_
Garden1 / Bottom
  -> source exit __id13_
Enter / Right
```

Garden1's other exits, including level-gated routes, are inventoried but excluded
from this two-room prototype. Source cell boundaries remain intact.

## Files to inspect

All generated files are local and gitignored under
`outputs/map_converter_trial/shadowfall/`:

* `map_source.json`: room graph, geometry, matrices, inspector literals, holds.
* `snapshots/Enter/snapshot.png`, `snapshots/Garden1/snapshot.png`: extracted stills.
* `UnityProject/`: isolated project; does not use the shared item-conversion project.
* `UnityProject/Assets/Generated/ShadowfallPrototype.unity`: local scene.
* `unity_build/Enter-unity.png`, `unity_build/Garden1-unity.png`: Unity renders.
* `unity_build/map-shadowfall.prototype.unity3d`: local Windows prototype bundle.
* `unity_build/unity_validation.json`: point-probe physics and transition checks.
* `unity_build/serialized_audit.json`: independent serialized bundle inspection.
* `unity_build/render_validation.json`: snapshot rendering comparison.
* `unity_build/prototype_verification.json`: source/bundle hashes and final checks.

Open the isolated Unity project, open its generated scene, and enter Play mode
to inspect the local probe. Click walking ground or use arrows/WASD. The yellow
marker is a test probe, not a converted player character. The default camera
shows the full source stage; wide-map camera following is not exercised here.

## Reproduce

From the InfinityServer repository root:

```powershell
$env:SWF2UNITY_CONFLATION_SCALE='4'
python scripts/swf2unity/map_source.py <preserved-shadowfall.swf> outputs/map_converter_trial/shadowfall --snapshot Enter,Garden1
python scripts/swf2unity/build_map_prototype.py outputs/map_converter_trial/shadowfall --project outputs/map_converter_trial/shadowfall/UnityProject --build
python scripts/swf2unity/verify_map_prototype.py outputs/map_converter_trial/shadowfall
python -m pytest scripts/swf2unity/test_map_source.py -q
```

Requires the existing JPEXS/Java tooling, Unity 6000.3.18f1, UnityPy, numpy,
Pillow and pytest. Unity licensing required running the build outside the
sandbox in this session. The extraction/render regression suite passed **10
tests**, including real button hit-state selection, rejection of dynamic
inspector expressions, negative pivots, unsupported holes/curves, and rejection
of blank Unity captures.

The first render attempt caught an empty multi-sprite import. The builder now
requires a non-null single sprite and a rendered image comparison. A C# build
or successful physics check cannot pass that rendering gate on its own.

The initial prototype build changed no production data. The later explicitly
requested `shadowtest` deployment added the map/bundle records and content files
described above. No client mod, installed game, shared Unity project, or player
inventory was changed.

## Next conversion work

Retain this geometry/graph foundation while replacing the snapshot art with
separate visual layers and faithful animated subtrees. Audit the reachable
scripts and implement explicit player-state behavior before connecting the
remaining rooms or testing the bundle in the real client. Use original artwork
and animation data; snapshots are not an acceptable final replacement.
