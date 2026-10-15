# Armor skin color and larger conversion batches

Follow-up: the user confirmed the batch generally looks good but reported white body skin on Captain and translucent back limbs on ChronoStriker. The previous repair fixed the face template; the body gradient and silhouette repairs are documented in [FLASH_CONVERSION_BODY_LIMBS_AND_BATCH04.md](FLASH_CONVERSION_BODY_LIMBS_AND_BATCH04.md).

The user confirmed that both batch02 items look good, then reported that converted armors do not carry skin color. The pet follows and the previous preview animation fix also have user confirmation.

## Cause and repair

The shared Cryo template's native Head sprite is opaque black and has no skin mask. The converter reused it for every armor, so the native compositor had no skin channel to recolor on the face. This was a template omission, independent of the working armor animation hook.

The replacement face kit comes from the captured native Warrior bundle (`capture/harvest/bundles/15775_Warrior.unity3d`). Its male and female Head sprites have native skin masks. The Cryo bone tree, body art and animation inputs remain the basis of each existing trial. Face pixels, masks, sprite rectangles and pivots are replaced in separate conversion directories; prior evidence is retained. Cryo's older constant image rectangles are expanded into the equivalent keyed rectangle fields required by the current builder.

`prepare_face_template.py <skeleton-template> <face-template> <output>` reproduces this merge and records the face image hashes. Both inputs are exported with `armor_template.py`.

`armor_template.py` now resolves each head mask from the exact ItemSlots texture pointer. Matching a texture by name could select another gender or prefab variant's identically named mask. `armor.py`, the builder's loaded-bundle checks, and the independent batch audit reject armor heads without skin masks.

Validation includes dark, light and blue skin responses in both genders with unchanged alpha, independent serialized mask checks, and isolated GPU head renders in addition to existing animation checks. The color-response previews use compositor emulation; native gradient shading and live customization still need client observation. The loader already uses the native compositor and receives `ArmorColor.Skin`; no new loader build is needed.

The GPU validation now isolates a head from its child eye renderers. Continuous animation curves are sampled at their exact key time; the small positive offset is reserved for renderers with stepped image changes.

Repair artifacts: `outputs/flash_converter_trial/skin_fix/`. The existing trial IDs are **2000002** and **2000005**. Versioned publication retains the old bundles and inventory instances, backs up catalog and database state, updates only bundle metadata, and restarts only the API.

Both repairs are now published as **version 2**. Backup: `/opt/infinity/backups/armor-skin-fix-20261012T133450Z`. Public bundle hashes, API versions/CRCs, and next-login inventory versions passed. Inventory was unchanged; the game retained PID 542558. Cryo passed 224 GPU captures (worst mean error 0.714/255); ChronoStriker passed 126 (0.226/255), including both genders' heads. Actual skin customization after relog remains the client proof boundary.

## Batch03

The next batch contains three armors and three swords selected from captured records with no Unity bundle. Sources, manifests, conversion reports, Unity validation and live publication evidence are kept under `outputs/flash_converter_trial/batch03/`.

- Chronomancer Pirate Captain
- Golden DragonLord Naval Commander
- Gold Coronation Dragonlord
- Ascended Phoenix Blade of Nulgath
- Hollow Soul Blade
- Golden Techno Blade

The reusable batch tools accept a named batch, require 2–12 unique source records, check source hashes and catalog drift, publish in one transaction, and grant trials unequipped to redux. Armor templates must include skin masks. Unresolved rig notes or missing render checks prevent publication.

The Hollowborn Phoenix Blade was held because nested loop periods exceed the bounded converter path (including a 40,681,078,200-frame joint period). Hollowborn DragonBlade was held because animated filters/additive container blending would be dropped. Their sources remain captured and no live trials were created for them. `survey_weapon_candidates.py` records source checks before expensive rendering; the batch builder now performs this screening for weapons itself. Completed validated builds can be reused with `--resume` after source hash checks and another independent audit.

Static swords now receive GPU captures too. Source-comparison publication gates require actual reference samples, worst sampled mean error at most 25/255, and clipped alpha at most 0.5 in the report's units; stricter GPU-versus-emulation gates remain 2/255 mean and 24/255 at the 99th percentile. These source comparisons are sampled and approximate, not a claim of exact Flash playback.

## Published batch03

All six trials are live and granted once, unequipped, to redux. Inventory increased from 202 to 208 rows, preserving existing items and equipment. Source records and prior catalog entries were preserved. Backup: `/opt/infinity/backups/conversion-batch03-20261012T134805Z`. Both services are active and the game PID remained 542558.

| Trial | Item/bundle ID | Download bytes |
|---|---:|---:|
| Chronomancer Pirate Captain | 2000007 | 281433 |
| Golden DragonLord Naval Commander | 2000008 | 285370 |
| Gold Coronation Dragonlord | 2000009 | 261218 |
| Ascended Phoenix Blade of Nulgath | 2000010 | 161703 |
| Hollow Soul Blade | 2000011 | 41817 |
| Golden Techno Blade | 2000012 | 31262 |

Independent audits, 96 GPU captures, public binary hashes, API versions/CRCs, next-login inventory wire, and original-record readback passed. Ascended Phoenix preserves periods 151 and 906; the other five sources have static body/weapon art in the supported path. Body source comparisons sampled both genders and found no clipped alpha; worst mean error was 16.6/255 on a skin region of Chronomancer Pirate Captain. The native face kit uses the client's skin-gradient compositor, whose live result remains a separate client observation.

Restart the client to load the repaired version-2 armors and new batch. Check several skin colors in both genders, character previews and gameplay, then equip the six new trials and check Ascended Phoenix's animation in combat. No loader update was required. The new items and the skin repair still need user-observed client confirmation.
