# Gigante Force Spear trial, 2026-10-12

The previous batch04 finished with 12 published trials. The user's next priority was the spear; DragonGate Fighter Combo remains queued because its complete shop/source export is unavailable.

## Current native publication

After the trial, the user authorized replacing the original native assets. Native bundle **71508** is now **version 2**, using the validated spear bytes under `items/polearms/71508_Dmnk26FiendSpearBlack.unity3d`. Both original items **98957** and **99045** use the replacement. Their item IDs, prefab names, and non-bundle fields were preserved; this promotion created no inventory grant. The separate trial item remains available.

Native publication, public byte hashes, API CRC/version, item wire, and service checks passed. Rollback directory: `/opt/infinity/backups/native01-20261012T182036Z`. The catalog cache fix was activated before native publication; the game PID became 579492 and subsequent native publications preserved it. See [native replacement report](FLASH_NATIVE_ASSET_REPLACEMENTS.md).

[Mobile spear GIF](https://content.130-162-189-229.sslip.io/previews/gigante-force-spear-6abcae431876.gif) is an animated preview rendered from the conversion, not a player capture.

## Earlier trial publication

- Item and bundle ID: **2000025**, **Gigante Force Spear Flash Trial**.
- Granted once, unequipped, to redux (character/account 8); inventory 220 -> 221. Ownership row 10003023.
- Prefab: `Dmnk26FiendSpearBlack_WeaponGO`; native Polearm contract ItemType 20 / EquipSpot 2.
- Bundle: `giganteforcespear_batch05_flash_trial.unity3d`, 5,999,490 bytes, CRC 1582661494.
- Bundle SHA256: `71817686ef41402c7d7913b9052686e0c2901ee60ee1cdd7a2a4fdaca4c2c3d9`.
- Original records 98957 and 99045 and native bundle catalog 71508 were read back unchanged. All previous catalog entries and inventory rows were preserved. Caladbolg was not changed.
- Deployment backup: `/opt/infinity/backups/conversion-batch05-20261012T180101Z`.
- Both services remain active; game PID 542558 was preserved.

## Conversion and validation

Source: `items/polearms/Dmnk26FiendSpearBlack.swf`, 35,004 bytes, SHA256 `3c5e5d5912eb0bb730ab082ea0bacf4b27804d36f609560473992f02a367f628`.

The expanded conversion retains authored nested animation clocks without a period cap. It has 78 tracks, a 4096 x 1608 atlas, BC7 textures, and 32,931,888 texture bytes. The longest individual period is 14,445 frames at 24 fps. Four mutually exclusive branches were compacted with a visibility/key-equivalence proof, preserving their visible poses and order. Authored knockout filters no longer duplicate the underlying children.

Expanded weapon inverse UV transforms can contain offsets over 100,000. Their pose validator compares image corners in destination pixels, with a 0.1-pixel limit, instead of treating magnified inverse UV offsets as visible displacement. Color/opacity/rect checks retain their 0.0003 scalar limit. A plateau of one percent of a frame on each side of authored keys absorbs Unity normalized-clock float roundoff; integer poses and exact loop durations are preserved, with linear motion between the plateaus. Existing armor curves keep their prior behavior.

Evidence in `outputs/flash_converter_trial/dragongate/GiganteForceSpear`:

- `conversion/validation.json`: five full-resolution Flash samples at frames 0, 119, 320, 7559, 14444, oversampling 4; worst mean channel error 15.75/255; zero clipped alpha.
- `conversion/color_validation.json`: two alternate Eye/Accessory palettes against Flash references at frames 0 and 119; passed.
- `conversion/track_compaction_validation.json`: all 12 corresponding branch columns passed gating and selected-key preservation checks (114 -> 78 tracks).
- `bundle/bundle_contract.json`: 14,467 loaded-bundle Animator pose checks (all integer frames 0..14445 plus 21 selected GPU frames); maximum recorded affine/color/rect pose errors zero.
- `bundle/unity_validation_summary.json`: 21 GPU captures including period boundaries and frame 28890; worst mean error 0.153/255, with unchanged limits mean <= 2 and p99 <= 24.
- `bundle/independent_audit.json`: serialized prefab, native script, dependencies, texture memory, manifest CRC, and hashes passed. The only runtime MonoScript is native `NoFlashWhenHit`; no ItemSlots required.
- `palette_validation/validation_summary.json`: two per-instance palette captures, worst mean 0.098/255 and p99 2.20/255; shared material preserved, second instance isolated, animation clocks preserved, and palette survived the next Animator update.
- `final_integrity_verification.json`: both original spear rows, all older bundle catalog entries, and public loader hash verified after publication.

The source validation predates the track compaction; the compaction proof establishes equivalence at the authored integer frames. Unity captures validate the compacted bundle separately. Three authored glow layers still use static glow approximations. Color reconstruction is approximate; these checks do not establish pixel-identical Flash rendering across every color and joint animation state.

## Loader and client test

`FlashRigWeaponColors` supplies Eye and Accessory colors through per-instance MaterialPropertyBlocks after native `LoadWeapon` and `RefreshColors`. It only touches `SwordShine/FlashRig` materials opting into `_CCDirectPalette`. It preserves animated materials and clocks. The loader compiled against the installed client's actual Managed assemblies with zero warnings/errors.

Published, repo-staged, and locally installed loader SHA256:
`0ddee71f91ef4a4b6a084a16055a601a67a67f2d509b6f289cdcb0f9246af227`.

Prior loader SHA256:
`f780b509dd8c39b5edfeb71e636d42e1ccff5c333b00c5b51e01a4ea41e66fe9`.

Loader backups:

- Server: `/opt/infinity/backups/flash-rig-preview-loader-20261012T180145Z`.
- Repository: `outputs/flash_converter_trial/dragongate/GiganteForceSpear/loader_backup_before`.
- Installed client: `C:\Program Files (x86)\Steam\steamapps\common\AdventureQuest Worlds Unity Playtest\InfinityServer-backup-preview-20261012T180153Z`.

Public bundle delivery, API CRC/version, and next-login inventory wire passed (`outputs/flash_converter_trial/batch05/live_verification.json`). The running client retains its already-loaded DLL: fully close and reopen the playtest, log into redux, equip **Gigante Force Spear Flash Trial**, observe its animation, and change Eye/Accessory colors. Real player equip, sizing/hand alignment, and visual acceptance remain unverified until that client test.
