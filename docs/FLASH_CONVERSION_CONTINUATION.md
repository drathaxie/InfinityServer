# Flash conversion continuation — October 12, 2026

Recovered Claude's latest session and the user's instruction to keep the converted armor animated in character previews. The user confirmed Caladbolg already worked; its bundles and deployment were left unchanged.

## Animated armor previews

The native `CustomizableCharacter.setupArmor` uses `ApplyCCMaterial` when `SkipComposite` is true. That assigns a static mask material over the converted front parts' FlashRig material. Back parts without a mask keep animating, which caused inconsistent previews. Adding neutral masks to the back parts would freeze those too; that source edit was removed.

`mod/InfinityLoader/FlashRigPreviewColors.cs` intercepts this path only for loaded armor slots that use `SwordShine/FlashRig`. It sends every ItemSlots region through the existing native texture compositor, assigns the colored atlas to the existing renderers, and keeps their materials and Animators. Recolor requests coalesce while one compositor job is pending. Superseded jobs discard their outputs; replacing an armor or closing a preview releases generated textures and sprites after pending work completes. Submission also works while a preview is hidden waiting for its setup callback. Native armor and normal gameplay retain their original paths.

- Release build against the installed client: passed, zero warnings/errors.
- `dotnet run --project mod/FlashRigPreviewChecks -c Release --no-restore`: 25 checks passed. These use native-contract doubles for selection, asynchronous completion, recoloring, and resource lifetime; they do not prove rendered client behavior.
- Published loader SHA-256: `f780b509dd8c39b5edfeb71e636d42e1ccff5c333b00c5b51e01a4ea41e66fe9`.
- Updated `data/mod` and the installed Steam client's `InfinityLoader.dll`, using backups and atomic replacement. The installed build takes effect on the next client start.
- Live backup: `/opt/infinity/backups/flash-rig-preview-loader-20261012T123329Z`.
- Local installed-client backup: `C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest/InfinityServer-backup-preview-20261012T123535Z`.

The Cryo Chronomancer armor bundle was not rebuilt. Its preview behavior is handled by the loader compatibility fix.

## Flying Moglin pet trial

Live source item **323**, `items/pets/moglinwings.swf`, had no Unity bundle. Its source hash is retained in `outputs/flash_converter_trial/pet_candidates/source_manifest.json`. JPEXS script export found only the root `PetMoglinwings` class, with Ready/Idle/Walk stop and loop control. Every root display state, including placement identity, is identical. The visual child runs independently.

`scripts/swf2unity/pet.py` currently supports this bounded case: an invariant root display list with both Idle and Walk labels. It rejects distinct root states and unresolved rig notes. The native follower receives an independent root Animator with Idle/Walk states; a child Animator drives the artwork's three independent periods of 10, 300, and 490 frames. Changing movement state keeps those visual clocks running.

- New item and bundle: **2000004**, **Flying Moglin Flash Trial**, prefab `FlyingMoglin_FlashTrial`.
- Granted one copy to **redux**, unequipped. Original pet 323 and existing inventory were preserved.
- Download: **224,869 bytes**. Serialized texture storage: **765,952 bytes** (one BC7 atlas).
- Unity 6000.3.18f1 build and reloaded bundle checks passed, including individual period poses and 12 GPU captures up to the 14,700-frame joint loop boundary.
- GPU versus shader emulation: worst sampled mean channel error **0.137/255**.
- Native follower state contract: 12 Idle/Walk switches passed without changing the nested visual clocks.
- Eleven sampled source-reference frames over 491 source ticks had mean channel error **5.91/255**. Rasterization and resampling remain approximate; this is not a pixel-identical claim.
- Live backup: `/opt/infinity/backups/flying-moglin-flash-trial-20261012T123326Z`.
- Public content/API, loader download hashes, next-login inventory wire, original pet data, and service health verified. The game PID was unchanged.

Artifacts and detailed evidence are under `outputs/flash_converter_trial/FlyingMoglin/`. The public bundle is `npcs/flying_moglin_flash_trial.unity3d`.

## Client confirmation — October 12

The user confirmed that Flying Moglin works and follows, and that the armor animations are fixed. These outcomes now have user-observed client confirmation. The next requested step is a small batch of additional conversion trials.

## Further client checks

Restart the installed client. Check Cryo Chronomancer in character select and customization: front and back shards should keep moving, and Base/Trim/Accessory color changes should recolor them without stopping animation. Test both genders and repeated preview opening/closing.

On redux, equip Flying Moglin Flash Trial and check scale, orientation, follow movement, map changes, hide/show, and repeated equip/unequip. These behaviors need rendered client observation; the serialized and native follower contract checks do not replace it.

Deployment tools are guarded and one-time. Do not rerun `trial_host.py apply` for an already granted item. Its preflight checks reject occupied IDs and existing trial records; future trials need their own reviewed payloads.
