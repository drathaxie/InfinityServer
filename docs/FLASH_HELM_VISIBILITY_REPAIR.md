# Converted helm visibility repair

The installed character loader requests `HelmGO` by name. The nineteen previously published converted helm bundles exposed only `ArmorSlots`. Preview code could resolve that prefab through item metadata, while the equipped-character path could not load it.

The repair adds a `HelmGO` container alias pointing to the same serialized prefab. Existing `ArmorSlots` lookup remains available. The repair changes AssetBundle container metadata only: every other serialized object's bytes were compared before and after and remained identical.

All nineteen repairs shipped in native31. Rollback snapshot: `/opt/infinity/backups/native31-20261014T141814Z`. Original item IDs, source records, inventory, and equipment were preserved. No client DLL change or game-service restart was required.

Validation loaded each repaired bundle in Unity through `LoadAsset<GameObject>("HelmGO")`, replayed the installed client's attachment sequence with test-owned bones, and checked that the reparented helm still renders and animates after the data root is disabled. An untouched original bundle failed the same lookup as a negative control. Source fidelity, GPU comparisons, independent serialized audits, public bundle hashes, metadata, and item-wire readbacks passed. Worst repaired GPU mean error was 1.123/255 against the unchanged 2/255 limit.

New helm builds export both prefab names. The builder and deployment gate require attachment contract revision 2. Native34's newly converted helms passed this gate as well.

Restart the game client to request the published versions. These are bundle-loading and attachment-contract results; a fresh authenticated player equip/render after restart remains a separate check.

Evidence: `outputs/flash_converter_trial/helm_visibility_repair/` contains the decompiled loader, serialized preservation report, negative control, and Unity validation. `outputs/flash_converter_trial/native_replacements/native31/` contains deployment, backup, and public verification manifests. Reproduce the alias repair with `scripts/swf2unity/repair_helm_aliases.py`.
