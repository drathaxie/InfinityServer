# Mixed specialty conversions

As of October 14, 2026, native34 completed with cumulative verified publication of **958 distinct bundles / 1,014 existing item records**. These totals count verified replacements, not candidates or queued builds, and exclude the additive battlepet pilot. The largest completed batch contains 256 bundles. Native31 repaired nineteen previously counted helms; see [helm visibility repair](FLASH_HELM_VISIBILITY_REPAIR.md).

The current priority is [Redux's owned inventory](REDUX_INVENTORY_CONVERSIONS.md), with a finite worker resumed at native35. `outputs/flash_converter_trial/redux_inventory/worker/status.json` is authoritative for current progress. Earlier worker PIDs below are historical. Native20 remains unpublished because of incompatible cross-category aliases.

| Batch | Published bundles | Existing item records | Rollback directory |
|---|---:|---:|---|
| native13 | 17 | 18 | `/opt/infinity/backups/native13-20261013T201815Z` |
| native14 | 35 | 38 | `/opt/infinity/backups/native14-20261013T215901Z` |
| native15 | 49 | 58 | `/opt/infinity/backups/native15-20261013T230843Z` |
| native16 | 22 | 25 | `/opt/infinity/backups/native16-20261014T014419Z` |
| native17 | 6 | 6 | `/opt/infinity/backups/native17-20261014T041344Z` |
| native18 | 1 | 1 | `/opt/infinity/backups/native18-20261014T062608Z` |
| native19 | 32 | 36 | `/opt/infinity/backups/native19-20261014T125311Z` |
| native31 | 19 repairs | 19 repairs | `/opt/infinity/backups/native31-20261014T141814Z` |
| native34 | 16 | 18 | `/opt/infinity/backups/native34-20261014T153115Z` |

Examples include the three beach/boardwalk morphs, DragonGate boomboxes, Patch of Emptiness, Unreal ShadowReaper of Doom, Draconian Flame Adept Morph, and mixed weapon categories. Full discovery now includes 35,628 source-backed original item records in 14 categories: Helm, Dagger, Mace, Staff, Bow, Gun, Gauntlet, Axe, Polearm, Sword, Whip, Wand, Rifle, HandGun. These are records, not unique bundles or conversion successes. The broader read-only inventory includes 55,563 source-backed records / 52,191 native bundle IDs across additional categories; source presence does not establish support.

Every published entry passed source comparison, loaded-bundle Animator/GPU comparison, serialized bundle audit, refreshed identity/hash preflight, guarded publication, and public asset/API/item-wire readback. The respective source/GPU limits remain 25 and 2. The independent helmet audit additionally checks direct `helm`, `fronthair`, and `backhair` children, native slot IDs, empty hair artwork for these complete morphs, and animation on the reparented helm child. A static renderer's absent layer list is now handled correctly. Source and GPU checks do not establish actual player head/hand placement or appearance; those still need client observation.

Publication preserved inventory, non-bundle item fields, unrelated catalog entries, and the running game PID. Both services remained active. Caladbolg remains excluded. Mixed weapon aliases are accepted only with one shared prefab/source identity and a native category owner; a native-only mirror must point to the exact bundle filename with empty Flash linkage. Captured source-owner fields are bound again at publication preflight.

## New cases

| Source | Finding | Current state |
|---|---|---|
| Twig of Sprites, bundle 78912 | Expanded nested animation first exceeds 13,260 frames; a read-only higher-limit probe reaches another 244,188-frame combined track. | Not published. Requires independent ancestor/child clock composition instead of an enormous flattened joint loop. Exact source hash and probe are recorded in `native13/independent_clock_frontier.json`. |
| Polearm bundle 78870 | At frame 2323, Unity sampled alpha 0.9996338 instead of source 1, exceeding the unchanged 0.0003 scalar gate. | Fixed with the existing one-percent-frame plateau, retaining integer poses and duration. Passed loaded-bundle validation and published in native14. |
| Patch of Emptiness 78868 | Original face stack blurred its noise, source error 33.32/255. | Exact uncapped 180-frame whole-source flipbook: source error 0.00; published native15. |
| Neon DragonGate Boombox 78875 | Flattening nested blend containers changed the backdrop, including color variants. | Recurse into those containers and retain non-normal blend draws: 56 layers, source worst 24.47 default / 17.66 dark / 24.40 bright; passed GPU and published native15. This remains approximate reconstruction. |
| Lost Spirit Orbs 77407 | Joint period 8736 produced over 17,000 PNGs; eager decoding exceeded 11.2 GB RAM. | Lazy PNG decoding and two-pass frame dedup/downsampling reduced measured peak to 1.34 GB. Source worst 12.44 without clipping; published native16. |
| Mine Raider's Blaster 78280 | Constructor calls a setter and front/back hand scripts toggle different artwork. | Requires compiling the constructor plus separate main and `Back_` prefabs. The native avatar already loads the second prefab for gauntlets/daggers; converter support remains unfinished. |
| Livyatan's Billow Harpoon / Cold and Dire Delusions Sidearms | Register attack-frame callbacks, then return to Idle. | Need native combat-event routing. They are not published as frozen idle artwork. |
| Transforming Berzerker Bunny Spear 1540 | Catalog linkage is absent from source exports. | Root-wrapper/linkage case remains unresolved; failure now reports actual exported symbols. |

Faithful effect stacks use a bounded atlas and cumulative image downsampling that retains geometry. Whole-source flipbooks require an exact uncapped loop. Frame deduplication includes color-response constants and slopes. Shader branch hints help some dense builds, but compiler timeouts still hold several bow/whip/helm sources; errors are not ignored. No loader update was required for these batches.

## Continuation

The prior workers stopped at the 12 GiB free-space guard. The local RuneScape dedicated-server installation was removed with user authorization; its 107 remaining state/config/log files were copied and hash verified first under `outputs/battlepet_forge_lab/live/runescape-server-state-backup/`. The friend's world state and separate game/project were preserved. An additional 1,514,006,279 bytes of generated Unity copies for 897 verified published assets were removed; all source SWFs, conversion JSON, comparison evidence, bundles and publication records remain. Input manifests are backed up alongside `published_unity_cache_cleanup.json`.

Worker `specialty_resume19` was launched as PID 20120, queuing native19–native30 at requested size 32. Native19 seeds 30 previously source-validated shader failures and adds two new candidates. Its editor scripts compiled successfully; source/GPU/audit gates still determine publication. `specialty_resume19/status.json` and logs are authoritative for current progress.

Redux owns all 960 published converted item records through native18: 957 missing items were added unequipped, without changing existing inventory/equipment. Separate grant backup is `/opt/infinity/backups/redux-converted-grant-20261014T124435Z/`; Redux's testing capacity is 4096 bag/bank slots, with other accounts unchanged. `grant_converted_redux_host.py` rechecks native identities and grants only missing audited items. Pipeline phase `grant` runs after public verification and summary refresh; its backup and result are separate from the asset deployment. Native19's already-loaded process predates this phase; the next cumulative grant includes it.

Native18's log identifies a FlashRig shader compiler timeout, followed by cached compiler failures for other entries. Unity launches now use a bounded 20-minute compiler deadline; BuildMany explicitly reimports a previously failed shader once before retrying. No shader artwork or validation limits changed. `seed_shader_retry_batch.py` reuses source-validated native18 conversions in native19 and writes fresh bundle outputs for a full native audit. The follow-up queue starts at native19, with the same disk guard and one-worker lock. Inspect its current status instead of trusting historical PIDs.

### Historical queues

`scripts/swf2unity/continue_specialty_batches.py` is preparing native16 at requested size 64. A deferred follow-on worker queues native17 through native36, each at requested size 64, waiting for verified native16 before acquiring the shared Unity lock. Both stop on batch failure; each new batch requires at least 12 GiB free disk. Unknown source behavior is recorded for compiler work and other candidates continue through the unchanged gates.

The current source worker was verified as PID 7724 and deferred worker as PID 64460. Process IDs are historical evidence, not a guarantee of current activity: inspect `outputs/flash_converter_trial/native_replacements/specialty_worker/` and `specialty_followon/`, their status/process JSON, and batch logs when resuming. Do not run a second Unity build in the shared warm project concurrently. The 20-batch follow-on queue is bounded and has a 48-hour predecessor wait.

Evidence: `outputs/flash_converter_trial/native_replacements/native13/` through `native16/`, the published item roster, `edge_repairs_20261013/`, full discovery JSON/logs, and worker directories above. Battlepet source and Forge experiments are recorded separately in `docs/BATTLEPET_FORGE_LAB.md`.
