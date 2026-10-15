# Conversion test batch 02 — October 12, 2026

The user confirmed Flying Moglin works and follows and the armor preview animations are fixed, then requested a small batch of further items. This batch adds two independent trials to redux's inventory.

| Trial | Item / bundle ID | Source item | Download | Texture storage |
| --- | --- | --- | --- | --- |
| ChronoStriker Flash Trial | 2000005 | 44489 | 769,764 bytes | 4,194,304 bytes |
| Dark Matter DragonBlade Flash Trial | 2000006 | 74419 | 121,957 bytes | 827,392 bytes |

Both live source records lacked Unity bundles at capture and preflight. The original records remain unchanged; each trial has a separate ID and one unequipped inventory grant to redux. Existing catalog entries and inventory were preserved. No loader update was needed for this batch.

## Coverage and validation

ChronoStriker includes male and female prefabs, with animated hands, shoulders, thighs, and shins. Its hand effect combines 100-, 200-, and 1,100-frame periods. `armor.py --max-period 1200` preserves the longest loop without the default 960-frame cap. The converter's default budget is unchanged; the new option accepts bounded explicit budgets from 1 to 10,000 frames. Both source pairs decomposed without unresolved rig notes at this budget.

The armor's reloaded-bundle Animator poses passed individual-period checks; 176 GPU captures passed comparison with compositor/shader emulation, including samples at the 1,100- and 2,200-frame boundaries. Worst sampled mean channel error was 0.226/255. Source comparisons cover 12 initial frames per part/gender, with no source-alpha clipping. They are approximate raster comparisons: animated parts range up to 15.28/255 mean visible-pixel error; they do not establish pixel-identical Flash rendering.

Dark Matter DragonBlade uses three layers and retains the effect's 214-frame loop. Its individual-period pose checks and ten GPU captures passed, including loop boundaries. Worst sampled mean GPU-versus-emulation error was 0.064/255. Thirty-six source-reference frames averaged 1.72/255 visible-pixel error, with no source-alpha clipping.

Independent UnityPy audits confirmed the prefab names, native Assembly-CSharp script bindings, slot components, NoFlashWhenHit protection, texture storage, and CRCs against the bundle manifests. Neither bundle has external dependencies.

Candidate Enchanted Chronomancer was held during selection because its animation appeared in the head, which the current armor converter replaces with the native face kit. Chronobound and Chrono Vanguard were also inspected; their identified animation was in the head. Their captured sources remain as evidence, and they were not published.

## Deployment and readback

The reviewed payload and both bundles were staged, and the live deployment dry run passed. Both new records, bundle registrations, grants, and audit entries were applied in one database transaction with one short API interruption. The game process stayed at PID 542558. Inventory grew from 200 to 202 rows, preserving all previous entries and equipment.

Backup: `/opt/infinity/backups/conversion-batch02-20261012T130424Z`.

Public HTTP delivery and SHA-256, API CRC/version/prefab metadata, next-login inventory wire, original source records, and active API/game services all passed verification.

- Armor SHA-256: `54348b36a1db8cc8d707a2b04bf0137660a4dc8a7397f32275b06bbe206a9113`.
- Sword SHA-256: `e6c4b6b82d52c728bc4ea23748eee5dd9f1878c91743b17e43b5568a0fc85e12`.
- Inventory grant rows: 10003000 and 10003001.

Artifacts are under `outputs/flash_converter_trial/batch02/`, including the source manifests, build logs, source comparisons, GPU captures, independent audits, dry run, deployment result, and live verification.

`scripts/swf2unity/batch_trials.py` coordinates this specific two-item batch. Its build mode uses the same persistent Unity project for the sequential builds. Its publication modes prepare a drift-guarded payload, stage/dry-run, apply once, and verify. An existing application result or occupied trial identities prevent duplicate publication. `deploy_batch.py` performs the transactional server operation and file rollback on pre-commit failure.

## Player checks remaining

Relog on redux and equip both trials. Check ChronoStriker's effects in both genders, in character previews, and during combat; check the sword's effect loop, grip, layer order, and repeated equip/unequip. These two new items still need rendered client observation. Earlier confirmations for Cryo Chronomancer and Flying Moglin do not substitute for checking the new assets.
