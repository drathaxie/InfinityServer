# Redux inventory conversion

The owned-inventory audit checks distinct catalog item IDs across Redux's inventory against the actual published bundle version, CRC, and file SHA-256. It exports public item metadata only; private inventory rows and session credentials stay on the server.

Audit after native34 on 2026-10-14: 1,227 distinct owned item records, of which 1,042 use verified converted assets, 175 visual items remain, six Caladbolg records are protected, and four consumable/nonvisual records have no asset to convert.

Native34 published sixteen native bundles covering eighteen original item records. The nineteen earlier helm visibility repairs shipped separately as native31. Both batches passed independent bundle audits and public/API/item-wire readback. Restart the client to obtain their new versions. Player gameplay/rendering is a separate proof boundary.

The remaining work includes weapons and helms, both armor genders, pets, capes, furniture, houses, and ground items. Missing native bundle assignments and missing house source files require additional work; a finished builder queue does not mean full inventory coverage.

`scripts/swf2unity/run_redux_inventory_batches.py --start 35` resumes the finite queue at native35. It preserves earlier status snapshots, skips batches with successful live verification, shares the Unity worker lock, and stops before new rendering if free disk space falls below 12 GiB. Each publication uses guarded remote backups, version/CRC updates, public readback, and an idempotent grant phase. Caladbolg remains excluded.

Armor conversion now supports an explicit six-color reconstruction mode without forcing a morph head. Default and dark/light skin palettes are compared against source renders, including selected late loop boundaries. The source script gate audits the body symbols and their reachable descendants; unused exported symbols are recorded separately. A negative control confirms that including a procedural cape in that graph still fails the gate.

Known infrastructure failures in native32, native33, and native35 are retryable and are not evidence of unsupported source art. Per-item logs and held-candidate reports distinguish those failures from actual compiler cases.

Native fixtures for cape and furniture contracts were fetched from their exact official live versions. The cape fixture exposes `CapeGO` with direct `cape` and `cape_walk` children; native loading installs a state machine over those children. The furniture fixture has a separate house-item root with a nested weapon node. These categories need those contracts before publication.

Current evidence lives under `outputs/flash_converter_trial/redux_inventory/`. `audit.json` and `remaining.json` contain the coverage snapshot; `worker/status.json` reports queue progress. Publication manifests and rollback paths are under `outputs/flash_converter_trial/native_replacements/nativeXX/`.
