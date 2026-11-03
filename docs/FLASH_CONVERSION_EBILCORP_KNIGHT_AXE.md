# Axe of the EbilCorp Knight conversion

Published on the existing item 908930, bundle 8930, version 3. Original prefab name
`ECBlackknightAxe1a-5Sep18_weaponSlots` and asset filename
`items/axes/8930_ECBlackknightAxe1a-5Sep18.unity3d` are preserved. Other item fields
and existing inventory were preserved during publication. Public file, API bundle
metadata and normalized item wire passed verification. Services remained active;
the game PID was unchanged.

## Source behavior

The exact Flash source is `items/axes/ECBlackknightAxe1a-5Sep18.swf`, SHA256
`7b76ab2a07b6add1b330ea0ec5ddd34ffa3c02d1217befb24d388d39b3df2ce9`.
Its initial callback uses
`gotoAndStop(Math.round(Math.random() * (this.totalFrames - 1) + 1));`.
There are eight parent appearances, chosen once per new equipped/preview instance.
The source's rounded distribution gives the two endpoint appearances half the
weight of each interior appearance. The converter preserves this distribution;
it does not invent a click-to-cycle action. Re-equipping rolls again and may
repeat the same appearance.

Animated descendants retain an exact 80-frame visual period at 24 fps. Dense
particle tracks are rasterized into eight selector layers while retaining source
filters, painter order and the registration point. Images are deduplicated.
Because this source has no color customization, no redundant RGB coefficient
atlas is bundled. The 8192x5944 BC7 atlas keeps the existing per-effect resolution;
no additional aggregate downsampling was required.

## Validation

All 128 source comparisons passed, including late loop boundaries (worst mean
11.45/255; no clipped alpha). All 128 activated GPU comparisons passed (worst mean
0.0586/255, worst p99 0.6873/255). The independent audit
passed: 7,282,270 file bytes, 48,693,248 texture bytes, no dependencies,
and only the native NoFlashWhenHit component.
Bundle SHA256: `4d1e2052dbe06140d460cb453ab94d5cec84eae4e79e57392d7d5174bcb2e1a2`. CRC: 2018078067.

The exact runtime component was tested on 32 native-style equipped instances;
all eight appearances were observed. A deterministic 7,000-sample check preserves
the source distribution. Effects keep animating while the chosen parent stays
stopped; refresh/reattachment retains the appearance and shared material. A
standalone item preview also receives its random selector. Existing Dracomancer
click controls, return cycles, palette retention and UI input routing passed
regression checks. Loader build passed with 0 warnings and 0 errors.

The loader uses the existing FlashMorph selector protocol, now supporting a
bounded random-initial program. It attaches after native weapon load and preview
ShowGameObject. The item bundle contains no new runtime MonoBehaviour.
Loader SHA256: `9a42a2fe94060a0dceae7e73a80180873912e134a94ddf241b8105fd3d77f0d2`. Public DLL/hash and installed DLL match.
The Euler preview/spinner repair source is unchanged. Full client restart is
required. Real-player equip, appearance and preview behavior remain awaiting
observation; editor/runtime and publication proofs are separate.

## Redux grant and backups

Added existing item 908930 unequipped to Redux (account/character 8), inventory row
10003056. Inventory grew from 225 to
226; prior entries were preserved and committed readback passed.

- Native replacement: `/opt/infinity/backups/native12-20261013T173824Z`
- Grant: `/opt/infinity/backups/native12-redux-grant-20261013T173831Z`
- Loader: `/opt/infinity/backups/flash-rig-preview-loader-20261013T173720Z`
- Local loader: `C:\Program Files (x86)\Steam\steamapps\common\AdventureQuest Worlds Unity Playtest\InfinityServer-backup-preview-20261013T173810Z`

Evidence: `outputs/flash_converter_trial/ebilcorp_knight_axe/` and
`outputs/flash_converter_trial/native_replacements/native12/`.
