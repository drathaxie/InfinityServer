# Native Flash-to-Unity asset replacements

Verified live: **989 native bundles**, covering **1066 existing item records**. Largest published batch: **256 bundles**.

| Batch | Native bundles | Existing item records | Bytes |
|---|---:|---:|---:|
| native01 | 1 | 2 | 5,999,490 |
| native02 | 32 | 47 | 2,885,861 |
| native03 | 63 | 63 | 8,901,209 |
| native04 | 128 | 129 | 9,852,603 |
| native05 | 15 | 17 | 6,885,627 |
| native06 | 256 | 258 | 22,541,284 |
| native07 | 7 | 11 | 4,631,287 |
| native08 | 195 | 203 | 20,892,977 |
| native09 | 54 | 54 | 18,835,065 |
| native10 | 1 | 4 | 5,369,596 |
| native11 | 42 | 42 | 16,829,647 |
| native12 | 1 | 1 | 7,282,270 |
| native13 | 17 | 18 | 3,227,355 |
| native14 | 35 | 38 | 5,524,314 |
| native15 | 49 | 58 | 10,162,162 |
| native16 | 22 | 25 | 17,931,053 |
| native17 | 6 | 6 | 284,022 |
| native18 | 1 | 1 | 149,859 |
| native19 | 32 | 36 | 18,886,001 |
| native31 | 19 | 19 | 2,638,400 |
| native34 | 16 | 18 | 10,106,998 |
| native37 | 5 | 14 | 3,739,860 |
| native38 | 15 | 16 | 5,030,050 |
| native39 | 3 | 5 | 3,401,796 |
| native40 | 4 | 9 | 3,967,004 |
| native41 | 4 | 8 | 6,276,476 |
| native60 | 2 | 5 | 1,098,876 |

These are replacements on the original bundle IDs, item IDs, prefab names, and logical Unity filenames. Each publication increments the published version and CRC; previous version files and metadata remain rollback points. No new trial items are created. A separate idempotent grant phase adds missing published items to Redux without changing existing inventory rows or equipment. All non-bundle item fields and unrelated catalog entries are guarded and preserved. Caladbolg is excluded.

Every published bundle passed Flash-source comparison, loaded-bundle Animator/render checks, and an independent serialized AssetBundle audit. Additional sword batches hold sources with color-custom scripts, procedural animation, unsupported masks/filters, long capped clocks, or excessive flipbook workloads. Numeric source limits remain mean <= 25/255 and clipped alpha <= 0.5; GPU limits remain mean <= 2/255 and p99 <= 24/255. The spear retains its separately documented color reconstruction and three static glow approximations.

Supplemental source checks for 22 earlier animated swords: **passed**. `late_boundary_audit.json` records source comparisons at each individual animation period boundary and the second loop boundary. From native04 onward, these late source frames are part of the build gate.

Native05 updates 15 earlier flipbook bundles (17 existing item records) with the short-loop closing-step guard. The old serialized J6 Cutlass reproduced a wrong pose at frame 10; the corrected bundle passed at that frame and around its fifth loop. The guard moves only the closing step slightly early while retaining the exact clip duration; existing armor curves are unchanged. An intentional validation exception followed by successful captures proves that one failed item no longer contaminates later comparisons. These updates are counted once in the live totals and item roster.

The native version serializer now caches the registry by its file revision, so the running game can see subsequent asset version updates. The fix passed a regression test and was activated with service and TCP listener checks. Two early startup checks rolled back before activation; logs established that game seeding takes about 14 seconds, and the successful activation allowed 40 seconds for readiness. Both API and game services were healthy afterward. Asset publications after activation preserve the game PID.

Publication manifests, raw before snapshots, backups, independent audits, and public hash/API/item-wire readbacks are under `outputs/flash_converter_trial/native_replacements/nativeXX/`. Each `deployment_result.json` records its server rollback directory. `summary.json` separates verified publication from unfinished preparation.

Backup catalogs are historical snapshots. After later batches have published, a rollback must restore only the affected IDs with current-state guards.

[Published item roster](../outputs/flash_converter_trial/native_replacements/published_items.csv) lists the original item IDs, names, and current bundle versions for testing.

Real player equip, hand alignment, visual appearance, and combat behavior remain unverified for the bulk replacements. Restart the client to fetch the new versions. Native spear bundle 71508 is version 2 and serves both original items 98957 and 99045; its previous trial item remains available separately.

[Mobile spear GIF](https://content.130-162-189-229.sslip.io/previews/gigante-force-spear-6abcae431876.gif)

Backups:

- `/opt/infinity/backups/native01-20261012T182036Z`
- `/opt/infinity/backups/native02-20261012T182924Z`
- `/opt/infinity/backups/native03-20261012T185000Z`
- `/opt/infinity/backups/native04-20261012T192804Z`
- `/opt/infinity/backups/native05-20261012T193305Z`
- `/opt/infinity/backups/native06-20261013T003838Z`
- `/opt/infinity/backups/native07-20261013T004229Z`
- `/opt/infinity/backups/native08-20261013T015420Z`
- `/opt/infinity/backups/native09-20261013T083217Z`
- `/opt/infinity/backups/native10-20261013T062456Z`
- `/opt/infinity/backups/native11-20261013T150758Z`
- `/opt/infinity/backups/native12-20261013T173824Z`
- `/opt/infinity/backups/native13-20261013T201815Z`
- `/opt/infinity/backups/native14-20261013T215901Z`
- `/opt/infinity/backups/native15-20261013T230843Z`
- `/opt/infinity/backups/native16-20261014T014419Z`
- `/opt/infinity/backups/native17-20261014T041344Z`
- `/opt/infinity/backups/native18-20261014T062608Z`
- `/opt/infinity/backups/native19-20261014T125311Z`
- `/opt/infinity/backups/native31-20261014T141814Z`
- `/opt/infinity/backups/native34-20261014T153115Z`
- `/opt/infinity/backups/native37-20261014T175123Z`
- `/opt/infinity/backups/native38-20261014T180256Z`
- `/opt/infinity/backups/native39-20261014T181648Z`
- `/opt/infinity/backups/native40-20261014T183058Z`
- `/opt/infinity/backups/native41-20261014T184521Z`
- `/opt/infinity/backups/native60-20261015T121812Z`
